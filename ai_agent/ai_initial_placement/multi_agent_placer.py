"""
ai_agent/ai_initial_placement/multi_agent_placer.py
====================================================
Autonomous Multi-Agent Initial Placement Pipeline  —  Multi-Row Edition
=======================================================================

ARCHITECTURE
------------
This module uses a 5-stage pipeline to produce placement-ready, DRC-clean
layouts for *any* analog circuit topology:

  Stage 1  Topology Analyst   — Pure-Python netlist analysis + LLM circuit ID
  Stage 2  Row Floor-Planner  — LLM decides HOW MANY rows, which functional
                                 block lives in each row, and PMOS/NMOS split
  Stage 3  Placement Spec.    — LLM assigns devices to rows and orders them
                                 within each row (slot-based, no coordinates)
  Stage 4  Geometry Engine    — Deterministic math converts ordering → x/y
  Stage 5  DRC Healing        — Deterministic abutment-pack + overlap guard

KEY IMPROVEMENT over the previous version
------------------------------------------
* **Multi-row support**: PMOS and NMOS can each span multiple rows.
  The LLM decides the number of rows based on circuit topology
  (e.g. telescopic OTA uses 3 NMOS rows + 2 PMOS rows).
* **No hard-coded 2-row assumption**: The geometry engine places as many
  rows as the LLM requests.
* **PMOS/NMOS separation is mathematically guaranteed**: NMOS rows start
  at y=0 and grow upward; PMOS rows start above all NMOS rows.
* **Topology-guided routing**: The Topology Analyst identifies diff pairs,
  current mirrors, cascodes, and bias chains so the LLM can apply
  interdigitation and common-centroid automatically.

OUTPUT FORMAT FROM THE LLM
---------------------------
```json
{
  "nmos_rows": [
    {"label": "tail_current",  "devices": ["MM3"]},
    {"label": "input_pair",    "devices": ["MM1", "MM2", "MM2", "MM1"]},
    {"label": "nmos_cascode",  "devices": ["MM5", "MM6"]}
  ],
  "pmos_rows": [
    {"label": "pmos_cascode",  "devices": ["MM7", "MM8"]},
    {"label": "load_mirror",   "devices": ["MM9", "MM10"]}
  ]
}
```
NMOS rows: y = 0, 0.668, 1.336, … (one per row, rows grow upward)
PMOS rows: y = N_nmos×0.668, (N_nmos+1)×0.668, … (above ALL nmos rows)

Author: AI-Based-Analog-Layout-Automation team
"""

import os
import json
import copy
import re
import math
import sys
from collections import defaultdict
from typing import Optional


def _print(*args, **kwargs):
    """Encoding-safe print for Windows consoles (charmap codec)."""
    kwargs.setdefault("flush", True)
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        text = " ".join(str(a) for a in args)
        enc = sys.stdout.encoding or "utf-8"
        print(text.encode(enc, errors="replace").decode(enc, errors="replace"), **kwargs)

from ai_agent.ai_initial_placement.placer_utils import (
    sanitize_json,
    _build_net_adjacency,
    _build_device_inventory,
    _build_block_info,
    _normalise_coords,
    _restore_coords,
    _format_abutment_candidates,
    _force_abutment_spacing,
    _convert_slots_to_geometry,   # fallback for legacy 2-row schema
    _heal_abutment_positions,
    compress_graph_for_prompt,
)
from ai_agent.ai_initial_placement.finger_grouper import (
    group_fingers, expand_groups,
    detect_matching_groups, build_matching_section,
    _enrich_matching_info,
)

# -------------------------------------------------------------------
# Physical layout constants
# -------------------------------------------------------------------
ROW_PITCH    = 0.668   # µm — default row-to-row pitch (overridden dynamically)
ABUT_SPACING = 0.070   # µm  — abutted finger pitch
STD_PITCH    = 0.294   # µm  — non-abutted standard pitch
MAX_RETRIES  = 3
MAX_ROW_DEVS = 16      # max devices per row (auto-split if exceeded)

# -----------------------------------------------------------------------
# Per-stage model mapping — each pipeline stage gets its optimal model.
# Format:  { provider: { stage_key: (model_name, location_override|None) } }
# location_override=None means use the env VERTEX_LOCATION / default.
# -----------------------------------------------------------------------
_VERTEX_MODELS = {
    "TopologyAnalyst":  ("gemini-2.5-flash", None),       # fast analysis
    "Placement":        ("gemini-2.5-pro",   "global"),   # best reasoning
    "DRC":              ("gemini-2.5-flash", None),       # fast fixes
    "default":          ("gemini-2.5-flash", None),
}
_GEMINI_MODELS = {
    "TopologyAnalyst":  "gemini-2.5-flash",
    "Placement":        "gemini-2.5-pro",
    "DRC":              "gemini-2.5-flash",
    "default":          "gemini-2.5-flash",
}
_ALIBABA_MODELS = {
    "TopologyAnalyst":  "qwen-plus",
    "Placement":        "qwen-max",
    "DRC":              "qwen-plus",
    "default":          "qwen-plus",
}


def _device_width(node: dict) -> float:
    """
    Compute the physical width of a device from its geometry or electrical params.

    Priority:
      1. geometry.width  (if present and > 0)
      2. Computed from electrical: nf * STD_PITCH (finger-aware)
      3. Fallback: STD_PITCH

    The value is used for device-to-device spacing when not abutted.
    """
    geo = node.get("geometry", {})
    w = geo.get("width", 0)
    if w and float(w) > 0:
        return float(w)
    elec = node.get("electrical", {})
    nf = max(1, int(elec.get("nf", 1)))
    return round(nf * STD_PITCH, 6)

_POWER_NETS = frozenset({"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS"})


###############################################################################
# 0.  LLM dispatcher
###############################################################################

def _call_llm(prompt: str, selected_model: str,
               task_weight: str = "light",
               stage: str = "") -> str:
    """
    Call the appropriate LLM and return the raw response text.

    Each pipeline stage gets its own optimal model:
      - TopologyAnalyst  → fast/cheap (flash / qwen-plus)
      - Placement         → best reasoning (pro / qwen-max)
      - DRC               → fast/cheap (flash / qwen-plus)

    For VertexGemini, the location is automatically adjusted per model
    (gemini-2.5-pro requires location="global").

    Falls back to "" on any error so the caller can use a deterministic
    fallback rather than crashing the whole pipeline.
    """
    # Determine the stage key for model lookup
    stage_key = "default"
    if "Topology" in stage or "Analyst" in stage:
        stage_key = "TopologyAnalyst"
    elif "Placement" in stage:
        stage_key = "Placement"
    elif "DRC" in stage:
        stage_key = "DRC"

    tag = f"[MultiAgent][{stage}]" if stage else "[MultiAgent]"
    try:
        if selected_model == "Gemini":
            from google import genai
            from google.genai import types as gtypes
            api_key = os.getenv("GEMINI_API_KEY", "")
            if not api_key:
                raise ValueError("GEMINI_API_KEY not set")
            client = genai.Client(api_key=api_key)
            model_name = _GEMINI_MODELS.get(stage_key, _GEMINI_MODELS["default"])
            _print(f"[MultiAgent] Gemini → {stage_key}: model='{model_name}'")
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=gtypes.GenerateContentConfig(max_output_tokens=65536),
            )
            return (resp.text or "").strip()

        elif selected_model == "Alibaba":
            from openai import OpenAI as AliOpenAI
            client = AliOpenAI(
                api_key=os.getenv("ALIBABA_API_KEY", ""),
                base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            )
            model_name = _ALIBABA_MODELS.get(stage_key, _ALIBABA_MODELS["default"])
            _print(f"[MultiAgent] Alibaba → {stage_key}: model='{model_name}'")
            resp = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=8192, temperature=0.2,
            )
            return (resp.choices[0].message.content or "").strip()

        elif selected_model == "VertexGemini":
            from google import genai
            from google.genai import types as gtypes
            project = os.getenv("VERTEX_PROJECT_ID", "")
            if not project:
                raise ValueError("VERTEX_PROJECT_ID not set")
            model_name, loc_override = _VERTEX_MODELS.get(
                stage_key, _VERTEX_MODELS["default"]
            )
            location = loc_override or os.getenv("VERTEX_LOCATION", "us-central1")
            _print(f"[MultiAgent] VertexGemini → {stage_key}: model='{model_name}', location='{location}'")
            client = genai.Client(
                vertexai=True, project=project, location=location,
            )
            resp = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=gtypes.GenerateContentConfig(max_output_tokens=65536),
            )
            return (resp.text or "").strip()

        elif selected_model == "VertexClaude":
            import anthropic
            client = anthropic.AnthropicVertex(
                project_id=os.getenv("VERTEX_PROJECT_ID", ""),
                region=os.getenv("VERTEX_LOCATION", "us-east5"),
            )
            model_name = "claude-3-5-sonnet-v2@20241022" if stage_key == "Placement" else "claude-3-5-sonnet@20240620"
            _print(f"[MultiAgent] VertexClaude → {stage_key}: model='{model_name}'")
            resp = client.messages.create(
                model=model_name,
                max_tokens=8192,
                messages=[{"role": "user", "content": prompt}],
            )
            return (resp.content[0].text or "").strip()

        else:
            _print(f"{tag} WARNING: Unknown provider '{selected_model}', falling back to Gemini.")
            from google import genai
            from google.genai import types as gtypes
            api_key = os.getenv("GEMINI_API_KEY", "")
            if not api_key:
                raise ValueError("GEMINI_API_KEY not set (fallback from unknown provider)")
            client = genai.Client(api_key=api_key)
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=gtypes.GenerateContentConfig(max_output_tokens=65536),
            )
            return (resp.text or "").strip()

    except Exception as exc:
        _print(f"{tag} LLM error (non-fatal): {exc}")
        return ""


###############################################################################
# 1.  Topology Analyst
###############################################################################

def _stage_topology(nodes: list, terminal_nets: dict, edges: list,
                    selected_model: str, task_weight: str = "light") -> str:
    """
    Stage 1 — Analyze the circuit topology.

    Runs a pure-Python first pass to extract connectivity groups
    (shared-gate → current mirrors / differential pairs, shared-drain →
    cascodes), then queries the LLM to identify the circuit function
    and produce structured placement directives.

    Parameters
    ----------
    nodes : list
        Physical device nodes (normalised Y-coords).
    terminal_nets : dict
        {device_id: {"D": net, "G": net, "S": net}}.
    edges : list
        Edge / net connection list.
    selected_model : str
        LLM provider key.
    task_weight : str
        Weight.

    Returns
    -------
    str
        Multi-line constraint text injected into every subsequent prompt.
    """
    _print("[MultiAgent] Stage 1/4: Topology Analyst...")

    safe_tn = terminal_nets if isinstance(terminal_nets, dict) else {}

    # Build group-level terminal_nets by aggregating from child finger nodes.
    # terminal_nets has keys like "MM6_m1" but group nodes have IDs like "MM6".
    # We pick the first child's nets as representative for the group.
    from ai_agent.ai_initial_placement.finger_grouper import _transistor_key
    group_terminal_nets: dict = {}
    for raw_id, tn in safe_tn.items():
        parent = _transistor_key(raw_id)
        if parent not in group_terminal_nets:
            group_terminal_nets[parent] = dict(tn)
    # Also add any direct matches (terminal_nets keyed by group name)
    for node in nodes:
        dev_id = str(node.get("id", ""))
        if dev_id not in group_terminal_nets and dev_id in safe_tn:
            group_terminal_nets[dev_id] = dict(safe_tn[dev_id])

    gate_groups:   dict = defaultdict(list)
    drain_groups:  dict = defaultdict(list)
    source_groups: dict = defaultdict(list)

    for node in nodes:
        dev_id = str(node.get("id", ""))
        nets = group_terminal_nets.get(dev_id, {})
        g = str(nets.get("G", "")).upper()
        d = str(nets.get("D", "")).upper()
        s = str(nets.get("S", "")).upper()
        if g and g not in _POWER_NETS:
            gate_groups[g].append(dev_id)
        if d and d not in _POWER_NETS:
            drain_groups[d].append(dev_id)
        if s and s not in _POWER_NETS:
            source_groups[s].append(dev_id)

    pmos = [n for n in nodes if str(n.get("type", "")).lower() == "pmos"]
    nmos = [n for n in nodes if str(n.get("type", "")).lower() == "nmos"]

    lines = [
        "=== DEVICE SUMMARY ===",
        f"Total: {len(nodes)} ({len(pmos)} PMOS, {len(nmos)} NMOS)",
    ]

    for n in sorted(nodes, key=lambda x: x.get("id", "")):
        e = n.get("electrical", {})
        tn = group_terminal_nets.get(n["id"], {})
        lines.append(
            f"  {n['id']:12s}  type={n.get('type','?'):5s}  "
            f"m={e.get('m',1)}  nf={e.get('nf',1)}  "
            f"G={tn.get('G','?')}  D={tn.get('D','?')}  S={tn.get('S','?')}"
        )

    lines.append("\n=== CONNECTIVITY GROUPS ===")
    id_to_node = {str(n.get("id", "")): n for n in nodes}

    for net, devs in sorted(gate_groups.items()):
        if len(devs) >= 2:
            types = {id_to_node.get(d, {}).get("type") for d in devs}
            tag = "DIFF-PAIR?" if len(types) > 1 else "MIRROR/MATCHED"
            lines.append(f"  shared-gate  [{net}] ({tag}): {', '.join(devs)}")

    for net, devs in sorted(drain_groups.items()):
        if len(devs) >= 2:
            lines.append(f"  shared-drain [{net}]: {', '.join(devs)}")

    for net, devs in sorted(source_groups.items()):
        if len(devs) >= 2:
            lines.append(f"  shared-src   [{net}]: {', '.join(devs)}")

    constraint_text = "\n".join(lines)

    # -- LLM enrichment --
    adjacency_str = _build_net_adjacency(nodes, edges)
    inventory_str = _build_device_inventory(nodes)

    analyst_prompt = f"""\
You are an expert analog IC layout engineer.

Analyze this circuit and output a structured identification. Be concise.

DEVICE INVENTORY:
{inventory_str}

NET CONNECTIVITY:
{adjacency_str}

CONNECTIVITY GROUPS (computed):
{constraint_text}

Identify:
1. Overall circuit type (diff amp, current mirror, telescopic OTA, folded cascode OTA, etc.)
2. Each functional block and which devices belong to it
3. Matched pairs requiring symmetric placement
4. Recommended number of NMOS rows and PMOS rows (explain why)
5. Special placement constraints (common-centroid, interdigitation, cascode stacking)

Format: 6-10 bullet points. Be specific. Use device IDs.
"""
    llm_analysis = _call_llm(analyst_prompt, selected_model, task_weight,
                              stage="TopologyAnalyst")
    if llm_analysis and len(llm_analysis.strip()) > 30:
        constraint_text = (
            "=== CIRCUIT IDENTIFICATION (AI) ===\n"
            + llm_analysis.strip()
            + "\n\n"
            + constraint_text
        )

    n_mirr = sum(1 for devs in gate_groups.values() if len(devs) >= 2)
    n_casc = sum(1 for devs in drain_groups.values() if len(devs) >= 2)
    _print(f"[MultiAgent]   {len(pmos)} PMOS  {len(nmos)} NMOS  "
          f"| {n_mirr} mirror/matched group(s)  {n_casc} cascode group(s)")

    # -- Matching detection using rich analysis from finger_grouper --
    matching_section = build_matching_section(
        nodes, edges, group_terminal_nets
    )
    if matching_section:
        constraint_text += "\n\n" + matching_section
        n_match_lines = len([l for l in matching_section.splitlines() if l.strip()])
        _print(f"[MultiAgent]   Matching analysis: {n_match_lines} constraint lines injected")

    return constraint_text, group_terminal_nets


###############################################################################
# 2.  Multi-Row Placement Prompt
###############################################################################

def _build_multirow_prompt(nodes: list, edges: list, graph_data: dict,
                            abutment_str: str,
                            constraint_text: str) -> str:
    """
    Build the comprehensive multi-row placement prompt.

    Asks the LLM to assign each device to a named functional row
    and order it Left-to-Right. Coordinates are computed later by
    ``_convert_multirow_to_geometry`` — zero floating-point hallucination.

    Includes computed target row counts for a square aspect ratio.
    """
    pmos_ids = sorted(n["id"] for n in nodes if n.get("type") == "pmos")
    nmos_ids = sorted(n["id"] for n in nodes if n.get("type") == "nmos")
    adjacency_str = _build_net_adjacency(nodes, edges)
    block_str     = _build_block_info(nodes, graph_data)

    # Electrical summary per device
    elec_lines = []
    for n in sorted(nodes, key=lambda x: x.get("id", "")):
        e = n.get("electrical", {})
        elec_lines.append(
            f"  {n['id']:12s}  {n.get('type','?'):5s}  "
            f"m={e.get('m',1)}  nf={e.get('nf',1)}  nfin={e.get('nfin',1)}"
        )
    elec_str = "\n".join(elec_lines)

    abut_section = ""
    if abutment_str and abutment_str.strip():
        abut_section = f"\nABUTMENT REQUIREMENTS (these pairs MUST be adjacent):\n{abutment_str}\n"

    # ── Compute target row counts for square aspect ratio ─────────────
    # Width per device ≈ 0.294µm, row pitch = 0.668µm.
    # For a square: n_devices_per_row * 0.294 ≈ n_total_rows * 0.668
    # So: devices_per_row ≈ sqrt(N * 0.668 / 0.294) ≈ sqrt(N * 2.27)
    n_total   = len(nmos_ids) + len(pmos_ids)
    avg_width = sum(_device_width(n) for n in nodes) / max(1, len(nodes))
    devs_per_row = max(4, int(math.sqrt(n_total * ROW_PITCH / avg_width)))
    devs_per_row = min(devs_per_row, MAX_ROW_DEVS)

    target_nmos_rows = max(1, math.ceil(len(nmos_ids) / devs_per_row))
    target_pmos_rows = max(1, math.ceil(len(pmos_ids) / devs_per_row))
    target_nmos_per  = math.ceil(len(nmos_ids) / target_nmos_rows) if target_nmos_rows else 0
    target_pmos_per  = math.ceil(len(pmos_ids) / target_pmos_rows) if target_pmos_rows else 0

    square_guidance = (
        f"SQUARE ASPECT RATIO TARGET (IMPORTANT):\n"
        f"  Total devices = {n_total} ({len(nmos_ids)} NMOS + {len(pmos_ids)} PMOS)\n"
        f"  Average device width ≈ {avg_width:.3f}µm, row pitch = {ROW_PITCH}µm\n"
        f"  For a near-square layout, aim for:\n"
        f"    • ~{target_nmos_rows} NMOS row(s) with ~{target_nmos_per} devices each\n"
        f"    • ~{target_pmos_rows} PMOS row(s) with ~{target_pmos_per} devices each\n"
        f"  Maximum {MAX_ROW_DEVS} devices per row (rows exceeding this will be auto-split).\n"
        f"  DO NOT put {len(pmos_ids)} devices in a single row — split by function.\n"
    )

    _print(f"[MultiAgent]   Square-ratio target: {target_nmos_rows} NMOS rows × "
          f"{target_nmos_per} dev + {target_pmos_rows} PMOS rows × {target_pmos_per} dev")

    return f"""\
You are an expert VLSI analog IC layout engineer.
Your task: assign every transistor to a NAMED FUNCTIONAL ROW and order them
Left-to-Right within that row for minimum wire length and maximum matching.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CIRCUIT TOPOLOGY (read carefully before placing):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{constraint_text}

ELECTRICAL PARAMETERS:
{elec_str}

NET CONNECTIVITY (place devices sharing a net ADJACENT):
{adjacency_str}

HIERARCHICAL BLOCK GROUPING:
{block_str}
{abut_section}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DEVICES TO PLACE:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NMOS ({len(nmos_ids)} total): {', '.join(nmos_ids)}
PMOS ({len(pmos_ids)} total): {', '.join(pmos_ids)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PLACEMENT RULES (MANDATORY):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RULE 1 — PMOS/NMOS SEPARATION (ABSOLUTE — no exceptions):
  • nmos_rows contains ONLY NMOS device IDs.
  • pmos_rows contains ONLY PMOS device IDs.
  • NEVER mix PMOS and NMOS in the same row.
  • Placing a PMOS ID in nmos_rows (or vice versa) is a FATAL ERROR.

RULE 2 — COMPLETE COVERAGE:
  • Every device ID appears EXACTLY ONCE across ALL rows.
  • No device may be omitted or duplicated.

RULE 3 — FUNCTIONAL ROW GROUPING:
  • Each row must contain a single functional group (see examples below).
  • Use the minimum number of rows needed — but NEVER compress two
    different functional groups (e.g. input pair + cascode) into one row.

RULE 4 — WITHIN-ROW ORDERING for MATCHING:
  • Differential pair (A,B): use A-B-B-A (interdigitated) at minimum.
    For better matching: B-A-A-B-A-A-B-B (common-centroid for multi-finger).
  • Current mirror (ref, copies): use ref-c1-c2-ref or c1-ref-c2 (centroid).
  • Cascode devices: order to match the row below (same x-slot order).
  • Multi-finger devices (_f1,_f2,…): must appear in consecutive order.
  • Abutment chains: keep chained devices consecutive as listed.

RULE 5 — ROUTING AWARENESS:
  • Place devices sharing a SIGNAL net adjacent (not separated by unrelated devices).
  • Place cascode devices directly above their drive device (same order in both rows).
  • Bias/tail devices may be independently ordered at the edges.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{square_guidance}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HOW MANY ROWS TO USE (topology-based guidance):

Simple diff-amp / 5T OTA (most common):
  nmos_rows: [ {{tail_current}}, {{input_pair}} ]
  pmos_rows: [ {{load}} ]

Telescopic OTA:
  nmos_rows: [ {{tail_current}}, {{input_pair}}, {{nmos_cascode}} ]
  pmos_rows: [ {{pmos_cascode}}, {{pmos_mirror_load}} ]

Folded-Cascode OTA:
  nmos_rows: [ {{input_pair}}, {{nmos_cascode_cs}} ]
  pmos_rows: [ {{p_folding_cs}}, {{pmos_cascode}}, {{pmos_mirror}} ]

Current mirror only (all same type, e.g. all NMOS):
  nmos_rows: [ {{mirror_main_row}} ]   ← single row if ≤ 12 devices
  pmos_rows: []   ← empty if no PMOS

Inverter / CMOS gate:
  nmos_rows: [ {{nmos_stack}} ]
  pmos_rows: [ {{pmos_stack}} ]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT (return ONLY valid JSON, no markdown):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{{
  "reasoning": "One sentence: circuit type + why you chose this row structure.",
  "nmos_rows": [
    {{"label": "FUNCTIONAL_LABEL", "devices": ["dev_id1", "dev_id2", ...]}},
    ...
  ],
  "pmos_rows": [
    {{"label": "FUNCTIONAL_LABEL", "devices": ["dev_id1", "dev_id2", ...]}},
    ...
  ]
}}

CRITICAL CHECKS before outputting:
  [ ] Every NMOS device ID appears exactly once in nmos_rows.
  [ ] Every PMOS device ID appears exactly once in pmos_rows.
  [ ] No PMOS ID is in nmos_rows. No NMOS ID is in pmos_rows.
  [ ] Multi-finger devices are consecutive (_f1, _f2, _f3 in order).
  [ ] No row has more than {MAX_ROW_DEVS} devices.
  [ ] The JSON is valid (no trailing commas, no comments).
"""


###############################################################################
# 2.5  Deterministic Topology-Driven Placement
###############################################################################

_POWER_RAIL = frozenset({"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS", ""})


def _deterministic_placement(
        nodes: list,
        edges: list,
        group_terminal_nets: dict,
        abutment_candidates: list,
) -> list:
    """
    Fully deterministic placement engine driven by circuit topology.

    Uses terminal net analysis to classify every device into a functional
    role (diff pair, tail, mirror, cascode, latch, switch) and assigns
    it to the correct row with symmetric ordering.

    Returns placed node dicts (same format as _stage_placement output).
    """
    _print("[MultiAgent] Stage 2/4: Deterministic Topology-Driven Placement...")

    node_map = {n["id"]: n for n in nodes}
    pmos_ids = {n["id"] for n in nodes if n.get("type") == "pmos"}
    nmos_ids = {n["id"] for n in nodes if n.get("type") == "nmos"}

    # ── Classify every device into functional roles ─────────────────
    roles: dict = {}  # dev_id -> role string
    used: set = set()

    # 1. Diff pair: gate = VINP or VINN
    vinp, vinn = [], []
    for gid, tn in group_terminal_nets.items():
        g = str(tn.get("G", "")).upper()
        if "VINP" in g or "INP" in g:
            vinp.append(gid)
        elif "VINN" in g or "INN" in g:
            vinn.append(gid)
    for d in vinp + vinn:
        roles[d] = "diff_pair"
        used.add(d)

    # 2. Cross-coupled latch: D of A = G of B AND D of B = G of A
    cross_coupled = []
    gids = list(group_terminal_nets.keys())
    for i, ga in enumerate(gids):
        for gb in gids[i + 1:]:
            ta = group_terminal_nets[ga]
            tb = group_terminal_nets[gb]
            if (ta.get("D") and ta["D"] == tb.get("G") and
                    tb.get("D") and tb["D"] == ta.get("G")):
                cross_coupled.append((ga, gb))
                if ga not in used:
                    roles[ga] = "cross_coupled"
                    used.add(ga)
                if gb not in used:
                    roles[gb] = "cross_coupled"
                    used.add(gb)

    # 3. Tail current: shares a non-power source/drain net with diff pair
    diff_ids = set(vinp + vinn)
    diff_source_nets = set()
    for did in diff_ids:
        tn = group_terminal_nets.get(did, {})
        s = str(tn.get("S", "")).upper()
        if s and s not in _POWER_RAIL:
            diff_source_nets.add(s)

    for gid, tn in group_terminal_nets.items():
        if gid in used:
            continue
        d_net = str(tn.get("D", "")).upper()
        s_net = str(tn.get("S", "")).upper()
        if d_net in diff_source_nets or s_net in diff_source_nets:
            roles[gid] = "tail"
            used.add(gid)

    # 4. CLK switches: gate = CLK
    for gid, tn in group_terminal_nets.items():
        if gid in used:
            continue
        g = str(tn.get("G", "")).upper()
        if g == "CLK":
            roles[gid] = "clk_switch"
            used.add(gid)

    # 5. Current mirrors: same-type, shared gate, one diode-connected
    gate_groups: dict = defaultdict(list)
    for gid, tn in group_terminal_nets.items():
        g = str(tn.get("G", "")).upper()
        if g and g not in _POWER_RAIL and g != "CLK":
            gate_groups[g].append(gid)

    for g_net, members in gate_groups.items():
        if len(members) < 2:
            continue
        types = {node_map.get(m, {}).get("type", "") for m in members}
        if len(types) != 1:
            continue
        # Check diode-connected
        has_diode = any(
            str(group_terminal_nets.get(m, {}).get("D", "")).upper() == g_net
            for m in members
        )
        if has_diode:
            for m in members:
                if m not in used:
                    roles[m] = "mirror"
                    used.add(m)

    # 6. Cascode: shares drain net with a device of different type
    drain_groups_map: dict = defaultdict(list)
    for gid, tn in group_terminal_nets.items():
        d = str(tn.get("D", "")).upper()
        if d and d not in _POWER_RAIL:
            drain_groups_map[d].append(gid)

    for d_net, members in drain_groups_map.items():
        if len(members) < 2:
            continue
        for m in members:
            if m not in used:
                roles[m] = "cascode"
                used.add(m)

    # 7. Remaining unclassified -> "other"
    for n in nodes:
        if n["id"] not in used:
            roles[n["id"]] = "other"

    # ── Log role classification ─────────────────────────────────────
    role_summary = defaultdict(list)
    for dev_id, role in roles.items():
        role_summary[role].append(dev_id)
    for role, devs in sorted(role_summary.items()):
        _print(f"[MultiAgent]   Role [{role}]: {', '.join(sorted(devs))}")

    # ── Assign to rows by function ──────────────────────────────────
    # NMOS rows (bottom to top): tail -> diff_pair -> cascode -> latch
    # PMOS rows (bottom to top): mirror/load -> cascode -> switches
    nmos_rows_plan: list = []
    pmos_rows_plan: list = []

    def _add_row(rows_list, label, dev_ids, expected_type):
        """Add a row if any devices of the expected type exist."""
        typed = [d for d in dev_ids if d in (pmos_ids if expected_type == "pmos" else nmos_ids)]
        if typed:
            rows_list.append({"label": label, "devices": typed})

    # NMOS rows
    nmos_tail = [d for d, r in roles.items() if r == "tail" and d in nmos_ids]
    nmos_diff = [d for d, r in roles.items() if r == "diff_pair" and d in nmos_ids]
    nmos_casc = [d for d, r in roles.items() if r == "cascode" and d in nmos_ids]
    nmos_latch = [d for d, r in roles.items() if r == "cross_coupled" and d in nmos_ids]
    nmos_clk = [d for d, r in roles.items() if r == "clk_switch" and d in nmos_ids]
    nmos_mirror = [d for d, r in roles.items() if r == "mirror" and d in nmos_ids]
    nmos_other = [d for d, r in roles.items() if r == "other" and d in nmos_ids]

    if nmos_tail:
        nmos_rows_plan.append({"label": "TAIL_CURRENT", "devices": sorted(nmos_tail)})
    if nmos_diff:
        # Interdigitate: VINP and VINN devices alternating
        nmos_vinp = [d for d in nmos_diff if d in vinp]
        nmos_vinn = [d for d in nmos_diff if d in vinn]
        interdig = _interdigitate_matched(nmos_vinp, nmos_vinn)
        nmos_rows_plan.append({"label": "DIFF_PAIR", "devices": interdig})
    if nmos_casc:
        nmos_rows_plan.append({"label": "NMOS_CASCODE", "devices": sorted(nmos_casc)})
    if nmos_latch:
        nmos_rows_plan.append({"label": "NMOS_LATCH", "devices": sorted(nmos_latch)})
    if nmos_clk:
        nmos_rows_plan.append({"label": "NMOS_CLK_SWITCH", "devices": sorted(nmos_clk)})
    if nmos_mirror:
        nmos_rows_plan.append({"label": "NMOS_MIRROR", "devices": sorted(nmos_mirror)})
    if nmos_other:
        nmos_rows_plan.append({"label": "NMOS_MISC", "devices": sorted(nmos_other)})

    # PMOS rows
    pmos_mirror = [d for d, r in roles.items() if r == "mirror" and d in pmos_ids]
    pmos_diff = [d for d, r in roles.items() if r == "diff_pair" and d in pmos_ids]
    pmos_casc = [d for d, r in roles.items() if r == "cascode" and d in pmos_ids]
    pmos_latch = [d for d, r in roles.items() if r == "cross_coupled" and d in pmos_ids]
    pmos_clk = [d for d, r in roles.items() if r == "clk_switch" and d in pmos_ids]
    pmos_other = [d for d, r in roles.items() if r == "other" and d in pmos_ids]

    if pmos_mirror:
        pmos_rows_plan.append({"label": "PMOS_MIRROR_LOAD", "devices": sorted(pmos_mirror)})
    if pmos_diff:
        pmos_vinp = [d for d in pmos_diff if d in vinp]
        pmos_vinn = [d for d in pmos_diff if d in vinn]
        interdig = _interdigitate_matched(pmos_vinp, pmos_vinn)
        pmos_rows_plan.append({"label": "PMOS_DIFF_PAIR", "devices": interdig})
    if pmos_latch:
        pmos_rows_plan.append({"label": "PMOS_LATCH", "devices": sorted(pmos_latch)})
    if pmos_casc:
        pmos_rows_plan.append({"label": "PMOS_CASCODE", "devices": sorted(pmos_casc)})
    if pmos_clk:
        pmos_rows_plan.append({"label": "PMOS_CLK_SWITCH", "devices": sorted(pmos_clk)})
    if pmos_other:
        pmos_rows_plan.append({"label": "PMOS_MISC", "devices": sorted(pmos_other)})

    # Fallback: if no rows planned, single row each
    if not nmos_rows_plan:
        nmos_rows_plan = [{"label": "NMOS", "devices": sorted(nmos_ids)}]
    if not pmos_rows_plan:
        pmos_rows_plan = [{"label": "PMOS", "devices": sorted(pmos_ids)}]

    # ── Log row plan ────────────────────────────────────────────────
    total_rows = len(nmos_rows_plan) + len(pmos_rows_plan)
    _print(f"[MultiAgent]   Row plan: {len(nmos_rows_plan)} NMOS + "
          f"{len(pmos_rows_plan)} PMOS = {total_rows} rows")
    for r in nmos_rows_plan:
        _print(f"[MultiAgent]     NMOS [{r['label']}]: {', '.join(r['devices'])}")
    for r in pmos_rows_plan:
        _print(f"[MultiAgent]     PMOS [{r['label']}]: {', '.join(r['devices'])}")

    # ── Convert to geometry ─────────────────────────────────────────
    placed = _convert_multirow_to_geometry(
        {"nmos_rows": nmos_rows_plan, "pmos_rows": pmos_rows_plan},
        nodes, abutment_candidates,
    )
    _print(f"[MultiAgent]   Placed {len(placed)} device(s) in "
          f"{len(nmos_rows_plan)} NMOS row(s) + {len(pmos_rows_plan)} PMOS row(s).")
    return placed


def _interdigitate_matched(group_a: list, group_b: list) -> list:
    """
    Interdigitate two matched groups in ABBA pattern for symmetry.
    A=[a1,a2], B=[b1,b2] -> [a1, b1, b2, a2]  (ABBA)
    If unequal sizes or single devices, just alternate.
    """
    if not group_a and not group_b:
        return []
    if not group_a:
        return list(group_b)
    if not group_b:
        return list(group_a)

    a = sorted(group_a)
    b = sorted(group_b)

    if len(a) == len(b):
        # True ABBA: A1 B1 B2 A2 | A3 B3 B4 A4 ...
        result = []
        i = 0
        while i < len(a):
            if i + 1 < len(a):
                result.extend([a[i], b[i], b[i+1], a[i+1]])
                i += 2
            else:
                result.extend([a[i], b[i]])
                i += 1
        return result
    else:
        # Simple alternation for unequal sizes
        result = []
        for i in range(max(len(a), len(b))):
            if i < len(a):
                result.append(a[i])
            if i < len(b):
                result.append(b[i])
        return result


def _enforce_matching_in_rows(
        placed_nodes: list,
        group_terminal_nets: dict,
) -> list:
    """
    Comprehensive post-processing of LLM placement:

    1. Extract the multi-row structure from placed geometry
    2. Split wide rows to achieve near-square aspect ratio
    3. Re-order within rows for matching (ABBA, centroid, cross-coupled)
    4. Re-run geometry engine with optimised row plan
    5. Log quality metrics
    """
    if not placed_nodes or not group_terminal_nets:
        return placed_nodes

    # ── Classify devices for matching ───────────────────────────────
    vinp_ids, vinn_ids = set(), set()
    cross_pairs = []

    for gid, tn in group_terminal_nets.items():
        g = str(tn.get("G", "")).upper()
        if "VINP" in g or "INP" in g:
            vinp_ids.add(gid)
        elif "VINN" in g or "INN" in g:
            vinn_ids.add(gid)

    gids_list = list(group_terminal_nets.keys())
    for i, ga in enumerate(gids_list):
        for gb in gids_list[i + 1:]:
            ta, tb = group_terminal_nets[ga], group_terminal_nets[gb]
            if (ta.get("D") and ta["D"] == tb.get("G") and
                    tb.get("D") and tb["D"] == ta.get("G")):
                cross_pairs.append((ga, gb))

    # ── Extract row structure from placed geometry ──────────────────
    row_buckets: dict = defaultdict(list)
    type_map = {}
    for n in placed_nodes:
        y = round(float(n.get("geometry", {}).get("y", 0.0)), 3)
        row_buckets[y].append(n)
        type_map[n["id"]] = str(n.get("type", "")).lower()

    # Sort rows by Y, separate NMOS (lower) and PMOS (upper)
    sorted_ys = sorted(row_buckets.keys())
    nmos_rows_raw = []
    pmos_rows_raw = []

    for y in sorted_ys:
        row_nodes = sorted(row_buckets[y], key=lambda n: n["geometry"]["x"])
        dev_ids = [n["id"] for n in row_nodes]
        row_type = type_map.get(dev_ids[0], "nmos") if dev_ids else "nmos"
        label = f"ROW_Y{y}"
        if row_type == "pmos":
            pmos_rows_raw.append({"label": label, "devices": dev_ids})
        else:
            nmos_rows_raw.append({"label": label, "devices": dev_ids})

    # ── Split wide rows for square aspect ratio ────────────────────
    # Compute target devices per row from total count
    all_ids = [n["id"] for n in placed_nodes]
    n_total = len(all_ids)
    avg_width = 0.294  # STD_PITCH approximation
    target_per_row = max(3, int(math.sqrt(n_total * ROW_PITCH / avg_width)))
    target_per_row = min(target_per_row, MAX_ROW_DEVS)

    def _split_row(row_dict, max_per_row):
        """Split a row if it has too many devices."""
        devs = row_dict["devices"]
        label = row_dict["label"]
        if len(devs) <= max_per_row:
            return [row_dict]
        chunks = []
        idx = 0
        while devs:
            chunk = devs[:max_per_row]
            devs = devs[max_per_row:]
            chunks.append({"label": f"{label}_{idx}", "devices": chunk})
            idx += 1
        return chunks

    nmos_rows = []
    for r in nmos_rows_raw:
        nmos_rows.extend(_split_row(r, target_per_row))
    pmos_rows = []
    for r in pmos_rows_raw:
        pmos_rows.extend(_split_row(r, target_per_row))

    rows_split = (len(nmos_rows) + len(pmos_rows)) - (len(nmos_rows_raw) + len(pmos_rows_raw))
    if rows_split > 0:
        _print(f"[MultiAgent]   Row splitting: {rows_split} row(s) split "
              f"(target {target_per_row} dev/row for square ratio)")

    # ── Re-order within rows for matching ──────────────────────────
    fixes_applied = 0
    for row in nmos_rows + pmos_rows:
        devs = row["devices"]

        # Diff pair interdigitation
        row_vinp = [d for d in devs if d in vinp_ids]
        row_vinn = [d for d in devs if d in vinn_ids]
        if row_vinp and row_vinn:
            others = [d for d in devs if d not in vinp_ids and d not in vinn_ids]
            interdig = _interdigitate_matched(row_vinp, row_vinn)
            half = len(others) // 2
            row["devices"] = others[:half] + interdig + others[half:]
            fixes_applied += 1

        # Cross-coupled: force adjacent at center
        for ga, gb in cross_pairs:
            if ga in devs and gb in devs:
                others = [d for d in row["devices"] if d != ga and d != gb]
                half = len(others) // 2
                row["devices"] = others[:half] + [ga, gb] + others[half:]
                fixes_applied += 1

    if fixes_applied:
        _print(f"[MultiAgent]   Matching enforcement: {fixes_applied} row(s) re-ordered")

    # ── Log final row plan ─────────────────────────────────────────
    total_rows = len(nmos_rows) + len(pmos_rows)
    _print(f"[MultiAgent]   Final row plan: {len(nmos_rows)} NMOS + "
          f"{len(pmos_rows)} PMOS = {total_rows} rows")
    for r in nmos_rows:
        _print(f"[MultiAgent]     NMOS [{r['label']}]: {', '.join(r['devices'])}")
    for r in pmos_rows:
        _print(f"[MultiAgent]     PMOS [{r['label']}]: {', '.join(r['devices'])}")

    # ── Re-run geometry engine with improved row plan ──────────────
    placed = _convert_multirow_to_geometry(
        {"nmos_rows": nmos_rows, "pmos_rows": pmos_rows},
        placed_nodes, [],  # No abutment candidates at group level
    )

    return placed


###############################################################################
# 3.  Multi-Row Geometry Engine
###############################################################################

def _build_abut_pairs(nodes: list, candidates: list) -> set:
    """
    Build a set of (dev_a, dev_b) abutment pairs.

    Parameters
    ----------
    nodes : list
        All device nodes (used to read embedded abutment flags).
    candidates : list
        Explicit abutment candidates from the layout JSON.

    Returns
    -------
    set of (str, str)
        Abutted device ID pairs in directed order (a abuts right -> b).
    """
    pairs: set = set()
    for c in (candidates or []):
        pairs.add((str(c.get("dev_a", "")), str(c.get("dev_b", ""))))

    # Build row-grouped adjacency from embedded flags to avoid
    # false cross-row abutment pairs.  Only devices in the SAME row
    # (Y rounded to 3dp) with adjacent X positions qualify.
    row_buckets: dict[float, list] = defaultdict(list)
    for n in nodes:
        y = round(float(n.get("geometry", {}).get("y", 0.0)), 3)
        row_buckets[y].append(n)

    for y_key, row_nodes in row_buckets.items():
        row_sorted = sorted(row_nodes, key=lambda n: n.get("geometry", {}).get("x", 0.0))
        for i in range(len(row_sorted) - 1):
            n1 = row_sorted[i]
            n2 = row_sorted[i + 1]
            if (n1.get("abutment", {}).get("abut_right")
                    and n2.get("abutment", {}).get("abut_left")):
                pairs.add((str(n1.get("id", "")), str(n2.get("id", ""))))

    return pairs


def _place_row(devices: list, row_y: float, node_map: dict,
               abut_pairs: set) -> list:
    """
    Pack a single row of devices left-to-right with correct spacing.

    Parameters
    ----------
    devices : list[str]
        Ordered device ID list for this row.
    row_y : float
        Y coordinate for all devices in this row (µm).
    node_map : dict
        {device_id: original_node_dict}.
    abut_pairs : set
        (dev_a, dev_b) pairs that must use ABUT_SPACING.

    Returns
    -------
    list
        Placed node dicts (deep copies with geometry set).
    """
    placed = []
    cursor = 0.0
    for idx, dev_id in enumerate(devices):
        if dev_id not in node_map:
            _print(f"[MultiAgent][geo] WARNING: '{dev_id}' not in node_map — skipping")
            continue
        node = copy.deepcopy(node_map[dev_id])
        geo  = node.setdefault("geometry", {})
        geo["x"] = round(cursor, 6)
        geo["y"] = row_y
        geo.setdefault("orientation", "R0")

        # Abutment flags
        abut_left  = (idx > 0 and (devices[idx - 1], dev_id) in abut_pairs)
        abut_right = (idx < len(devices) - 1
                      and (dev_id, devices[idx + 1]) in abut_pairs)
        node["abutment"] = {"abut_left": abut_left, "abut_right": abut_right}

        # Advance cursor by the ACTUAL device width (not STD_PITCH)
        if idx < len(devices) - 1:
            next_id = devices[idx + 1]
            if (dev_id, next_id) in abut_pairs:
                cursor = round(cursor + ABUT_SPACING, 6)
            else:
                cursor = round(cursor + _device_width(node), 6)

        placed.append(node)
    return placed


def _convert_multirow_to_geometry(multirow_data: dict, original_nodes: list,
                                   abutment_candidates: list) -> list:
    """
    Convert multi-row LLM output to exact physical geometry.

    NMOS rows get y = 0, ROW_PITCH, 2×ROW_PITCH, …
    PMOS rows get y = n_nmos×ROW_PITCH, (n_nmos+1)×ROW_PITCH, …
    This guarantees min(PMOS y) > max(NMOS y) by exactly ROW_PITCH.

    Also handles the legacy 2-row schema ``{nmos_order, pmos_order}``.

    Parameters
    ----------
    multirow_data : dict
        LLM output (multi-row or legacy schema).
    original_nodes : list
        All device nodes (with metadata, electrical params, etc.).
    abutment_candidates : list
        Abutment pair candidates from the layout JSON.

    Returns
    -------
    list
        All placed node dicts with x/y geometry set.
    """
    # ── Legacy 2-row fallback ───────────────────────────────────────
    if "nmos_order" in multirow_data or "pmos_order" in multirow_data:
        return _convert_slots_to_geometry(
            multirow_data, original_nodes, abutment_candidates
        )

    nmos_rows = multirow_data.get("nmos_rows", [])
    pmos_rows = multirow_data.get("pmos_rows", [])

    # ── Auto-split oversized rows ─────────────────────────────────
    # If the LLM puts >MAX_ROW_DEVS devices in a single row, split it
    # into multiple sub-rows for a more square aspect ratio.
    def _split_rows(rows: list) -> list:
        split = []
        for row in rows:
            devs = row.get("devices", [])
            label = row.get("label", "row")
            if len(devs) <= MAX_ROW_DEVS:
                split.append(row)
            else:
                chunk_idx = 0
                while devs:
                    chunk = devs[:MAX_ROW_DEVS]
                    devs = devs[MAX_ROW_DEVS:]
                    split.append({"label": f"{label}_sub{chunk_idx}", "devices": chunk})
                    chunk_idx += 1
        return split

    nmos_rows = _split_rows(nmos_rows)
    pmos_rows = _split_rows(pmos_rows)

    # ── Total fallback — alphabetical single-row each ───────────────
    if not nmos_rows and not pmos_rows:
        nmos_ids = sorted(n["id"] for n in original_nodes if n.get("type") == "nmos")
        pmos_ids = sorted(n["id"] for n in original_nodes if n.get("type") == "pmos")
        return _convert_slots_to_geometry(
            {"nmos_order": nmos_ids, "pmos_order": pmos_ids},
            original_nodes, abutment_candidates,
        )

    node_map   = {n["id"]: n for n in original_nodes if "id" in n}
    abut_pairs = _build_abut_pairs(original_nodes, abutment_candidates)

    # ── Compute row Y coordinates ───────────────────────────────────
    # Dynamically compute row pitch from actual device height.
    # The hardcoded ROW_PITCH=0.668 is too small for devices with
    # height=0.818 — causes vertical overlap in every row.
    max_height = max(
        (float(n.get("geometry", {}).get("height", 0.5)) for n in original_nodes),
        default=0.5
    )
    row_pitch = round(max(ROW_PITCH, max_height + 0.15), 3)  # 0.15um routing gap
    pmos_nmos_gap = round(row_pitch, 3)  # same as row pitch (no wasted space)

    _print(f"[MultiAgent]   Row pitch: {row_pitch:.3f}µm "
          f"(device height={max_height:.3f}µm, gap={row_pitch - max_height:.3f}µm)")
    _print(f"[MultiAgent]   PMOS/NMOS gap: {pmos_nmos_gap:.3f}µm")

    n_nmos = len(nmos_rows)
    n_pmos = len(pmos_rows)
    nmos_ys = [round(i * row_pitch, 6) for i in range(n_nmos)]
    pmos_base = round(n_nmos * row_pitch + pmos_nmos_gap, 6)  # extra gap between types
    pmos_ys   = [round(pmos_base + j * row_pitch, 6) for j in range(n_pmos)]

    placed_ids: set  = set()
    placed_nodes: list = []

    # ── Place NMOS rows ─────────────────────────────────────────────
    for row_idx, row in enumerate(nmos_rows):
        y       = nmos_ys[row_idx]
        devices = [d for d in row.get("devices", []) if d not in placed_ids]
        label   = row.get("label", f"nmos_row_{row_idx}")
        _print(f"[MultiAgent]   NMOS row {row_idx} [{label}]  y={y:.3f}  "
              f"{len(devices)} device(s)")
        row_nodes = _place_row(devices, y, node_map, abut_pairs)
        placed_nodes.extend(row_nodes)
        placed_ids.update(n["id"] for n in row_nodes)

    # ── Place PMOS rows ─────────────────────────────────────────────
    for row_idx, row in enumerate(pmos_rows):
        y       = pmos_ys[row_idx]
        devices = [d for d in row.get("devices", []) if d not in placed_ids]
        label   = row.get("label", f"pmos_row_{row_idx}")
        _print(f"[MultiAgent]   PMOS row {row_idx} [{label}]  y={y:.3f}  "
              f"{len(devices)} device(s)")
        row_nodes = _place_row(devices, y, node_map, abut_pairs)
        placed_nodes.extend(row_nodes)
        placed_ids.update(n["id"] for n in row_nodes)

    # ── Any device not yet placed → append to correct row ──────────
    for n in original_nodes:
        nid = n.get("id", "")
        if nid in placed_ids:
            continue
        dev_type = str(n.get("type", "")).lower()
        if dev_type == "nmos":
            y = nmos_ys[-1] if nmos_ys else 0.0
        elif dev_type == "pmos":
            y = pmos_ys[-1] if pmos_ys else pmos_base
        else:
            # passive (res/cap) — dedicated row above everything
            y = round(pmos_base + n_pmos * row_pitch + row_pitch, 6)

        # Find leftmost free x in that row
        used_x = {round(p["geometry"]["x"], 6)
                  for p in placed_nodes
                  if round(p.get("geometry", {}).get("y", -999), 6) == y}
        w = _device_width(n)
        x = 0.0
        while round(x, 6) in used_x:
            x = round(x + w, 6)

        orphan = copy.deepcopy(n)
        geo    = orphan.setdefault("geometry", {})
        geo["x"] = round(x, 6)
        geo["y"] = y
        geo.setdefault("orientation", "R0")
        orphan["abutment"] = {"abut_left": False, "abut_right": False}
        placed_nodes.append(orphan)
        placed_ids.add(nid)
        _print(f"[MultiAgent]   Orphan '{nid}' ({dev_type}) → ({x:.3f}, {y:.3f})")

    # ── Center all rows for symmetric layout ──────────────────────
    # Compute each row's bounding width (last device x + its width),
    # then shift each row so its center aligns with the widest row.
    row_nodes_by_y: dict = defaultdict(list)
    for p in placed_nodes:
        ry = round(float(p.get("geometry", {}).get("y", 0.0)), 6)
        row_nodes_by_y[ry].append(p)

    global_max_width = 0.0
    row_widths = {}
    for ry, rnodes in row_nodes_by_y.items():
        if rnodes:
            rightmost = max(rnodes, key=lambda n: float(n["geometry"]["x"]))
            row_w = float(rightmost["geometry"]["x"]) + _device_width(rightmost)
            row_widths[ry] = row_w
            global_max_width = max(global_max_width, row_w)

    if global_max_width > 0:
        for ry, rnodes in row_nodes_by_y.items():
            if not rnodes:
                continue
            row_w = row_widths.get(ry, 0)
            shift = round((global_max_width - row_w) / 2.0, 6)
            if shift > 0:
                for n in rnodes:
                    n["geometry"]["x"] = round(float(n["geometry"]["x"]) + shift, 6)

    # ── Log layout metrics ──────────────────────────────────────────
    nmos_base = nmos_ys[0] if nmos_ys else 0.0
    pmos_top  = (pmos_ys[-1] + max_height) if pmos_ys else 0.0
    total_height = max(pmos_top, (nmos_ys[-1] + max_height) if nmos_ys else 0.0) - nmos_base
    aspect = global_max_width / total_height if total_height > 0 else 0
    _print(f"[MultiAgent]   Layout: {global_max_width:.3f}µm × {total_height:.3f}µm "
          f"(aspect={aspect:.2f}, target≈1.0)")

    return placed_nodes


###############################################################################
# 4.  Placement Specialist (multi-row aware)
###############################################################################

def _validate_multirow(nodes: list, placed: list) -> list:
    """
    Lightweight multi-row-aware placement validation.

    Checks that:
    - Every input device ID appears in the output.
    - No device has a mixed type (PMOS placed at NMOS y, etc.).
    - No two devices in the SAME ROW overlap (x-distance < min spacing).

    Uses a direct minimum-distance check instead of slot rounding to
    avoid false positives when device widths differ from STD_PITCH.
    """
    errors: list = []
    orig_ids   = {n["id"] for n in nodes}
    orig_types = {n["id"]: n.get("type", "?") for n in nodes}
    placed_ids = {p.get("id") for p in placed if isinstance(p, dict) and p.get("id")}

    # 1. Coverage + duplicates
    missing = orig_ids - placed_ids
    extra   = placed_ids - orig_ids
    if missing:
        errors.append(f"MISSING devices: {sorted(missing)}")
    if extra:
        errors.append(f"EXTRA (invented) devices: {sorted(extra)}")

    from collections import Counter
    id_counts = Counter(p.get("id") for p in placed if isinstance(p, dict) and p.get("id"))
    duplicates = [dev_id for dev_id, count in id_counts.items() if count > 1]
    if duplicates:
        errors.append(f"DUPLICATE devices: {sorted(duplicates)}")

    # 2. Same-row overlap (bounding-box based, not slot-based)
    # For non-abutted pairs: n2.x must be >= n1.x + n1.width (no overlap)
    # For abutted pairs: n2.x must be exactly n1.x + ABUT_SPACING (0.070 µm)
    row_devs: dict = defaultdict(list)
    for p in placed:
        if not isinstance(p, dict):
            continue
        geo = p.get("geometry", {})
        x   = float(geo.get("x", 0.0))
        y   = round(float(geo.get("y", 0.0)), 3)
        w   = float(geo.get("width", STD_PITCH))
        row_devs[y].append((x, w, p.get("id", "?"), p))

    for y, devs in row_devs.items():
        devs_sorted = sorted(devs, key=lambda d: d[0])
        for i in range(len(devs_sorted) - 1):
            x_a, w_a, id_a, node_a = devs_sorted[i]
            x_b, w_b, id_b, node_b = devs_sorted[i + 1]
            # Check if this is an abutted pair
            abut_a = node_a.get("abutment", {})
            abut_b = node_b.get("abutment", {})
            is_abutted = abut_a.get("abut_right") and abut_b.get("abut_left")
            if is_abutted:
                # Abutted: must be exactly ABUT_SPACING apart
                if abs(x_b - x_a - ABUT_SPACING) > 0.005:
                    errors.append(
                        f"Abutment spacing error in row y={y:.3f}: "
                        f"'{id_a}' and '{id_b}' "
                        f"delta X={x_b - x_a:.4f}µm, expected {ABUT_SPACING:.3f}µm"
                    )
            else:
                # Non-abutted: n2.x must be >= n1.x + n1.width
                min_x_b = x_a + w_a
                if x_b < min_x_b - 0.001:
                    errors.append(
                        f"OVERLAP in row y={y:.3f}: '{id_a}' and '{id_b}' "
                        f"n2.x={x_b:.4f} < n1.x+n1.w={min_x_b:.4f} "
                        f"(bounding boxes overlap by {min_x_b - x_b:.4f}µm)"
                    )

    # 3. Type must not change
    for p in placed:
        if not isinstance(p, dict):
            continue
        pid = p.get("id", "")
        if pid in orig_types and p.get("type") and p["type"] != orig_types[pid]:
            errors.append(
                f"TYPE CHANGED: {pid} was {orig_types[pid]}, now {p['type']}"
            )

    return errors


def _stage_placement(nodes: list, edges: list, graph_data: dict,
                     abutment_candidates: list,
                     constraint_text: str,
                     selected_model: str,
                     task_weight: str = "heavy") -> list:
    """
    Stage 3 — Multi-row Placement Specialist.

    Calls the LLM with the multi-row prompt and converts the returned
    ordering to physical geometry. Retries up to MAX_RETRIES times with
    targeted error feedback. Falls back to alphabetical deterministic
    placement if all attempts fail.

    Parameters
    ----------
    nodes : list
        Normalised device nodes.
    edges, graph_data : list, dict
        Edge list and full graph data.
    abutment_candidates : list
        Abutment pair candidates.
    constraint_text : str
        Topology summary from Stage 1.
    selected_model : str
    task_weight : str

    Returns
    -------
    list
        Placed node dicts with geometry.
    """
    _print("[MultiAgent] Stage 2/4: Placement Specialist (multi-row)…")

    abutment_str = _format_abutment_candidates(abutment_candidates)
    prompt       = _build_multirow_prompt(nodes, edges, graph_data,
                                          abutment_str, constraint_text)

    expected_nmos = {n["id"] for n in nodes if n.get("type") == "nmos"}
    expected_pmos = {n["id"] for n in nodes if n.get("type") == "pmos"}

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        _print(f"[MultiAgent]   Placement attempt {attempt}/{MAX_RETRIES}…")
        raw = _call_llm(prompt, selected_model, task_weight,
                        stage=f"Placement#{attempt}")
        if not raw:
            last_error = "Empty LLM response"
            prompt += "\n\nPREVIOUS ATTEMPT RETURNED EMPTY — please try again."
            continue

        # ── Parse ──────────────────────────────────────────────────
        try:
            data = sanitize_json(raw)
        except Exception as exc:
            last_error = f"JSON parse failed: {exc}"
            prompt += f"\n\nJSON PARSE FAILED ({exc}). Return ONLY valid JSON."
            continue

        if not isinstance(data, dict):
            last_error = (
                f"Parsed JSON root must be an object, got {type(data).__name__}"
            )
            prompt += (
                "\n\nINVALID OUTPUT SHAPE: return a JSON object with "
                "'nmos_rows' and 'pmos_rows' keys."
            )
            continue

        # Handle legacy schema returned by a stubborn model
        if "nmos_order" in data or "pmos_order" in data:
            nmos_rows = [{"label": "nmos", "devices": data.get("nmos_order", [])}]
            pmos_rows = [{"label": "pmos", "devices": data.get("pmos_order", [])}]
            data = {"nmos_rows": nmos_rows, "pmos_rows": pmos_rows}

        nmos_rows = data.get("nmos_rows", [])
        pmos_rows = data.get("pmos_rows", [])

        if not nmos_rows and not pmos_rows:
            last_error = "Both nmos_rows and pmos_rows are empty"
            prompt += "\n\nBOTH ROWS EMPTY — include every device."
            continue

        # ── Enforce correct types within rows ──────────────────────
        pmos_ids_in_nmos = []
        nmos_ids_in_pmos = []
        for row in nmos_rows:
            for dev_id in row.get("devices", []):
                if dev_id in expected_pmos:
                    pmos_ids_in_nmos.append(dev_id)
        for row in pmos_rows:
            for dev_id in row.get("devices", []):
                if dev_id in expected_nmos:
                    nmos_ids_in_pmos.append(dev_id)

        if pmos_ids_in_nmos or nmos_ids_in_pmos:
            err = (
                f"TYPE MISMATCH: PMOS IDs in nmos_rows: {pmos_ids_in_nmos}, "
                f"NMOS IDs in pmos_rows: {nmos_ids_in_pmos}"
            )
            last_error = err
            prompt += (
                f"\n\nFATAL ERROR — {err}\n"
                "PMOS device IDs MUST only appear in pmos_rows.\n"
                "NMOS device IDs MUST only appear in nmos_rows.\n"
                f"NMOS IDs: {sorted(expected_nmos)}\n"
                f"PMOS IDs: {sorted(expected_pmos)}"
            )
            continue

        # ── Fill missing devices ────────────────────────────────────
        placed_nmos = {d for r in nmos_rows for d in r.get("devices", [])}
        placed_pmos = {d for r in pmos_rows for d in r.get("devices", [])}

        missing_nmos = sorted(expected_nmos - placed_nmos)
        missing_pmos = sorted(expected_pmos - placed_pmos)

        if missing_nmos:
            nmos_rows.append({"label": "misc_nmos", "devices": missing_nmos})
            _print(f"[MultiAgent]   Auto-appended NMOS: {missing_nmos}")
        if missing_pmos:
            pmos_rows.append({"label": "misc_pmos", "devices": missing_pmos})
            _print(f"[MultiAgent]   Auto-appended PMOS: {missing_pmos}")

        # ── Geometry conversion ─────────────────────────────────────
        try:
            placed = _convert_multirow_to_geometry(
                {"nmos_rows": nmos_rows, "pmos_rows": pmos_rows},
                nodes, abutment_candidates,
            )
        except Exception as exc:
            last_error = f"Geometry failed: {exc}"
            continue

        # ── Validate ───────────────────────────────────────────────
        val_errs = _validate_multirow(nodes, placed)
        if val_errs:
            err_summary = "; ".join(val_errs[:3])
            last_error  = err_summary
            prompt += (
                f"\n\nVALIDATION FAILED: {err_summary}\n"
                "Fix the issues and try again."
            )
            continue

        n_nmos_rows = len(nmos_rows)
        n_pmos_rows = len(pmos_rows)
        _print(f"[MultiAgent]   ✓ Placed {len(placed)} device(s) in "
              f"{n_nmos_rows} NMOS row(s) + {n_pmos_rows} PMOS row(s).")
        return placed

    # ── Deterministic fallback ─────────────────────────────────────
    _print(f"[MultiAgent]   All LLM attempts failed ({last_error}). "
          "Using deterministic fallback.")
    return _deterministic_fallback(nodes, abutment_candidates)


def _deterministic_fallback(nodes: list, abutment_candidates: list) -> list:
    """
    Connectivity-aware multi-row deterministic fallback.

    Instead of dumping all NMOS/PMOS into a single row each (which
    produces an extremely elongated, unusable layout), this groups
    devices by shared gate nets (mirrors, diff pairs) and splits
    large groups into multiple rows for a roughly square aspect ratio.

    Matched pairs are interdigitated (A-B-B-A) for symmetry.

    Parameters
    ----------
    nodes : list
        Physical device nodes.
    abutment_candidates : list
        Abutment pair candidates.

    Returns
    -------
    list
        Placed node dicts.
    """
    MAX_ROW_WIDTH = 14  # Max devices per row for ~square aspect ratio

    # ── Separate by type ────────────────────────────────────────────
    nmos_nodes = [n for n in nodes if n.get("type") == "nmos"]
    pmos_nodes = [n for n in nodes if n.get("type") == "pmos"]

    def _build_rows(typed_nodes: list) -> list:
        """Group devices into rows by connectivity, then split large rows."""
        if not typed_nodes:
            return []

        # Group by parent device (strip _fN, _mN suffixes)
        parent_groups: dict = defaultdict(list)
        for n in typed_nodes:
            dev_id = n["id"]
            parent = re.sub(r'_[mf]\d+$', '', dev_id)
            parent_groups[parent].append(dev_id)

        # Sort parents and interdigitate matched pairs (same-size groups)
        parents = sorted(parent_groups.keys())
        used = set()
        rows = []
        current_row = []

        for parent in parents:
            if parent in used:
                continue
            group = parent_groups[parent]

            # Find a matching partner (same finger count, unused)
            partner = None
            for other in parents:
                if other != parent and other not in used:
                    if len(parent_groups[other]) == len(group):
                        partner = other
                        break

            if partner:
                # Interdigitate: A_f1, B_f1, A_f2, B_f2, ... (common centroid)
                a_devs = sorted(parent_groups[parent])
                b_devs = sorted(parent_groups[partner])
                interdig = []
                for a, b in zip(a_devs, b_devs):
                    interdig.extend([a, b])
                # Any remaining from uneven sizes
                interdig.extend(a_devs[len(b_devs):])
                interdig.extend(b_devs[len(a_devs):])

                if len(current_row) + len(interdig) > MAX_ROW_WIDTH:
                    if current_row:
                        rows.append(current_row)
                    current_row = interdig
                else:
                    current_row.extend(interdig)
                used.add(parent)
                used.add(partner)
            else:
                # No partner — add group sequentially
                if len(current_row) + len(group) > MAX_ROW_WIDTH:
                    if current_row:
                        rows.append(current_row)
                    current_row = sorted(group)
                else:
                    current_row.extend(sorted(group))
                used.add(parent)

        if current_row:
            rows.append(current_row)

        # Split any remaining oversized rows
        final_rows = []
        for row in rows:
            while len(row) > MAX_ROW_WIDTH:
                final_rows.append(row[:MAX_ROW_WIDTH])
                row = row[MAX_ROW_WIDTH:]
            if row:
                final_rows.append(row)

        return final_rows

    nmos_row_lists = _build_rows(nmos_nodes)
    pmos_row_lists = _build_rows(pmos_nodes)

    nmos_rows = [{"label": f"nmos_group_{i}", "devices": devs}
                 for i, devs in enumerate(nmos_row_lists)]
    pmos_rows = [{"label": f"pmos_group_{i}", "devices": devs}
                 for i, devs in enumerate(pmos_row_lists)]

    # If no rows built, fall back to single rows
    if not nmos_rows and not pmos_rows:
        nmos_ids = sorted(n["id"] for n in nmos_nodes)
        pmos_ids = sorted(n["id"] for n in pmos_nodes)
        nmos_rows = [{"label": "nmos", "devices": nmos_ids}]
        pmos_rows = [{"label": "pmos", "devices": pmos_ids}]

    n_total_rows = len(nmos_rows) + len(pmos_rows)
    _print(f"[MultiAgent]   Deterministic fallback: {len(nmos_rows)} NMOS row(s) + "
          f"{len(pmos_rows)} PMOS row(s) = {n_total_rows} total")

    return _convert_multirow_to_geometry(
        {"nmos_rows": nmos_rows, "pmos_rows": pmos_rows},
        nodes, abutment_candidates,
    )


###############################################################################
# 5.  DRC Healing (deterministic, multi-row aware)
###############################################################################

def _stage_drc_and_heal(placed_nodes: list, abutment_candidates: list,
                         no_abutment: bool) -> list:
    """
    Stage 4 — Deterministic DRC healing for multi-row layouts.

    Runs per-row left-to-right repacking to eliminate x-collisions,
    then applies the existing abutment-spacing enforcement pass.
    PMOS/NMOS row separation is not modified (guaranteed by geometry engine).

    Parameters
    ----------
    placed_nodes : list
        Placed node dicts from Stage 3.
    abutment_candidates : list
        Abutment pair candidates.
    no_abutment : bool
        If True, use STD_PITCH everywhere (skip abutment logic).

    Returns
    -------
    list
        Geometrically corrected node dicts.
    """
    _print("[MultiAgent] Stage 3/4: DRC & Healing…")

    # ── Group nodes by row (y-value) ───────────────────────────────
    row_buckets: dict = defaultdict(list)
    for node in placed_nodes:
        y = round(float(node.get("geometry", {}).get("y", 0.0)), 3)
        row_buckets[y].append(node)

    # ── Re-pack each row to remove x-collisions ────────────────────
    abut_pairs = _build_abut_pairs(placed_nodes, abutment_candidates)
    for y_key, row_nodes in row_buckets.items():
        # Sort by current x (preserves relative LLM ordering)
        row_sorted = sorted(row_nodes, key=lambda n: n.get("geometry", {}).get("x", 0.0))
        # Preserve the row's original leftmost position rather than forcing x=0
        cursor = row_sorted[0].get("geometry", {}).get("x", 0.0) if row_sorted else 0.0
        for idx, node in enumerate(row_sorted):
            geo      = node.setdefault("geometry", {})
            nid      = node.get("id", "")
            geo["x"] = round(cursor, 6)
            geo["y"] = y_key  # enforce exact row y

            next_node = row_sorted[idx + 1] if idx < len(row_sorted) - 1 else None
            if next_node:
                next_id = next_node.get("id", "")
                if not no_abutment and (nid, next_id) in abut_pairs:
                    cursor = round(cursor + ABUT_SPACING, 6)
                else:
                    cursor = round(cursor + _device_width(node), 6)

    # ── Force exact 0.070µm between known abutted pairs ───────────
    if not no_abutment:
        placed_nodes = _force_abutment_spacing(placed_nodes, abutment_candidates)

    _print("[MultiAgent]   DRC healing complete.")

    # ── Re-center all rows for symmetric layout ──────────────────────
    row_nodes_by_y: dict = defaultdict(list)
    for p in placed_nodes:
        ry = round(float(p.get("geometry", {}).get("y", 0.0)), 3)
        row_nodes_by_y[ry].append(p)

    global_max_width = 0.0
    row_widths = {}
    for ry, rnodes in row_nodes_by_y.items():
        if rnodes:
            rightmost = max(rnodes, key=lambda n: float(n["geometry"]["x"]))
            row_w = float(rightmost["geometry"]["x"]) + _device_width(rightmost)
            row_widths[ry] = row_w
            global_max_width = max(global_max_width, row_w)

    if global_max_width > 0:
        for ry, rnodes in row_nodes_by_y.items():
            if not rnodes:
                continue
            row_w = row_widths.get(ry, 0)
            shift = round((global_max_width - row_w) / 2.0, 6)
            if shift > 0.001:
                for n in rnodes:
                    n["geometry"]["x"] = round(float(n["geometry"]["x"]) + shift, 6)
        _print(f"[MultiAgent]   Rows re-centered (max width={global_max_width:.3f}µm)")

    return placed_nodes


###############################################################################
# 6.  SA post-optimisation (optional)
###############################################################################

def _run_sa(nodes: list, edges: list, abutment_candidates: list) -> list:
    """
    Optional Simulated Annealing post-pass for HPWL minimisation.

    Within-row reordering only — does NOT change row assignments.

    Parameters
    ----------
    nodes : list
        Placed node dicts after DRC healing.
    edges : list
        Edge list for HPWL evaluation.
    abutment_candidates : list
        Abutment constraints for the SA feasibility checker.

    Returns
    -------
    list
        SA-optimised nodes, or original nodes if SA raises an error.
    """
    try:
        from ai_agent.ai_initial_placement.sa_optimizer import optimize_placement
        _print("[MultiAgent] Running SA Post-Optimisation…")
        result = optimize_placement(nodes, edges, abutment_candidates=abutment_candidates)
        _print("[MultiAgent] SA complete.")
        return result
    except Exception as sa_err:
        _print(f"[MultiAgent] SA failed (non-fatal): {sa_err}")
        return nodes


###############################################################################
# 7.  Public entry point
###############################################################################

def multi_agent_generate_placement(
    input_json_path: str,
    output_json_path: str,
    selected_model: str = "Gemini",
    task_weight: str = "heavy",
    run_sa: bool = False,
) -> None:
    """
    Execute the complete 4-stage autonomous multi-row placement pipeline.

    Reads the layout JSON, chains all four stages, and writes the final
    physically valid, DRC-clean placement to disk.

    Pipeline
    --------
    1. Topology Analyst   — Pure-Python + LLM circuit identification.
    2-3. Placement Spec.  — Multi-row slot assignment (LLM) + geometry
                            computed deterministically.
    4. DRC Healing        — Per-row repacking + abutment enforcement.
    (opt) SA Optimizer    — HPWL minimisation via Simulated Annealing.

    Parameters
    ----------
    input_json_path : str
        Path to the input layout JSON (must contain 'nodes').
    output_json_path : str
        Path where the placed JSON will be written.
    selected_model : str
        LLM provider: "Gemini" | "Alibaba" | "VertexGemini" | "VertexClaude".
        Defaults to "Gemini".
    task_weight : str
        "light" or "heavy" logic mapping. Defaults to "heavy".
    run_sa : bool
        Run Simulated Annealing post-pass. Default: False.

    Raises
    ------
    ValueError
        If the input JSON contains no device nodes.
    FileNotFoundError
        If ``input_json_path`` does not exist.
    """
    _print(f"\n[MultiAgent] === Autonomous Multi-Row Placement -- {selected_model} ===")

    # Suppress expand/resolve noise during placement
    if os.environ.get("PLACEMENT_DEBUG_FULL_LOG", "0").lower() not in ("1", "true", "yes"):
        os.environ["PLACEMENT_STEPS_ONLY"] = "1"

    # -- Load --
    with open(input_json_path, "r", encoding="utf-8") as f:
        graph_data = json.load(f)

    nodes: list              = graph_data.get("nodes", [])
    edges: list              = graph_data.get("edges", [])
    terminal_nets: dict      = graph_data.get("terminal_nets", {})
    abutment_candidates: list = graph_data.get("abutment_candidates", [])
    no_abutment: bool        = graph_data.get("no_abutment", False)

    if not nodes:
        raise ValueError(f"No device nodes found in '{input_json_path}'.")

    n_pmos = sum(1 for n in nodes if str(n.get("type", "")).lower() == "pmos")
    n_nmos = sum(1 for n in nodes if str(n.get("type", "")).lower() == "nmos")
    _print(f"[MultiAgent] Loaded {len(nodes)} devices ({n_pmos} PMOS, {n_nmos} NMOS), "
          f"{len(edges)} edges.")

    # ── Normalise Y-coordinates (same as all working placers) ───────
    norm_nodes, y_offset = _normalise_coords(nodes)
    if abs(y_offset) > 1e-9:
        _print(f"[MultiAgent] Y-offset applied: {y_offset:+.4f} µm")

    # ── Collapse finger-level nodes → transistor groups for LLM ──────
    group_nodes, group_edges, finger_map = group_fingers(norm_nodes, edges)
    _print(f"[MultiAgent] Finger grouping: {len(norm_nodes)} nodes → {len(group_nodes)} groups")

    prompt_graph = dict(graph_data)
    prompt_graph["nodes"] = group_nodes
    prompt_graph["edges"] = group_edges

    # ── Stage 1/4: Topology Analyst (flash — fast analysis) ─────────
    _print(f"[MultiAgent] Model assignment per stage:")
    if selected_model == "VertexGemini":
        for k, (m, loc) in _VERTEX_MODELS.items():
            if k == "default": continue
            _print(f"[MultiAgent]   {k:20s} → {m} (location={loc or 'default'})")
    elif selected_model == "Gemini":
        for k, m in _GEMINI_MODELS.items():
            if k == "default": continue
            _print(f"[MultiAgent]   {k:20s} → {m}")
    elif selected_model == "Alibaba":
        for k, m in _ALIBABA_MODELS.items():
            if k == "default": continue
            _print(f"[MultiAgent]   {k:20s} → {m}")

    constraint_text, group_terminal_nets = _stage_topology(
        group_nodes, terminal_nets, group_edges, selected_model, "light"
    )

    # -- Stage 2/4: LLM Placement + Deterministic Matching Enforcement --
    try:
        placed_groups = _stage_placement(
            group_nodes, group_edges, prompt_graph,
            abutment_candidates, constraint_text,
            selected_model, "heavy",
        )
        # Post-process: enforce matching/symmetry within rows
        placed_groups = _enforce_matching_in_rows(
            placed_groups, group_terminal_nets
        )
    except Exception as exc:
        _print(f"[MultiAgent]   LLM placement failed ({exc}), using deterministic fallback...")
        placed_groups = _deterministic_placement(
            group_nodes, group_edges, group_terminal_nets,
            abutment_candidates,
        )

    # ── Expand groups back to finger-level nodes ───────────────────
    original_group_nodes = {n["id"]: n for n in group_nodes}
    placed_nodes = expand_groups(
        placed_groups, finger_map,
        no_abutment=no_abutment,
        original_group_nodes=original_group_nodes,
    )
    _print(f"[MultiAgent] Expanded {len(placed_groups)} groups → {len(placed_nodes)} finger nodes")

    # ── Stage 3/4: DRC & Abutment Healing ──────────────────────────
    placed_nodes = _stage_drc_and_heal(placed_nodes, abutment_candidates, no_abutment)

    # ── Stage 4/4: SA Post-Optimisation (optional) ─────────────────

    if run_sa:
        placed_nodes = _run_sa(placed_nodes, edges, abutment_candidates)

    # ── Restore original Y-coordinate frame ────────────────────────
    placed_nodes = _restore_coords(placed_nodes, y_offset)

    # -- Validate PMOS/NMOS separation in final output --
    orig_type = {n["id"]: n.get("type", "?") for n in nodes}
    pmos_ys   = [round(float(p.get("geometry", {}).get("y", 0)), 4)
                 for p in placed_nodes if orig_type.get(p.get("id", "")) == "pmos"]
    nmos_ys   = [round(float(p.get("geometry", {}).get("y", 0)), 4)
                 for p in placed_nodes if orig_type.get(p.get("id", "")) == "nmos"]
    if pmos_ys and nmos_ys:
        if min(pmos_ys) <= max(nmos_ys):
            _print(f"[MultiAgent] [!!] PMOS/NMOS overlap detected in final output "
                  f"(min PMOS y={min(pmos_ys):.4f} <= max NMOS y={max(nmos_ys):.4f}). "
                  "Check DRC after import.")
        else:
            _print(f"[MultiAgent] [OK] PMOS/NMOS separation OK "
                  f"(PMOS >= {min(pmos_ys):.4f} > NMOS <= {max(nmos_ys):.4f})")

    # -- Quality Report --
    all_xs = [float(p.get("geometry", {}).get("x", 0)) for p in placed_nodes]
    all_ys = [float(p.get("geometry", {}).get("y", 0)) for p in placed_nodes]
    widths = [_device_width(p) for p in placed_nodes]
    heights = [float(p.get("geometry", {}).get("height", 0.5)) for p in placed_nodes]

    if all_xs and all_ys:
        layout_w = max(x + w for x, w in zip(all_xs, widths)) - min(all_xs)
        layout_h = max(y + h for y, h in zip(all_ys, heights)) - min(all_ys)
        aspect = layout_w / layout_h if layout_h > 0 else 0

        n_rows = len(set(round(y, 3) for y in all_ys))
        n_nmos_rows = len(set(round(y, 3) for y in nmos_ys)) if nmos_ys else 0
        n_pmos_rows = len(set(round(y, 3) for y in pmos_ys)) if pmos_ys else 0

        _print(f"\n[MultiAgent] === PLACEMENT QUALITY REPORT ===")
        _print(f"[MultiAgent]   Layout Size  : {layout_w:.3f}um x {layout_h:.3f}um")
        _print(f"[MultiAgent]   Aspect Ratio : {aspect:.2f} (target: 1.0)")
        _print(f"[MultiAgent]   Rows         : {n_rows} total ({n_nmos_rows} NMOS + {n_pmos_rows} PMOS)")
        _print(f"[MultiAgent]   Devices      : {len(placed_nodes)} placed")
        if aspect > 0.7 and aspect < 1.4:
            _print(f"[MultiAgent]   Shape        : [OK] Near-square")
        elif aspect >= 1.4:
            _print(f"[MultiAgent]   Shape        : [!!] Wide - consider more rows")
        else:
            _print(f"[MultiAgent]   Shape        : [!!] Tall - consider fewer rows")
        _print(f"[MultiAgent] =================================")

    # ── Write output ────────────────────────────────────────────────
    output = dict(graph_data)
    output["nodes"] = placed_nodes
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=4)

    _print(f"[MultiAgent] ═══ Complete — {len(placed_nodes)} devices placed "
          f"→ {output_json_path} ═══\n")


# ── CLI smoke-test ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3:
        multi_agent_generate_placement(
            input_json_path=sys.argv[1],
            output_json_path=sys.argv[2],
            selected_model=sys.argv[3] if len(sys.argv) > 3 else "Gemini",
        )
    else:
        _print("Usage: python multi_agent_placer.py <input.json> <output.json> [model]")

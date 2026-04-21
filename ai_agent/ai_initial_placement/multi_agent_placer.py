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
from collections import defaultdict
from typing import Optional

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

# -------------------------------------------------------------------
# Physical layout constants
# -------------------------------------------------------------------
ROW_PITCH    = 0.668   # µm  — row-to-row pitch
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
            print(f"[MultiAgent] Gemini → {stage_key}: model='{model_name}'")
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
            print(f"[MultiAgent] Alibaba → {stage_key}: model='{model_name}'")
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
            print(f"[MultiAgent] VertexGemini → {stage_key}: model='{model_name}', location='{location}'")
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
            print(f"[MultiAgent] VertexClaude → {stage_key}: model='{model_name}'")
            resp = client.messages.create(
                model=model_name,
                max_tokens=8192,
                messages=[{"role": "user", "content": prompt}],
            )
            return (resp.content[0].text or "").strip()

        else:
            print(f"{tag} WARNING: Unknown provider '{selected_model}', falling back to Gemini.")
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
        print(f"{tag} LLM error (non-fatal): {exc}")
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
    print("[MultiAgent] Stage 1/4: Topology Analyst…")

    safe_tn = terminal_nets if isinstance(terminal_nets, dict) else {}
    gate_groups:   dict = defaultdict(list)
    drain_groups:  dict = defaultdict(list)
    source_groups: dict = defaultdict(list)

    for node in nodes:
        dev_id  = str(node.get("id", ""))
        nets    = safe_tn.get(dev_id) or safe_tn.get(re.sub(r'_[mf]\d+$', '', dev_id), {})
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

    # Electrical parameters per device
    for n in sorted(nodes, key=lambda x: x.get("id", "")):
        e = n.get("electrical", {})
        lines.append(
            f"  {n['id']:12s}  type={n.get('type','?'):5s}  "
            f"m={e.get('m',1)}  nf={e.get('nf',1)}  "
            f"nfin={e.get('nfin',1)}  l={e.get('l','?')}"
        )

    lines.append("\n=== CONNECTIVITY GROUPS ===")

    # Shared-gate → current mirrors or diff-pair loads
    for net, devs in sorted(gate_groups.items()):
        if len(devs) >= 2:
            types = {node.get("type") for node in nodes if node.get("id") in devs}
            tag   = "DIFF-PAIR?" if len(types) > 1 else "MIRROR/MATCHED"
            lines.append(f"  shared-gate  [{net}] ({tag}): {' ↔ '.join(devs)}")

    # Shared-drain → cascode / folded connections
    for net, devs in sorted(drain_groups.items()):
        if len(devs) >= 2:
            lines.append(f"  shared-drain [{net}]: {' ↔ '.join(devs)}")

    # Shared-source → tail / bias chains
    for net, devs in sorted(source_groups.items()):
        if len(devs) >= 2:
            lines.append(f"  shared-src   [{net}]: {' ↔ '.join(devs)}")

    constraint_text = "\n".join(lines)

    # ── LLM enrichment ───────────────────────────────────────────
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

    n_mirr  = sum(1 for devs in gate_groups.values()  if len(devs) >= 2)
    n_casc  = sum(1 for devs in drain_groups.values() if len(devs) >= 2)
    print(f"[MultiAgent]   {len(pmos)} PMOS  {len(nmos)} NMOS  "
          f"| {n_mirr} mirror/matched group(s)  {n_casc} cascode group(s)")
    return constraint_text


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
    import math
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

    print(f"[MultiAgent]   Square-ratio target: {target_nmos_rows} NMOS rows × "
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
        Abutted device ID pairs in directed order (a abuts right → b).
    """
    pairs: set = set()
    for c in (candidates or []):
        pairs.add((str(c.get("dev_a", "")), str(c.get("dev_b", ""))))
    for n in nodes:
        abut = n.get("abutment", {})
        nid  = str(n.get("id", ""))
        if abut.get("abut_right"):
            for m in nodes:
                mid = str(m.get("id", ""))
                if m.get("abutment", {}).get("abut_left") and mid != nid:
                    pairs.add((nid, mid))
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
            print(f"[MultiAgent][geo] WARNING: '{dev_id}' not in node_map — skipping")
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
    # PMOS rows start one extra ROW_PITCH above the top NMOS row
    # to guarantee a clear vertical gap and prevent visual overlap.
    n_nmos = len(nmos_rows)
    n_pmos = len(pmos_rows)
    nmos_ys = [round(i * ROW_PITCH, 6) for i in range(n_nmos)]
    pmos_base = (n_nmos + 1) * ROW_PITCH   # +1 for PMOS/NMOS gap
    pmos_ys   = [round(pmos_base + j * ROW_PITCH, 6) for j in range(n_pmos)]

    placed_ids: set  = set()
    placed_nodes: list = []

    # ── Place NMOS rows ─────────────────────────────────────────────
    for row_idx, row in enumerate(nmos_rows):
        y       = nmos_ys[row_idx]
        devices = [d for d in row.get("devices", []) if d not in placed_ids]
        label   = row.get("label", f"nmos_row_{row_idx}")
        print(f"[MultiAgent]   NMOS row {row_idx} [{label}]  y={y:.3f}  "
              f"{len(devices)} device(s)")
        row_nodes = _place_row(devices, y, node_map, abut_pairs)
        placed_nodes.extend(row_nodes)
        placed_ids.update(n["id"] for n in row_nodes)

    # ── Place PMOS rows ─────────────────────────────────────────────
    for row_idx, row in enumerate(pmos_rows):
        y       = pmos_ys[row_idx]
        devices = [d for d in row.get("devices", []) if d not in placed_ids]
        label   = row.get("label", f"pmos_row_{row_idx}")
        print(f"[MultiAgent]   PMOS row {row_idx} [{label}]  y={y:.3f}  "
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
            y = pmos_ys[-1] if pmos_ys else ROW_PITCH
        else:
            # passive (res/cap) — dedicated row above everything
            y = round((n_nmos + n_pmos) * ROW_PITCH + ROW_PITCH, 6)

        # Find leftmost free x in that row
        used_x = {round(p["geometry"]["x"], 6)
                  for p in placed_nodes
                  if round(p.get("geometry", {}).get("y", -999), 6) == y}
        x = 0.0
        while round(x, 6) in used_x:
            x = round(x + STD_PITCH, 6)

        orphan = copy.deepcopy(n)
        geo    = orphan.setdefault("geometry", {})
        geo["x"] = round(x, 6)
        geo["y"] = y
        geo.setdefault("orientation", "R0")
        orphan["abutment"] = {"abut_left": False, "abut_right": False}
        placed_nodes.append(orphan)
        placed_ids.add(nid)
        print(f"[MultiAgent]   Orphan '{nid}' ({dev_type}) → ({x:.3f}, {y:.3f})")

    # ── Center all rows for symmetric layout ──────────────────────
    # Find the max row width, then shift each row so it's centered.
    row_nodes_by_y: dict = defaultdict(list)
    for p in placed_nodes:
        ry = round(float(p.get("geometry", {}).get("y", 0.0)), 6)
        row_nodes_by_y[ry].append(p)

    global_max_x = 0.0
    for ry, rnodes in row_nodes_by_y.items():
        if rnodes:
            row_max = max(float(n["geometry"]["x"]) for n in rnodes)
            global_max_x = max(global_max_x, row_max)

    if global_max_x > 0:
        for ry, rnodes in row_nodes_by_y.items():
            if not rnodes:
                continue
            row_max = max(float(n["geometry"]["x"]) for n in rnodes)
            shift = round((global_max_x - row_max) / 2.0, 6)
            if shift > 0:
                for n in rnodes:
                    n["geometry"]["x"] = round(float(n["geometry"]["x"]) + shift, 6)

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

    # 1. Coverage
    missing = orig_ids - placed_ids
    if missing:
        errors.append(f"MISSING devices: {sorted(missing)}")

    # 2. Same-row overlap (distance-based, not slot-based)
    MIN_SPACING = ABUT_SPACING * 0.9  # ~0.063 µm — anything closer is a collision
    row_devs: dict = defaultdict(list)
    for p in placed:
        if not isinstance(p, dict):
            continue
        geo = p.get("geometry", {})
        x   = float(geo.get("x", 0.0))
        y   = round(float(geo.get("y", 0.0)), 3)
        row_devs[y].append((x, p.get("id", "?")))

    for y, devs in row_devs.items():
        devs_sorted = sorted(devs, key=lambda d: d[0])
        for i in range(len(devs_sorted) - 1):
            x_a, id_a = devs_sorted[i]
            x_b, id_b = devs_sorted[i + 1]
            if abs(x_b - x_a) < MIN_SPACING:
                errors.append(
                    f"X-COLLISION in row y={y:.3f}: '{id_a}' and '{id_b}' "
                    f"only {abs(x_b - x_a):.4f}µm apart (min={MIN_SPACING:.4f}µm)"
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
    print("[MultiAgent] Stage 2/4: Placement Specialist (multi-row)…")

    abutment_str = _format_abutment_candidates(abutment_candidates)
    prompt       = _build_multirow_prompt(nodes, edges, graph_data,
                                          abutment_str, constraint_text)

    expected_nmos = {n["id"] for n in nodes if n.get("type") == "nmos"}
    expected_pmos = {n["id"] for n in nodes if n.get("type") == "pmos"}

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"[MultiAgent]   Placement attempt {attempt}/{MAX_RETRIES}…")
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
            print(f"[MultiAgent]   Auto-appended NMOS: {missing_nmos}")
        if missing_pmos:
            pmos_rows.append({"label": "misc_pmos", "devices": missing_pmos})
            print(f"[MultiAgent]   Auto-appended PMOS: {missing_pmos}")

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
        print(f"[MultiAgent]   ✓ Placed {len(placed)} device(s) in "
              f"{n_nmos_rows} NMOS row(s) + {n_pmos_rows} PMOS row(s).")
        return placed

    # ── Deterministic fallback ─────────────────────────────────────
    print(f"[MultiAgent]   All LLM attempts failed ({last_error}). "
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
    print(f"[MultiAgent]   Deterministic fallback: {len(nmos_rows)} NMOS row(s) + "
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
    print("[MultiAgent] Stage 3/4: DRC & Healing…")

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
        cursor = 0.0
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

    print("[MultiAgent]   DRC healing complete.")
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
        print("[MultiAgent] Running SA Post-Optimisation…")
        result = optimize_placement(nodes, edges, abutment_candidates=abutment_candidates)
        print("[MultiAgent] SA complete.")
        return result
    except Exception as sa_err:
        print(f"[MultiAgent] SA failed (non-fatal): {sa_err}")
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
        Run Simulated Annealing post-pass. Default: False.

    Raises
    ------
    ValueError
        If the input JSON contains no device nodes.
    FileNotFoundError
        If ``input_json_path`` does not exist.
    """
    print(f"\n[MultiAgent] ═══ Autonomous Multi-Row Placement — {selected_model} ═══")

    # ── Load ────────────────────────────────────────────────────────
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
    print(f"[MultiAgent] Loaded {len(nodes)} devices ({n_pmos} PMOS, {n_nmos} NMOS), "
          f"{len(edges)} edges.")

    # ── Normalise Y-coordinates (same as all working placers) ───────
    norm_nodes, y_offset = _normalise_coords(nodes)
    if abs(y_offset) > 1e-9:
        print(f"[MultiAgent] Y-offset applied: {y_offset:+.4f} µm")

    prompt_graph        = dict(graph_data)
    prompt_graph["nodes"] = norm_nodes

    # ── Stage 1/4: Topology Analyst (flash — fast analysis) ─────────
    print(f"[MultiAgent] Model assignment per stage:")
    if selected_model == "VertexGemini":
        for k, (m, loc) in _VERTEX_MODELS.items():
            if k == "default": continue
            print(f"[MultiAgent]   {k:20s} → {m} (location={loc or 'default'})")
    elif selected_model == "Gemini":
        for k, m in _GEMINI_MODELS.items():
            if k == "default": continue
            print(f"[MultiAgent]   {k:20s} → {m}")
    elif selected_model == "Alibaba":
        for k, m in _ALIBABA_MODELS.items():
            if k == "default": continue
            print(f"[MultiAgent]   {k:20s} → {m}")

    constraint_text = _stage_topology(
        norm_nodes, terminal_nets, edges, selected_model, "light"
    )

    # ── Stage 2/4: Multi-Row Placement Specialist ──────────────────
    placed_nodes = _stage_placement(
        norm_nodes, edges, prompt_graph,
        abutment_candidates, constraint_text,
        selected_model, "heavy",
    )

    # ── Stage 3/4: DRC & Abutment Healing ──────────────────────────
    placed_nodes = _stage_drc_and_heal(placed_nodes, abutment_candidates, no_abutment)

    # ── Stage 4/4: SA Post-Optimisation (optional) ─────────────────

    if run_sa:
        placed_nodes = _run_sa(placed_nodes, edges, abutment_candidates)

    # ── Restore original Y-coordinate frame ────────────────────────
    placed_nodes = _restore_coords(placed_nodes, y_offset)

    # ── Validate PMOS/NMOS separation in final output ───────────────
    orig_type = {n["id"]: n.get("type", "?") for n in nodes}
    pmos_ys   = [round(float(p.get("geometry", {}).get("y", 0)), 4)
                 for p in placed_nodes if orig_type.get(p.get("id", "")) == "pmos"]
    nmos_ys   = [round(float(p.get("geometry", {}).get("y", 0)), 4)
                 for p in placed_nodes if orig_type.get(p.get("id", "")) == "nmos"]
    if pmos_ys and nmos_ys:
        if min(pmos_ys) <= max(nmos_ys):
            print(f"[MultiAgent] ⚠  PMOS/NMOS overlap detected in final output "
                  f"(min PMOS y={min(pmos_ys):.4f} ≤ max NMOS y={max(nmos_ys):.4f}). "
                  "Check DRC after import.")
        else:
            print(f"[MultiAgent] ✓  PMOS/NMOS separation OK "
                  f"(PMOS ≥ {min(pmos_ys):.4f} > NMOS ≤ {max(nmos_ys):.4f})")

    # ── Write output ────────────────────────────────────────────────
    output = dict(graph_data)
    output["nodes"] = placed_nodes
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=4)

    print(f"[MultiAgent] ═══ Complete — {len(placed_nodes)} devices placed "
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
        print("Usage: python multi_agent_placer.py <input.json> <output.json> [model]")

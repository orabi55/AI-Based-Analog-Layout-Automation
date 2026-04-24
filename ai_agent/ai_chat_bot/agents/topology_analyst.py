"""
ai_agent/ai_chat_bot/agents/topology_analyst.py
=============================
Topology Analyst Agent
======================
Identifies placement constraints from SPICE netlist topology:
  - shared_gate  -> mirror/cascode candidates -> must stay adjacent
  - shared_drain -> differential-pair loads -> symmetry required
  - shared_source -> bias-current mirrors -> close grouping preferred

Domain helper: analyze_topology() - pure Python, no LLM needed.

"""

import os
import sys
from collections import defaultdict
from math import gcd
from typing import Dict, List, Optional, Tuple

from ai_agent.ai_chat_bot.analog_kb import ANALOG_LAYOUT_RULES


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
TOPOLOGY_ANALYST_PROMPT = """\
You are the TOPOLOGY ANALYST agent in a multi-agent analog IC layout system.

Your task is to analyze the given circuit netlist (devices and connections) and extract
clear, structured, and device-centric topology information for downstream agents.

Your output will be used directly by floorplanning agents, so it must be precise and unambiguous.

--------------------------------------------------
OBJECTIVES
--------------------------------------------------

1) Identify all fundamental circuit topologies present.

Examples include:
- Differential pair
- Current mirror (simple, cascode, Wilson)
- Cascode structures
- Active loads
- Bias networks
- Logic gates (CMOS)
- Gain stages

2) Assign EVERY device to exactly ONE PRIMARY group.

- Each device must appear in EXACTLY ONE primary group.
- A primary group represents the device's MAIN functional role in the circuit.
- Devices that must be placed together (for matching/symmetry) should be in the same group.

2.5) Placement Cohesion
- Each PRIMARY group should represent a set of devices that are expected to be placed together.
- Groups should align with physical layout units (e.g., diff pair, mirror, load block).

3) Assign OPTIONAL secondary tags (non-exclusive).

- A device may have ZERO or MORE secondary tags.
- Tags describe additional roles or relationships but do NOT affect grouping.
- Examples:
  - part_of_current_mirror
  - bias_related
  - cascode_device
  - load_device

4) Define functional roles inside each group.

For each device, clearly specify its role:
- Input transistor
- Load transistor
- Tail current source
- Reference device
- Output device
- Bias device
- Cascode device

5) Identify matching and symmetry requirements.

Explicitly state:
- Which devices must be matched
- Which devices require symmetric placement
- Any arrays or pairs

6) Identify the overall circuit function.

Examples:
- Differential amplifier
- Comparator
- Current reference
- Logic gate
- Multi-stage amplifier

--------------------------------------------------
DIFF PAIR & CURRENT MIRROR DETECTION (CRITICAL)
--------------------------------------------------

You MUST detect the following patterns and classify them precisely:

DIFFERENTIAL PAIR detection rules:
  - Two same-type transistors (both NMOS or both PMOS)
  - Connected to the SAME gate net (input+, input-)  OR
  - Connected to the same source net (tail current)
  - Drains go to different signal nets (output+, output-)
  - These devices MUST be marked as a DIFF_PAIR group
  - Matching technique: COMMON_CENTROID_1D (for 1-row) or COMMON_CENTROID_2D (for 2-row)

CURRENT MIRROR detection rules:
  - Two or more same-type transistors sharing the SAME gate net
  - One device has its gate tied to its drain (diode-connected = reference)
  - The other device(s) copy the reference current
  - These devices MUST be marked as a CURRENT_MIRROR group
  - Matching technique: INTERDIGITATION

CASCODE detection rules:
  - Two same-type transistors stacked: drain of bottom -> source of top
  - Gate of top is typically a bias voltage
  - Mark as CASCODE group

--------------------------------------------------
CRITICAL RULES
--------------------------------------------------

- Use EXACT device names from the input (no renaming).
- Each device must appear in EXACTLY ONE PRIMARY group.
- Devices MAY appear in multiple secondary tags.
- Do NOT leave any device unassigned.
- Groups must reflect real electrical relationships.
- Be explicit about matching and symmetry (critical for layout).
- Matching and symmetry requirements should be defined WITHIN groups.
- Avoid defining critical matching relationships across different primary groups.
- Avoid vague or generic descriptions.

--------------------------------------------------
OUTPUT FORMAT (STRICT)
--------------------------------------------------

CIRCUIT_TYPE:
[One line: overall function of the circuit]

TOPOLOGY_GROUPS:

[GROUP_NAME_1]
Type: [e.g., Differential Pair / Current Mirror / Cascode]
Devices: [D1, D2, D3, ...]
Roles:
  - D1: [role]
  - D2: [role]
Secondary_Tags:
  - D1: [tag1, tag2, ...] (or NONE)
  - D2: [tag1, ...] (or NONE)
Matching_Requirements:
  - [e.g., D1 <-> D2 must be matched]
Symmetry:
  - [e.g., D1 and D2 must be placed symmetrically]

[GROUP_NAME_2]
Type: [...]
Devices: [...]
Roles:
  - ...
Secondary_Tags:
  - ...
Matching_Requirements:
  - ...
Symmetry:
  - ...

(repeat until ALL devices are assigned)

--------------------------------------------------
MATCHING GROUPS JSON (MANDATORY — append after text output)
--------------------------------------------------

After your text output, you MUST append a JSON block specifying which device
groups need deterministic matching. This JSON is consumed by the matching engine.

Available techniques:
  - "COMMON_CENTROID_1D"  — for diff pairs (centroid-matched in 1 row)
  - "COMMON_CENTROID_2D"  — for diff pairs (centroid-matched across 2 rows)
  - "INTERDIGITATION"     — for current mirrors (ratio-proportional interleaving)

```json
{
  "match_groups": [
    {
      "devices": ["MM0", "MM1"],
      "technique": "COMMON_CENTROID_1D",
      "reason": "Differential pair — must be centroid-matched"
    },
    {
      "devices": ["MM3", "MM4"],
      "technique": "INTERDIGITATION",
      "reason": "Current mirror — ratio-preserving interleaving"
    }
  ]
}
```

Rules for match_groups:
  - Use PARENT device names (MM0, not MM0_f1)
  - Only include groups that genuinely need matching (diff pairs, mirrors)
  - Do NOT include cascode or simple bias devices unless they form a mirror
  - If no matching is needed, output: {"match_groups": []}

--------------------------------------------------
FINAL CHECK (MANDATORY)
--------------------------------------------------

- Every device is assigned to exactly ONE primary group
- No device is repeated across primary groups
- Secondary tags do NOT violate primary grouping
- Matching and symmetry are clearly identified
- Output strictly follows the required format
- match_groups JSON is present and valid

If any rule is violated, regenerate the output.
"""


def analyze_json(nodes: List[dict], terminal_nets: dict) -> str:
    """
    Convert raw layout JSON structures (nodes + terminal_nets) into a clear,
    prompt-ready text snapshot.

    IMPORTANT: This function intentionally parses inputs directly and does
    not call helper functions from this module.

    Args:
        nodes: Device list from layout JSON/canvas.
        terminal_nets: Mapping device_id -> {"D": ..., "G": ..., "S": ...}.

    Returns:
        Multi-line string suitable for LLM/system prompts.
    """
    safe_terminal_nets = terminal_nets if isinstance(terminal_nets, dict) else {}
    safe_nodes = nodes if isinstance(nodes, list) else []

    def _lookup_nets(dev_key: str) -> dict:
        for key in (dev_key, dev_key.upper(), dev_key.lower()):
            value = safe_terminal_nets.get(key)
            if isinstance(value, dict):
                return value
        return {}

    def _resolve_node_nets(node: dict) -> dict:
        dev_id = str(node.get("id", ""))
        if not dev_id:
            return {}

        # Direct net mapping (physical or already-aggregated logical net entry)
        direct = _lookup_nets(dev_id)
        if direct:
            return direct

        # Logical-node fallback: aggregate per-pin nets from all fingers.
        finger_ids = node.get("_fingers", [])
        if not isinstance(finger_ids, list) or not finger_ids:
            return {}

        merged = {"D": set(), "G": set(), "S": set()}
        for fid in finger_ids:
            if not fid:
                continue
            fnets = _lookup_nets(str(fid))
            if not fnets:
                continue
            for pin in ("D", "G", "S"):
                net = fnets.get(pin)
                if net not in (None, ""):
                    merged[pin].add(str(net))

        resolved = {}
        for pin, values in merged.items():
            if len(values) == 1:
                resolved[pin] = next(iter(values))
            elif len(values) > 1:
                # Keep conflicts visible for debug/readability in summaries.
                resolved[pin] = "|".join(sorted(values))
        return resolved

    lines: List[str] = []
    lines.append("=== LAYOUT JSON SUMMARY ===")
    lines.append(
        f"Devices: physical={len(safe_nodes)}, "
        f"terminal_nets={len(safe_terminal_nets)}"
    )

    pmos_count = sum(
        1 for n in safe_nodes
        if str(n.get("type", "")).lower().startswith("p") and not n.get("is_dummy")
    )
    nmos_count = sum(
        1 for n in safe_nodes
        if str(n.get("type", "")).lower().startswith("n") and not n.get("is_dummy")
    )
    dummy_count = sum(1 for n in safe_nodes if n.get("is_dummy"))
    lines.append(f"Types: PMOS={pmos_count}, NMOS={nmos_count}, DUMMY={dummy_count}")
    lines.append("")

    lines.append("=== DEVICES ===")
    for node in safe_nodes:
        dev_id = str(node.get("id", "?"))
        dev_type = str(node.get("type", "unknown"))
        geo = node.get("geometry", {}) if isinstance(node.get("geometry", {}), dict) else {}
        elec = node.get("electrical", {}) if isinstance(node.get("electrical", {}), dict) else {}

        x_val = geo.get("x", "?")
        y_val = geo.get("y", "?")
        try:
            x_text = f"{float(x_val):.3f}"
        except (TypeError, ValueError):
            x_text = str(x_val)
        try:
            y_text = f"{float(y_val):.3f}"
        except (TypeError, ValueError):
            y_text = str(y_val)

        nf = elec.get("nf", "?")
        nfin = elec.get("nfin", "?")

        nets = _resolve_node_nets(node)
        g_net = nets.get("G", "?")
        d_net = nets.get("D", "?")
        s_net = nets.get("S", "?")

        dummy_tag = " dummy" if node.get("is_dummy") else ""

        lines.append(
            f"- {dev_id} ({dev_type}{dummy_tag}) "
            f"pos=({x_text},{y_text}) nf={nf} nfin={nfin} "
            f"D={d_net} G={g_net} S={s_net}"
        )

    if not safe_nodes:
        lines.append("- No devices found")

    lines.append("")
    lines.append("=== CONNECTIVITY GROUPS ===")

    gate_groups: Dict[str, List[str]] = defaultdict(list)
    drain_groups: Dict[str, List[str]] = defaultdict(list)
    source_groups: Dict[str, List[str]] = defaultdict(list)

    for node in safe_nodes:
        dev_id = str(node.get("id", ""))
        if not dev_id:
            continue

        nets = _resolve_node_nets(node)

        g_net = str(nets.get("G", "")).upper()
        d_net = str(nets.get("D", "")).upper()
        s_net = str(nets.get("S", "")).upper()

        if g_net:
            gate_groups[g_net].append(dev_id)
        if d_net:
            drain_groups[d_net].append(dev_id)
        if s_net:
            source_groups[s_net].append(dev_id)

    any_group = False
    for net, devs in sorted(gate_groups.items()):
        if len(devs) >= 2:
            any_group = True
            lines.append(f"- shared-gate {net}: " + " <-> ".join(devs))

    for net, devs in sorted(drain_groups.items()):
        if len(devs) >= 2:
            any_group = True
            lines.append(f"- shared-drain {net}: " + " <-> ".join(devs))

    for net, devs in sorted(source_groups.items()):
        if len(devs) >= 2:
            any_group = True
            lines.append(f"- shared-source {net}: " + " <-> ".join(devs))

    if not any_group:
        lines.append("- No shared gate/drain/source groups found")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Abutment candidate extraction
# ---------------------------------------------------------------------------

_POWER_NETS = frozenset({"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS"})


def build_abutment_candidates(nodes: List[dict], terminal_nets: dict = None) -> List[dict]:
    """
    Extract abutment pair candidates from device flags and shared source nets.

    An abutment pair is two adjacent devices that share a source/drain
    diffusion. Identifying them early lets the geometry engine and SA
    optimizer use the correct 0.070µm pitch (vs 0.294µm for standalone).

    Sources of abutment info (in priority order):
      1. Explicit ``node["abutment"]["abut_right/left"]`` flags set by the
         layout extractor.
      2. Shared source nets — two devices on the same source net that are
         also the same type (both NMOS or both PMOS) are likely abutted.

    Parameters
    ----------
    nodes         : list of device node dicts
    terminal_nets : {device_id: {"D": net, "G": net, "S": net}}

    Returns
    -------
    List of {"dev_a": str, "dev_b": str, "shared_net": str} dicts.
    """
    safe_tn = terminal_nets if isinstance(terminal_nets, dict) else {}
    candidates: List[dict] = []
    seen: set = set()

    # ── Source 1: embedded abutment flags ─────────────────────────────
    # Build a {y: [sorted_by_x nodes]} map so we can find adjacent pairs
    rows: Dict[float, list] = defaultdict(list)
    for n in nodes:
        geo = n.get("geometry", {})
        y   = round(float(geo.get("y", 0.0)), 3)
        rows[y].append(n)

    for y_val, row_nodes in rows.items():
        sorted_row = sorted(row_nodes, key=lambda n: n.get("geometry", {}).get("x", 0.0))
        for i in range(len(sorted_row) - 1):
            n1 = sorted_row[i]
            n2 = sorted_row[i + 1]
            if (
                n1.get("abutment", {}).get("abut_right")
                and n2.get("abutment", {}).get("abut_left")
            ):
                key = (n1["id"], n2["id"])
                if key not in seen:
                    seen.add(key)
                    candidates.append({
                        "dev_a": n1["id"],
                        "dev_b": n2["id"],
                        "shared_net": "abutment_flag",
                    })

    # ── Source 2: shared source nets (same type devices) ──────────────
    source_net_devs: Dict[str, List[dict]] = defaultdict(list)
    for n in nodes:
        nid  = n.get("id", "")
        nets = safe_tn.get(nid) or {}
        snet = str(nets.get("S", "")).strip().upper()
        if snet and snet not in _POWER_NETS:
            source_net_devs[snet].append(n)

    for snet, devs in source_net_devs.items():
        # Only consider pairs within same type
        for i in range(len(devs)):
            for j in range(i + 1, len(devs)):
                a, b = devs[i], devs[j]
                if a.get("type") != b.get("type"):
                    continue
                key = (a["id"], b["id"])
                if key not in seen:
                    seen.add(key)
                    candidates.append({
                        "dev_a": a["id"],
                        "dev_b": b["id"],
                        "shared_net": snet,
                    })

    if candidates:
        print(f"[TopoAnalyst] Extracted {len(candidates)} abutment candidate(s)")
    return candidates

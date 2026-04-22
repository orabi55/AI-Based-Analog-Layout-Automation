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
- A primary group represents the device’s MAIN functional role in the circuit.
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
  - [e.g., D1 ↔ D2 must be matched]
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
FINAL CHECK (MANDATORY)
--------------------------------------------------

- Every device is assigned to exactly ONE primary group
- No device is repeated across primary groups
- Secondary tags do NOT violate primary grouping
- Matching and symmetry are clearly identified
- Output strictly follows the required format

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


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

from ai_agent.knowledge.analog_rules import ANALOG_LAYOUT_RULES


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
TOPOLOGY_ANALYST_PROMPT = """\
You are the TOPOLOGY ANALYST agent in a multi-agent analog IC layout system.

Your task:
Analyze the circuit netlist (devices and connections) and extract precise, structured, device-centric topology information for downstream floorplanning agents.

Your output must be strict, unambiguous, and directly usable.

ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
1. OBJECTIVES
ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

1) Identify fundamental circuit topologies:

Examples:
- Differential pair
- Current mirror (simple / cascode / Wilson)
- Cascode structures
- Active loads
- Bias networks
- CMOS logic gates
- Gain stages

2) Assign EVERY device to EXACTLY ONE PRIMARY GROUP:

- Each device must appear in exactly one primary group
- Each group represents a single main functional role set
- Devices requiring tight placement/matching should be grouped together

Placement Cohesion Rule:
- Each primary group must correspond to a physically placeable layout block
- Groups should map to real structural units (diff pair, mirror, load, etc.)

ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
2. SECONDARY TAGS (OPTIONAL)
ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

Devices may have zero or more secondary tags.

Controlled SKILL_HINT vocabulary only:

- SKILL_HINT:bias_chain        ΓåÆ vertical current dependency chain
- SKILL_HINT:common_centroid   ΓåÆ gradient-canceling centroid requirement
- SKILL_HINT:bias_mirror       ΓåÆ mirrored current mirror structure
- SKILL_HINT:differential_pair ΓåÆ half of a differential pair
- SKILL_HINT:interdigitate     ΓåÆ ratio-matching via interdigitation
- SKILL_HINT:proximity_net     ΓåÆ high-connectivity locality requirement

Rules:
- Tags do NOT affect grouping
- Multiple tags allowed per device
- Only controlled vocabulary allowed

ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
3. DEVICE ROLE CLASSIFICATION
ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

For each device, specify:

- Role:
  (Input / Load / Tail current source / Reference / Output / Bias / Cascode)

- Type:
  NMOS or PMOS (must be exact)

- nf:
  integer ΓëÑ 1

Rules:
- nf must be read from input netlist
- If missing ΓåÆ nf = 1 and mark as (assumed)

ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
4. MATCHING & SYMMETRY RULES
ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

You must explicitly define:

- Devices requiring matching
- Symmetry relationships
- Device arrays or pairs

Critical rule:
- Matching and symmetry must be defined WITHIN groups
- Do NOT define primary matching relationships across groups unless unavoidable

ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
5. CIRCUIT FUNCTION IDENTIFICATION
ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

Identify overall circuit type:

Examples:
- Differential amplifier
- Comparator
- Current reference
- Logic gate
- Multi-stage amplifier

ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
6. CRITICAL RULES
ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

- Use EXACT device names (no renaming)
- Each device must appear in exactly ONE primary group
- No unassigned devices allowed
- Groups must reflect real electrical structure
- Be explicit about matching and symmetry (critical)
- Secondary tags must only use SKILL_HINT vocabulary
- Devices may have multiple secondary tags

ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
7. CURRENT_FLOW_GRAPH RULES
ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

- Must be derived from:
  current mirrors, cascodes, tail sources

- Format:
  A ΓåÆ B means A provides bias current to B

- Must use exact device names

- Graph must be acyclic

If cycle detected:
ΓåÆ report topology error

ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
8. NETLIST_GRAPH RULES
ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

Undirected weighted connectivity:

Format:
- A ΓÇö B : net_name : HIGH|MEDIUM|LOW

Weight rules:
- Differential nets = HIGH
- Bias nodes = MEDIUM
- Supply/ground = LOW

If no meaningful connections:
ΓåÆ write NONE

ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
9. OUTPUT FORMAT (STRICT)
ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

CIRCUIT_TYPE:
[one-line circuit function]

TOPOLOGY_GROUPS:

[GROUP_NAME]
Type: [...]
Devices: [D1, D2, ...]
Roles:
    - D1: [role] | Type: NMOS|PMOS | nf: [int]
    - D2: [role] | Type: NMOS|PMOS | nf: [int]

Secondary_Tags:
    - D1: [SKILL_HINT:...] or NONE
    - D2: [SKILL_HINT:...] or NONE

Matching_Requirements:
    - [...]

Symmetry:
    - [...]

PAIR_MAPPING: (ONLY if Differential Pair, else NONE)
    - (D+, D-)

(repeat for all groups)

ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
CURRENT_FLOW_GRAPH:
- A ΓåÆ B
- C ΓåÆ D
or NONE

ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
NETLIST_GRAPH:
- A ΓÇö B : net : HIGH|MEDIUM|LOW
or NONE

ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
10. FINAL VALIDATION (MANDATORY)
ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

Before output ensure:

Γ£ô Every device assigned exactly once
Γ£ô No duplicate group membership
Γ£ô All roles include Type + nf
Γ£ô Matching and symmetry clearly defined
Γ£ô Output follows strict format
Γ£ô Graphs are valid and acyclic
Γ£ô No missing devices

If any rule is violated:
ΓåÆ regenerate output
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

    # ── Append machine-readable [SYMMETRY] block ─────────────────────────
    sym_block = extract_symmetry_block(safe_nodes, safe_terminal_nets)
    if sym_block:
        lines.append("")
        lines.append(sym_block)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pure-Python symmetry detector — produces [SYMMETRY] machine block
# ---------------------------------------------------------------------------
_POWER_NETS = {"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS", "VDDA", "VSSA"}


def extract_symmetry_block(nodes: List[dict], terminal_nets: dict) -> str:
    """
    Detect diff pairs, load mirrors, and tail current sources from the netlist
    and return a machine-readable [SYMMETRY] block string.

    Detection rules:
    - Diff pair (rank 1): two same-type devices sharing a source net NOT in
      _POWER_NETS.
    - Load mirror (rank 2): two same-type devices sharing a gate net where
      that gate net is NOT a power net AND not the same type as the diff pair
      (or is a different polarity row).
    - Axis device: a device whose drain net equals the diff pair's shared
      source net (i.e., the tail current source).

    Returns:
        A [SYMMETRY]...[/SYMMETRY] block string, or "" if no symmetry found.
    """
    if not nodes or not terminal_nets:
        return ""

    safe_tn = terminal_nets if isinstance(terminal_nets, dict) else {}

    def _nets(dev_id: str) -> dict:
        for key in (dev_id, dev_id.upper(), dev_id.lower()):
            v = safe_tn.get(key)
            if isinstance(v, dict):
                return v
        return {}

    def _net(dev_id: str, pin: str) -> str:
        return str(_nets(dev_id).get(pin, "")).upper().strip()

    # Build type-grouped maps
    by_type: Dict[str, List[str]] = {}  # type -> [dev_id, ...]
    for n in nodes:
        if n.get("is_dummy"):
            continue
        dev_id = str(n.get("id", ""))
        dtype = str(n.get("type", "")).lower()
        by_type.setdefault(dtype, []).append(dev_id)

    # ── Step 1: Find diff pair (shared source, non-power) ──
    diff_pairs: List[Tuple[str, str]] = []
    diff_source_nets: List[str] = []

    for dtype, devs in by_type.items():
        # Build source-net groups
        src_map: Dict[str, List[str]] = {}
        for did in devs:
            snet = _net(did, "S")
            if snet and snet not in _POWER_NETS:
                src_map.setdefault(snet, []).append(did)
        for snet, members in src_map.items():
            if len(members) == 2:
                diff_pairs.append((members[0], members[1]))
                diff_source_nets.append(snet)
            elif len(members) > 2:
                # Larger group: take the first two as the primary pair
                diff_pairs.append((members[0], members[1]))
                diff_source_nets.append(snet)

    if not diff_pairs:
        return ""  # No symmetry structure detected

    # ── Step 2: Find load mirrors (shared gate, non-power, different type) ──
    diff_pair_ids = {did for pair in diff_pairs for did in pair}
    diff_type = ""
    for n in nodes:
        if str(n.get("id", "")) in diff_pair_ids:
            diff_type = str(n.get("type", "")).lower()
            break

    load_pairs: List[Tuple[str, str]] = []
    for dtype, devs in by_type.items():
        if dtype == diff_type:
            continue  # Skip same type as diff pair for loads
        gate_map: Dict[str, List[str]] = {}
        for did in devs:
            gnet = _net(did, "G")
            if gnet and gnet not in _POWER_NETS:
                gate_map.setdefault(gnet, []).append(did)
        for gnet, members in gate_map.items():
            if len(members) == 2:
                load_pairs.append((members[0], members[1]))
            elif len(members) > 2:
                load_pairs.append((members[0], members[1]))

    # ── Step 3: Find tail/axis device (drain == diff-pair shared source) ──
    axis_devices: List[str] = []
    for snet in diff_source_nets:
        for n in nodes:
            if n.get("is_dummy"):
                continue
            did = str(n.get("id", ""))
            if did in diff_pair_ids:
                continue
            if _net(did, "D") == snet:
                axis_devices.append(did)

    # ── Step 4: Build block string ──
    block_lines = ["[SYMMETRY]", "mode=two_half axis_row=both"]
    for i, (left, right) in enumerate(diff_pairs, start=1):
        block_lines.append(f"pair={left},{right} rank={i}")
    for i, (left, right) in enumerate(load_pairs, start=len(diff_pairs) + 1):
        block_lines.append(f"pair={left},{right} rank={i}")
    for ax in axis_devices:
        block_lines.append(f"axis={ax}")
    block_lines.append("[/SYMMETRY]")

    return "\n".join(block_lines)


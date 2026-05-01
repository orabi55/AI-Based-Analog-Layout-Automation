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

────────────────────────────────────────────
1. OBJECTIVES
────────────────────────────────────────────

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

────────────────────────────────────────────
2. SECONDARY TAGS (OPTIONAL)
────────────────────────────────────────────

Devices may have zero or more secondary tags.

Controlled SKILL_HINT vocabulary only:

- SKILL_HINT:bias_chain        → vertical current dependency chain
- SKILL_HINT:common_centroid   → gradient-canceling centroid requirement
- SKILL_HINT:bias_mirror       → mirrored current mirror structure
- SKILL_HINT:differential_pair → half of a differential pair
- SKILL_HINT:interdigitate     → ratio-matching via interdigitation
- SKILL_HINT:proximity_net     → high-connectivity locality requirement

Rules:
- Tags do NOT affect grouping
- Multiple tags allowed per device
- Only controlled vocabulary allowed

────────────────────────────────────────────
3. DEVICE ROLE CLASSIFICATION
────────────────────────────────────────────

For each device, specify:

- Role:
  (Input / Load / Tail current source / Reference / Output / Bias / Cascode)

- Type:
  NMOS or PMOS (must be exact)

- nf:
  integer ≥ 1

Rules:
- nf must be read from input netlist
- If missing → nf = 1 and mark as (assumed)

────────────────────────────────────────────
4. MATCHING & SYMMETRY RULES
────────────────────────────────────────────

You must explicitly define:

- Devices requiring matching
- Symmetry relationships
- Device arrays or pairs

Critical rule:
- Matching and symmetry must be defined WITHIN groups
- Do NOT define primary matching relationships across groups unless unavoidable

────────────────────────────────────────────
5. CIRCUIT FUNCTION IDENTIFICATION
────────────────────────────────────────────

Identify overall circuit type:

Examples:
- Differential amplifier
- Comparator
- Current reference
- Logic gate
- Multi-stage amplifier

────────────────────────────────────────────
6. CRITICAL RULES
────────────────────────────────────────────

- Use EXACT device names (no renaming)
- Each device must appear in exactly ONE primary group
- No unassigned devices allowed
- Groups must reflect real electrical structure
- Be explicit about matching and symmetry (critical)
- Secondary tags must only use SKILL_HINT vocabulary
- Devices may have multiple secondary tags

────────────────────────────────────────────
7. CURRENT_FLOW_GRAPH RULES
────────────────────────────────────────────

- Must be derived from:
  current mirrors, cascodes, tail sources

- Format:
  A → B means A provides bias current to B

- Must use exact device names

- Graph must be acyclic

If cycle detected:
→ report topology error

────────────────────────────────────────────
8. NETLIST_GRAPH RULES
────────────────────────────────────────────

Undirected weighted connectivity:

Format:
- A — B : net_name : HIGH|MEDIUM|LOW

Weight rules:
- Differential nets = HIGH
- Bias nodes = MEDIUM
- Supply/ground = LOW

If no meaningful connections:
→ write NONE

────────────────────────────────────────────
9. OUTPUT FORMAT (STRICT)
────────────────────────────────────────────

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

────────────────────────────────────────────
CURRENT_FLOW_GRAPH:
- A → B
- C → D
or NONE

────────────────────────────────────────────
NETLIST_GRAPH:
- A — B : net : HIGH|MEDIUM|LOW
or NONE

────────────────────────────────────────────
10. FINAL VALIDATION (MANDATORY)
────────────────────────────────────────────

Before output ensure:

✓ Every device assigned exactly once
✓ No duplicate group membership
✓ All roles include Type + nf
✓ Matching and symmetry clearly defined
✓ Output follows strict format
✓ Graphs are valid and acyclic
✓ No missing devices

If any rule is violated:
→ regenerate output
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
        # Fallback: match on base prefix (e.g., MM5 -> MM5_m1/MM5_m2)
        if dev_key:
            base = dev_key.split("<", 1)[0].split("_", 1)[0]
            for key, value in safe_terminal_nets.items():
                if not isinstance(value, dict):
                    continue
                if str(key).startswith(base):
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

    def _extract_xy(node: dict) -> tuple:
        geo = node.get("geometry", {}) if isinstance(node.get("geometry", {}), dict) else {}
        x_val = geo.get("x")
        y_val = geo.get("y")

        if x_val is None:
            x_val = node.get("x")
        if y_val is None:
            y_val = node.get("y")

        # Fallback for logical nodes without geometry: use type defaults.
        if y_val is None:
            dev_type = str(node.get("type", "")).lower()
            try:
                from config.design_rules import PMOS_Y, NMOS_Y
                if dev_type.startswith("p"):
                    y_val = PMOS_Y
                elif dev_type.startswith("n"):
                    y_val = NMOS_Y
            except Exception:
                y_val = 0.0
        if x_val is None:
            x_val = 0.0

        return x_val, y_val

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
        x_val, y_val = _extract_xy(node)
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


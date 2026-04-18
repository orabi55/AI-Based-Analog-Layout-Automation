"""
ai_agent/topology_analyst.py
=============================
Topology Analyst Agent
======================
Identifies placement constraints from SPICE netlist topology:
  - shared_gate  -> mirror/cascode candidates -> must stay adjacent
  - shared_drain -> differential-pair loads -> symmetry required
  - shared_source -> bias-current mirrors -> close grouping preferred

Domain helper: analyze_topology() - pure Python, no LLM needed.

FIXES APPLIED:
  - Bug #2: _aggregate_terminal_nets now tries all finger ID formats
  - Bug #3: _try_graph_analysis returns [] on error (not error string)
  - Bug #4: _parse_spice_directly added as primary net source
  - Bug #RATIO: ratio direction fixed to output:reference convention
               gcd simplification now computes both numerator and denominator
  - Bug #NFIN: nfin is a FinFET process parameter (fins per finger).
               It does NOT change physical finger count. nfingers = nf ONLY.
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
You are an expert Analog IC Layout Engineer specialising in topology analysis.
You are part of a multi-agent team. Your goal is to analyze the structural representation 
of the following layout circuit and identify fundamental analog and digital building blocks.
State any possible topologies you can identify, and the reasoning behind them.

IDENTIFY THE CIRCUIT:
1) Read the device list and net connections below CAREFULLY.
2) Determine what kind of circuit this actually is by examining:
    - Which devices share gate/drain/source nets
    - The number of NMOS vs PMOS devices
    - Signal flow patterns (inputs -> logic -> outputs)
    - Any biasing structures 
3) State your conclusion about which topologies are present (e.g., current mirror, differential pair, cascode, logic gate, etc.)
4) For each identified topology, explain your reasoning based on the connectivity and device types.
5) Then identify the overall circuit function (e.g., amplifier, comparator, logic function) based on the topologies you found.
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


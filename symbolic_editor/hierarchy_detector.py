# -*- coding: utf-8 -*-
"""
hierarchy_detector.py
=====================
Automatic functional-group detection for analog IC layouts.

Detects standard topological patterns from terminal_nets + node types:
  - Precharge  : CLK-gated PMOS pulling to VDD
  - Latch_PMOS : cross-coupled PMOS (A.G=B.D, B.G=A.D)
  - TailClock  : CLK-gated NMOS at the bottom of a stack
  - Latch_NMOS : cross-coupled NMOS
  - InputPair_* : remaining NMOS grouped by their gate net (e.g., VINP / VINN)

Returns a list of group dicts compatible with apply_custom_groups():
    [{"name": str, "devices": [dev_id, ...]}, ...]
"""

from collections import defaultdict


def detect_functional_groups(nodes, terminal_nets):
    """Infer functional groups from topology.

    Args:
        nodes        : list of node dicts (must include "id", "type")
        terminal_nets: dict {dev_id: {"G": net, "D": net, "S": net}}

    Returns:
        list of {"name": str, "devices": [dev_id, ...]} — non-empty groups only
    """
    device_info = _build_device_info(nodes, terminal_nets)
    if not device_info:
        return []

    clk_net = _find_clk_net(device_info)
    pmos_ids = sorted(did for did, info in device_info.items() if info["type"] == "pmos")
    nmos_ids = sorted(did for did, info in device_info.items() if info["type"] == "nmos")

    groups = []
    used: set = set()

    # ── 1. Precharge PMOS — CLK-gated, source = VDD ─────────────────
    if clk_net:
        precharge = [did for did in pmos_ids if device_info[did]["G"] == clk_net]
        if precharge:
            groups.append({"name": "Precharge", "devices": precharge})
            used.update(precharge)

    remaining_pmos = [did for did in pmos_ids if did not in used]

    # ── 2. Cross-coupled PMOS → Latch_PMOS ──────────────────────────
    latch_pmos, remaining_pmos = _detect_cross_coupled(remaining_pmos, device_info)
    if latch_pmos:
        groups.append({"name": "Latch_PMOS", "devices": latch_pmos})
        used.update(latch_pmos)

    if remaining_pmos:
        groups.append({"name": "Other_PMOS", "devices": remaining_pmos})
        used.update(remaining_pmos)

    # ── 3. Tail / Clock NMOS — CLK-gated ────────────────────────────
    if clk_net:
        tail = [did for did in nmos_ids if device_info[did]["G"] == clk_net]
        if tail:
            groups.append({"name": "TailClock", "devices": tail})
            used.update(tail)

    remaining_nmos = [did for did in nmos_ids if did not in used]

    # ── 4. Cross-coupled NMOS → Latch_NMOS ──────────────────────────
    latch_nmos, remaining_nmos = _detect_cross_coupled(remaining_nmos, device_info)
    if latch_nmos:
        groups.append({"name": "Latch_NMOS", "devices": latch_nmos})
        used.update(latch_nmos)

    # ── 5. Input pairs — remaining NMOS grouped by gate net ─────────
    gate_groups: dict = defaultdict(list)
    for did in remaining_nmos:
        g = device_info[did]["G"]
        gate_groups[g if g else "__ungated__"].append(did)

    for gate_net, devs in sorted(gate_groups.items()):
        safe = gate_net.replace("<", "").replace(">", "").upper()
        name = "Other_NMOS" if gate_net == "__ungated__" else f"InputPair_{safe}"
        groups.append({"name": name, "devices": devs})

    return [g for g in groups if g["devices"]]


# ── Internal helpers ────────────────────────────────────────────────────────

def _build_device_info(nodes, terminal_nets):
    """Map dev_id → {type, G, D, S} for every NMOS/PMOS in terminal_nets."""
    id_to_type: dict = {}
    for node in nodes:
        nid = node.get("id", "")
        ntype = str(node.get("type", "")).lower()
        if ntype in ("nmos", "pmos"):
            id_to_type[nid] = ntype
            parent = (node.get("electrical") or {}).get("parent", "")
            if parent:
                id_to_type.setdefault(parent, ntype)

    result: dict = {}
    for dev_id, nets in (terminal_nets or {}).items():
        if not nets:
            continue
        dev_type = id_to_type.get(dev_id)
        if not dev_type:
            parts = dev_id.split("_")
            for n in range(len(parts) - 1, 0, -1):
                candidate = "_".join(parts[:n])
                if candidate in id_to_type:
                    dev_type = id_to_type[candidate]
                    break
        if not dev_type:
            continue
        result[dev_id] = {
            "type": dev_type,
            "G": (nets.get("G") or "").strip(),
            "D": (nets.get("D") or "").strip(),
            "S": (nets.get("S") or "").strip(),
        }
    return result


def _find_clk_net(device_info):
    """Return the clock net by finding which gate net contains 'clk' most often."""
    candidates: dict = {}
    for info in device_info.values():
        g = info["G"]
        if g and "clk" in g.lower():
            candidates[g] = candidates.get(g, 0) + 1
    return max(candidates, key=candidates.get) if candidates else None


def _detect_cross_coupled(dev_ids, device_info):
    """Split dev_ids into (cross_coupled, remaining).

    Cross-coupled criterion: A.G == B.D  AND  B.G == A.D  for some pair (A, B).
    """
    drain_to_ids: dict = defaultdict(list)
    for did in dev_ids:
        d = device_info[did]["D"]
        if d:
            drain_to_ids[d].append(did)

    cross_coupled: set = set()
    for did in dev_ids:
        g = device_info[did]["G"]
        if not g:
            continue
        for partner in drain_to_ids.get(g, []):
            if partner != did and device_info[partner]["G"] == device_info[did]["D"]:
                cross_coupled.add(did)
                cross_coupled.add(partner)

    ordered = [did for did in dev_ids if did in cross_coupled]
    remaining = [did for did in dev_ids if did not in cross_coupled]
    return ordered, remaining

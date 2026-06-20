# -*- coding: utf-8 -*-
"""
hierarchy_detector.py
=====================
Automatic functional-group detection for analog IC layouts.

Detects standard topological patterns from terminal_nets + node types:
  - Precharge  : CLK-gated PMOS pulling to VDD
  - TailClock  : CLK-gated NMOS at the bottom of a stack
  - Latch_PMOS : cross-coupled PMOS parents
  - Latch_NMOS : cross-coupled NMOS parents
  - Diff_Pair  : parent devices forming a differential pair (sources tied, different gates)
  - Current_Mirror / Active_Load : parent devices sharing gate and source, with one diode-connected

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
    used = set()

    # Define power and ground nets
    power_nets = {"VDD", "VCC", "AVDD", "DVDD"}
    ground_nets = {"VSS", "GND", "AVSS", "DVSS", "GROUND"}

    # ── 1. Precharge PMOS — CLK-gated, source = VDD ─────────────────
    precharge_devs = []
    if clk_net:
        precharge_devs = [did for did in pmos_ids if device_info[did]["G"] == clk_net]
        if precharge_devs:
            groups.append({"name": "Precharge", "devices": precharge_devs})
            used.update(precharge_devs)

    # ── 2. Tail / Clock NMOS — CLK-gated ────────────────────────────
    tail_devs = []
    if clk_net:
        tail_devs = [did for did in nmos_ids if device_info[did]["G"] == clk_net]
        if tail_devs:
            groups.append({"name": "TailClock", "devices": tail_devs})
            used.update(tail_devs)

    # Remaining devices for latch/differential/mirror detection at parent level
    remaining_devs = [did for did in device_info if did not in used]

    # Group remaining devices by their parent name (e.g., MM8_m1 -> MM8)
    parent_to_devs = defaultdict(list)
    for did in remaining_devs:
        # parent name is derived by splitting at the first underscore
        parent_name = did.split("_")[0]
        parent_to_devs[parent_name].append(did)

    # Summarize parent properties
    parent_info = {}
    for parent, devs in parent_to_devs.items():
        p_type = device_info[devs[0]]["type"]
        gates = {device_info[d]["G"] for d in devs if device_info[d]["G"]}
        drains = {device_info[d]["D"] for d in devs if device_info[d]["D"]}
        sources = {device_info[d]["S"] for d in devs if device_info[d]["S"]}
        parent_info[parent] = {
            "type": p_type,
            "gates": gates,
            "drains": drains,
            "sources": sources,
            "devices": devs
        }

    # ── 3. Cross-coupled Latch Detection (Parent level) ──────────────
    latch_pairs = []
    parents_list = list(parent_info.keys())
    for i in range(len(parents_list)):
        for j in range(i + 1, len(parents_list)):
            pA = parents_list[i]
            pB = parents_list[j]
            infoA = parent_info[pA]
            infoB = parent_info[pB]
            if infoA["type"] != infoB["type"]:
                continue

            # Check cross-coupling: A.G intersects B.D and B.G intersects A.D
            cc1 = not infoA["gates"].isdisjoint(infoB["drains"])
            cc2 = not infoB["gates"].isdisjoint(infoA["drains"])
            # Neither is diode-connected
            diodeA = not infoA["gates"].isdisjoint(infoA["drains"])
            diodeB = not infoB["gates"].isdisjoint(infoB["drains"])

            if cc1 and cc2 and not diodeA and not diodeB:
                latch_pairs.append((pA, pB, infoA["type"]))

    for pA, pB, p_type in latch_pairs:
        devs = parent_info[pA]["devices"] + parent_info[pB]["devices"]
        name = "Latch_PMOS" if p_type == "pmos" else "Latch_NMOS"
        groups.append({"name": name, "devices": sorted(devs)})
        used.update(devs)
        parent_info.pop(pA, None)
        parent_info.pop(pB, None)

    # ── 4. Differential Input Pair Detection (Parent level) ──────────
    diff_pairs = []
    parents_list = list(parent_info.keys())
    for i in range(len(parents_list)):
        for j in range(i + 1, len(parents_list)):
            pA = parents_list[i]
            pB = parents_list[j]
            infoA = parent_info[pA]
            infoB = parent_info[pB]
            if infoA["type"] != infoB["type"]:
                continue

            common_sources = infoA["sources"].intersection(infoB["sources"])
            # Exclude standard power/ground nets from common sources
            common_sources = {s for s in common_sources if s.upper() not in power_nets and s.upper() not in ground_nets}

            # Different gate nets
            diff_gates = infoA["gates"].isdisjoint(infoB["gates"])

            if common_sources and diff_gates:
                diff_pairs.append((pA, pB))

    for pA, pB in diff_pairs:
        devs = parent_info[pA]["devices"] + parent_info[pB]["devices"]
        name = f"Diff_Pair_{pA}_{pB}"
        groups.append({"name": name, "devices": sorted(devs)})
        used.update(devs)
        parent_info.pop(pA, None)
        parent_info.pop(pB, None)

    # ── 5. Current Mirror / Active Load Detection (Parent level) ─────
    # Group remaining parents by primary gate and source nets
    mirror_groups = defaultdict(list)
    for parent, info in parent_info.items():
        if not info["gates"] or not info["sources"]:
            continue
        g = list(info["gates"])[0]
        s = list(info["sources"])[0]
        mirror_groups[(info["type"], g, s)].append(parent)

    for (p_type, g, s), parents in list(mirror_groups.items()):
        if len(parents) >= 2:
            # Check if at least one parent is diode-connected (gate matches drain)
            has_diode = False
            for p in parents:
                info = parent_info[p]
                if not info["gates"].isdisjoint(info["drains"]):
                    has_diode = True
                    break
            if has_diode:
                devs = []
                for p in parents:
                    devs.extend(parent_info[p]["devices"])
                    parent_info.pop(p, None)
                parents_str = "_".join(sorted(parents))
                name = f"Active_Load_{parents_str}" if p_type == "pmos" else f"Current_Mirror_{parents_str}"
                groups.append({"name": name, "devices": sorted(devs)})
                used.update(devs)

    # CMOS Inverters or other remaining devices
    remaining_pmos = sorted(did for did, info in device_info.items() if info["type"] == "pmos" and did not in used)
    remaining_nmos = sorted(did for did, info in device_info.items() if info["type"] == "nmos" and did not in used)

    # Inverters
    for p_id in list(remaining_pmos):
        p_info = device_info[p_id]
        p_s = p_info["S"].upper()
        if not any(pn in p_s for pn in power_nets) and p_info["S"]:
            continue
        
        for n_id in list(remaining_nmos):
            n_info = device_info[n_id]
            n_s = n_info["S"].upper()
            if not any(gn in n_s for gn in ground_nets) and n_info["S"]:
                continue
            
            # Match if gate and drain are the same
            if p_info["G"] == n_info["G"] and p_info["D"] == n_info["D"]:
                gate_net = p_info["G"]
                safe = gate_net.replace("<", "").replace(">", "").upper()
                groups.append({
                    "name": f"Inverter_{safe}",
                    "devices": [p_id, n_id]
                })
                used.add(p_id)
                used.add(n_id)
                remaining_pmos.remove(p_id)
                remaining_nmos.remove(n_id)
                break

    # Remaining devices
    is_analog = bool(clk_net or latch_pairs or diff_pairs or mirror_groups)
    if remaining_pmos:
        name = "PUN" if not is_analog else "Other_PMOS"
        groups.append({"name": name, "devices": remaining_pmos})
    if remaining_nmos:
        name = "PDN" if not is_analog else "Other_NMOS"
        groups.append({"name": name, "devices": remaining_nmos})

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

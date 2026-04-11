"""
Device matcher — matches netlist devices to layout instances.

Handles transistors (NMOS/PMOS) and passives (resistors/capacitors).
For multi-finger transistors, all fingers map to the same layout instance.
"""

from collections import defaultdict


def split_layout_by_type(layout_devices):
    """Separate layout instances into groups by base type.
    Returns: { 'nmos': [(idx, dev), ...], 'pmos': [...], ... }
    """
    groups = {
        "nmos": [],
        "pmos": [],
        "res":  [],
        "cap":  [],
    }

    for idx, dev in enumerate(layout_devices):
        cell = dev["cell"].lower()
        ptype = dev.get("passive_type", "")

        if ptype == "res":
            groups["res"].append((idx, dev))
        elif ptype == "cap":
            groups["cap"].append((idx, dev))
        elif "nfet" in cell or "nmos" in cell:
            groups["nmos"].append((idx, dev))
        elif "pfet" in cell or "pmos" in cell:
            groups["pmos"].append((idx, dev))

    return groups


def split_netlist_by_type(netlist):
    """Separate netlist devices into groups by base type.
    Returns: { 'nmos': [dev_name, ...], 'pmos': [...], ... }
    """
    groups = {
        "nmos": [],
        "pmos": [],
        "res":  [],
        "cap":  [],
    }

    for dev in netlist.devices.values():
        if dev.type in groups:
            groups[dev.type].append(dev.name)

    # Sort names for determinism (groups e.g. MM1_f1, MM1_f2...)
    for dtype in groups:
        groups[dtype] = sorted(groups[dtype])

    return groups


def sort_layout_by_position(layout_group):
    """Sort layout instances spatially (left-to-right, bottom-to-top)."""
    return sorted(layout_group,
                  key=lambda x: (x[1]["x"], x[1]["y"]))


def match_devices(netlist, layout_devices):
    """Deterministically match electrical devices to layout instances.

    Uses a hierarchy of matching:
    1. Device Type (NMOS/PMOS/Res/Cap)
    2. Spatial Position (Layout) vs Lexicographical Name (Netlist)

    Returns:
        mapping: {device_name: layout_index}
    """

    mapping = {}

    # 1) Split both sides into groups
    layout_groups = split_layout_by_type(layout_devices)
    netlist_groups = split_netlist_by_type(netlist)

    # 2) Match each type
    for dtype in ["nmos", "pmos", "res", "cap"]:
        l_list = layout_groups[dtype]
        n_list = netlist_groups[dtype]

        if len(l_list) != len(n_list):
            print(f"[Matcher] ⚠ {dtype.upper()} count mismatch: "
                  f"netlist={len(n_list)}, layout={len(l_list)}. "
                  "Matching will be partial or may fail.")

        # Sort layout spatially and match to sorted names
        l_sorted = sort_layout_by_position(l_list)
        # n_list is already sorted by name in split_netlist_by_type

        for (layout_idx, _), net_name in zip(l_sorted, n_list):
            mapping[net_name] = layout_idx

    return mapping

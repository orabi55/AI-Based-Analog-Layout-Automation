"""
Device matcher — matches netlist devices to layout instances.

Handles transistors (NMOS/PMOS) and passives (resistors/capacitors).
For multi-finger transistors, all fingers map to the same layout instance.
"""

from collections import defaultdict


# ------------------------------------------------------------
# Split layout instances by device type
# ------------------------------------------------------------

def split_layout_by_type(layout_devices):
    """Separate layout instances into NMOS, PMOS, resistor, and capacitor groups."""
    layout_nmos = []
    layout_pmos = []
    layout_res = []
    layout_cap = []

    for idx, dev in enumerate(layout_devices):
        cell = dev["cell"].lower()
        ptype = dev.get("passive_type", "")

        if ptype == "res":
            layout_res.append((idx, dev))
        elif ptype == "cap":
            layout_cap.append((idx, dev))
        elif "nfet" in cell or "nmos" in cell:
            layout_nmos.append((idx, dev))
        elif "pfet" in cell or "pmos" in cell:
            layout_pmos.append((idx, dev))

    return layout_nmos, layout_pmos, layout_res, layout_cap


# ------------------------------------------------------------
# Split netlist devices by type (grouping fingers by parent)
# ------------------------------------------------------------

def split_netlist_by_type(netlist):
    """Separate netlist devices into NMOS, PMOS, resistor, and capacitor groups.

    For multi-finger devices (nf>1), groups fingers by their parent
    name to get the logical device count that matches the layout.
    """
    nmos_parents = set()
    pmos_parents = set()
    res_names = []
    cap_names = []

    for dev in netlist.devices.values():
        parent = dev.params.get("parent", dev.name)

        if dev.type == "nmos":
            nmos_parents.add(parent)
        elif dev.type == "pmos":
            pmos_parents.add(parent)
        elif dev.type == "res":
            res_names.append(dev.name)
        elif dev.type == "cap":
            cap_names.append(dev.name)

    return sorted(nmos_parents), sorted(pmos_parents), sorted(res_names), sorted(cap_names)


# ------------------------------------------------------------
# Sort layout devices by position
# ------------------------------------------------------------

def sort_layout_by_position(layout_group):
    """Sort layout instances spatially (left-to-right, bottom-to-top)."""
    return sorted(layout_group,
                  key=lambda x: (x[1]["x"], x[1]["y"]))


# ------------------------------------------------------------
# Main matching function
# ------------------------------------------------------------

def match_devices(netlist, layout_devices):
    """Deterministically match electrical devices to layout instances.

    For multi-finger devices, all fingers map to the same layout index.

    Returns:
        mapping: {device_name: layout_index}
    """

    mapping = {}

    # 1) Split by type
    layout_nmos, layout_pmos, layout_res, layout_cap = split_layout_by_type(layout_devices)
    netlist_nmos, netlist_pmos, netlist_res, netlist_cap = split_netlist_by_type(netlist)

    # 2) Validate transistor counts
    if len(layout_nmos) != len(netlist_nmos):
        raise ValueError(
            f"NMOS count mismatch: netlist={len(netlist_nmos)}, "
            f"layout={len(layout_nmos)}"
        )

    if len(layout_pmos) != len(netlist_pmos):
        raise ValueError(
            f"PMOS count mismatch: netlist={len(netlist_pmos)}, "
            f"layout={len(layout_pmos)}"
        )

    # Validate passive counts (warn instead of raising — passives may be missing from layout)
    if len(layout_res) != len(netlist_res):
        print(f"[Matcher] ⚠ Resistor count mismatch: netlist={len(netlist_res)}, "
              f"layout={len(layout_res)}")
    if len(layout_cap) != len(netlist_cap):
        print(f"[Matcher] ⚠ Capacitor count mismatch: netlist={len(netlist_cap)}, "
              f"layout={len(layout_cap)}")

    # 3) Sort layout by geometry
    layout_nmos_sorted = sort_layout_by_position(layout_nmos)
    layout_pmos_sorted = sort_layout_by_position(layout_pmos)
    layout_res_sorted  = sort_layout_by_position(layout_res)
    layout_cap_sorted  = sort_layout_by_position(layout_cap)

    # 4) Assign parent transistors to layout indices
    parent_mapping = {}

    for (layout_idx, _), net_name in zip(layout_nmos_sorted, netlist_nmos):
        parent_mapping[net_name] = layout_idx

    for (layout_idx, _), net_name in zip(layout_pmos_sorted, netlist_pmos):
        parent_mapping[net_name] = layout_idx

    # 5) Expand transistor mapping to include finger-expanded names
    for dev in netlist.devices.values():
        parent = dev.params.get("parent", dev.name)
        if parent in parent_mapping:
            mapping[dev.name] = parent_mapping[parent]

    # 6) Assign passives directly (no finger expansion needed)
    for (layout_idx, _), dev_name in zip(layout_res_sorted, netlist_res):
        mapping[dev_name] = layout_idx

    for (layout_idx, _), dev_name in zip(layout_cap_sorted, netlist_cap):
        mapping[dev_name] = layout_idx

    return mapping
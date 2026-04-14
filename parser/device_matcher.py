"""
Device matcher — matches netlist devices to layout instances.

Handles both flat and hierarchical designs, including multi-finger
device expansion (nf>1) where multiple netlist fingers map to a
single layout PCell instance.
"""

from collections import defaultdict


# ------------------------------------------------------------
# Split layout instances by device type
# ------------------------------------------------------------

def split_layout_by_type(layout_devices):
    """Separate layout instances into NMOS and PMOS groups."""
    layout_nmos = []
    layout_pmos = []

    for idx, dev in enumerate(layout_devices):
        cell = dev["cell"].lower()

        if "nfet" in cell or "nmos" in cell:
            layout_nmos.append((idx, dev))
        elif "pfet" in cell or "pmos" in cell:
            layout_pmos.append((idx, dev))

    return layout_nmos, layout_pmos


# ------------------------------------------------------------
# Split netlist devices by type (grouping fingers by parent)
# ------------------------------------------------------------

def split_netlist_by_type(netlist):
    """Separate netlist devices into NMOS and PMOS groups.

    For multi-finger devices (nf>1), groups fingers by their parent
    name to get the logical device count that matches the layout.

    Returns:
        (netlist_nmos, netlist_pmos): Lists of unique parent device names
    """
    nmos_parents = set()
    pmos_parents = set()

    for dev in netlist.devices.values():
        # Use the parent name if this is a finger expansion,
        # otherwise use the device name itself
        parent = dev.params.get("parent", dev.name)

        if dev.type == "nmos":
            nmos_parents.add(parent)
        elif dev.type == "pmos":
            pmos_parents.add(parent)

    return sorted(nmos_parents), sorted(pmos_parents)


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
        For finger-expanded devices, each finger gets the same layout index.
    """

    mapping = {}

    # 1) Split by type
    layout_nmos, layout_pmos = split_layout_by_type(layout_devices)
    netlist_nmos, netlist_pmos = split_netlist_by_type(netlist)

    # 2) Validate counts (warn instead of error for multi-finger mismatches)
    if len(layout_nmos) != len(netlist_nmos):
        print(f"[Device Matcher] WARNING: NMOS count mismatch: "
              f"netlist={len(netlist_nmos)}, layout={len(layout_nmos)}. "
              f"Proceeding with best-effort matching.")

    if len(layout_pmos) != len(netlist_pmos):
        print(f"[Device Matcher] WARNING: PMOS count mismatch: "
              f"netlist={len(netlist_pmos)}, layout={len(layout_pmos)}. "
              f"Proceeding with best-effort matching.")

    # 3) Sort layout by geometry
    layout_nmos_sorted = sort_layout_by_position(layout_nmos)
    layout_pmos_sorted = sort_layout_by_position(layout_pmos)

    # 4) Assign parent devices to layout indices (match by position order)
    parent_mapping = {}

    for i in range(min(len(layout_nmos_sorted), len(netlist_nmos))):
        layout_idx = layout_nmos_sorted[i][0]
        net_name = netlist_nmos[i]
        parent_mapping[net_name] = layout_idx

    for i in range(min(len(layout_pmos_sorted), len(netlist_pmos))):
        layout_idx = layout_pmos_sorted[i][0]
        net_name = netlist_pmos[i]
        parent_mapping[net_name] = layout_idx

    # 5) Expand mapping to include finger-expanded names
    for dev in netlist.devices.values():
        parent = dev.params.get("parent", dev.name)
        if parent in parent_mapping:
            mapping[dev.name] = parent_mapping[parent]

    return mapping
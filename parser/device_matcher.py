from collections import defaultdict


# ------------------------------------------------------------
# Split layout instances by device type
# ------------------------------------------------------------

def split_layout_by_type(layout_devices):
    """
    Separate layout instances into NMOS and PMOS groups
    based on PCell name.
    """

    layout_nmos = []
    layout_pmos = []

    for idx, dev in enumerate(layout_devices):

        cell = dev["cell"].lower()

        if "nfet" in cell:
            layout_nmos.append((idx, dev))

        elif "pfet" in cell:
            layout_pmos.append((idx, dev))

    return layout_nmos, layout_pmos


# ------------------------------------------------------------
# Split netlist devices by type
# ------------------------------------------------------------

def split_netlist_by_type(netlist):
    """
    Separate netlist devices into NMOS and PMOS groups.
    """

    netlist_nmos = []
    netlist_pmos = []

    for dev in netlist.devices.values():

        if dev.type == "nmos":
            netlist_nmos.append(dev.name)

        elif dev.type == "pmos":
            netlist_pmos.append(dev.name)

    return netlist_nmos, netlist_pmos


# ------------------------------------------------------------
# Sort layout devices by position
# ------------------------------------------------------------

def sort_layout_by_position(layout_group):
    """
    Sort layout instances spatially (left-to-right, bottom-to-top)
    """

    return sorted(layout_group,
                  key=lambda x: (x[1]["x"], x[1]["y"]))


# ------------------------------------------------------------
# Main matching function
# ------------------------------------------------------------

def match_devices(netlist, layout_devices):
    """
    Deterministically match electrical devices to layout instances
    based on device type and spatial ordering.
    """

    mapping = {}

    # --------------------------------------------------------
    # 1) Split by type
    # --------------------------------------------------------
    layout_nmos, layout_pmos = split_layout_by_type(layout_devices)
    netlist_nmos, netlist_pmos = split_netlist_by_type(netlist)

    # --------------------------------------------------------
    # 2) Validate counts
    # --------------------------------------------------------
    if len(layout_nmos) != len(netlist_nmos):
        raise ValueError(
            f"NMOS count mismatch: netlist={len(netlist_nmos)}, layout={len(layout_nmos)}"
        )

    if len(layout_pmos) != len(netlist_pmos):
        raise ValueError(
            f"PMOS count mismatch: netlist={len(netlist_pmos)}, layout={len(layout_pmos)}"
        )

    # --------------------------------------------------------
    # 3) Sort layout by geometry
    # --------------------------------------------------------
    layout_nmos_sorted = sort_layout_by_position(layout_nmos)
    layout_pmos_sorted = sort_layout_by_position(layout_pmos)

    # --------------------------------------------------------
    # 4) Sort netlist names (stable ordering)
    # --------------------------------------------------------
    netlist_nmos.sort()
    netlist_pmos.sort()

    # --------------------------------------------------------
    # 5) Assign sequentially
    # --------------------------------------------------------
    for (layout_idx, _), net_name in zip(layout_nmos_sorted, netlist_nmos):
        mapping[net_name] = layout_idx

    for (layout_idx, _), net_name in zip(layout_pmos_sorted, netlist_pmos):
        mapping[net_name] = layout_idx

    return mapping
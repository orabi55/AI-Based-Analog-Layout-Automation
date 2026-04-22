"""
Device matcher — matches netlist devices to layout instances.

Handles transistors (NMOS/PMOS) and passives (resistors/capacitors).
For multi-finger transistors, all fingers map to the same layout instance.
"""

import logging
import re
from collections import defaultdict


_LOGGER = logging.getLogger(__name__)
_NAME_TOKEN_RE = re.compile(r"\d+|\D+")
_DEVICE_TYPES = ("nmos", "pmos", "res", "cap")


def _natural_sort_key(value):
    return [
        int(token) if token.isdigit() else token.lower()
        for token in _NAME_TOKEN_RE.findall(str(value))
    ]


def split_layout_by_type(layout_devices):
    """Separate layout instances into groups by base type."""
    groups = {dtype: [] for dtype in _DEVICE_TYPES}

    for idx, dev in enumerate(layout_devices):
        cell = str(dev.get("cell", "")).lower()
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
    """Separate netlist devices into flat groups by base type."""
    groups = {dtype: [] for dtype in _DEVICE_TYPES}

    for dev in netlist.devices.values():
        if dev.type in groups:
            groups[dev.type].append(dev.name)

    for dtype in groups:
        groups[dtype] = sorted(groups[dtype], key=_natural_sort_key)

    return groups


def split_netlist_by_logical_device(netlist):
    """Group netlist leaves by their logical parent device."""
    groups = {dtype: defaultdict(list) for dtype in _DEVICE_TYPES}

    for dev in netlist.devices.values():
        if dev.type not in groups:
            continue
        parent_name = dev.params.get("parent") or dev.name
        groups[dev.type][parent_name].append(dev.name)

    logical_groups = {dtype: [] for dtype in _DEVICE_TYPES}
    for dtype, parent_map in groups.items():
        for parent_name, child_names in parent_map.items():
            logical_groups[dtype].append(
                (parent_name, sorted(child_names, key=_natural_sort_key))
            )
        logical_groups[dtype].sort(key=lambda entry: _natural_sort_key(entry[0]))

    return logical_groups


def sort_layout_by_position(layout_group):
    """Sort layout instances spatially (left-to-right, bottom-to-top)."""
    return sorted(layout_group, key=lambda entry: (entry[1]["x"], entry[1]["y"]))


def match_devices(netlist, layout_devices):
    """Deterministically match electrical devices to layout instances.

    Matching priority:
    1. Device Type (NMOS/PMOS/Res/Cap)
    2. Exact leaf-device count match
    3. Logical parent-device match for expanded multi-finger netlists
    4. Partial fallback with a warning

    Returns:
        mapping: {device_name: layout_index}
    """

    mapping = {}

    # 1) Split both sides into groups
    layout_groups = split_layout_by_type(layout_devices)
    netlist_groups = split_netlist_by_type(netlist)
    logical_groups = split_netlist_by_logical_device(netlist)

    # 2) Match each type
    for dtype in _DEVICE_TYPES:
        l_list = sort_layout_by_position(layout_groups[dtype])
        n_list = netlist_groups[dtype]
        logical_list = logical_groups[dtype]

        if len(l_list) == len(n_list):
            for (layout_idx, _), net_name in zip(l_list, n_list):
                mapping[net_name] = layout_idx
            continue

        if len(l_list) == len(logical_list):
            _LOGGER.warning(
                "[Matcher] %s count mismatch: netlist=%d, layout=%d. "
                "Collapsing expanded logical devices onto shared layout instances.",
                dtype.upper(),
                len(n_list),
                len(l_list),
            )
            for (layout_idx, _), (_, child_names) in zip(l_list, logical_list):
                for child_name in child_names:
                    mapping[child_name] = layout_idx
            continue

        _LOGGER.warning(
            "[Matcher] %s count mismatch: netlist=%d, layout=%d, logical=%d. "
            "Matching will be partial.",
            dtype.upper(),
            len(n_list),
            len(l_list),
            len(logical_list),
        )

        if False and len(l_list) != len(n_list):
            print(f"[Matcher] ⚠ {dtype.upper()} count mismatch: "
                  f"netlist={len(n_list)}, layout={len(l_list)}. "
                  "Matching will be partial or may fail.")

        # Sort layout spatially and match to sorted names
        l_sorted = l_list
        # n_list is already sorted by name in split_netlist_by_type

        for (layout_idx, _), net_name in zip(l_sorted, n_list):
            mapping[net_name] = layout_idx

    return mapping

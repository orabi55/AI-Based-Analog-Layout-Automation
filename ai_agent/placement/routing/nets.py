"""
Net → Logical Device Map Builder
==================================
Builds a mapping from net names to the *logical* devices (post-finger-aggregation)
that are connected to them.

Problem solved:
  After finger expansion, one transistor MM1 becomes MM1_m1 … MM1_m8.
  The old scorer treated each finger as a separate endpoint, inflating net spans.
  This module aggregates all fingers of the same logical device back to a single
  centroid, so MM1_m1…MM1_m8 all contribute ONE point to the net span.

Usage:
    from ai_agent.placement.routing.nets import build_net_map, LogicalDev
    net_map, dev_map = build_net_map(nodes, edges, terminal_nets)
    # net_map: {net_name: [LogicalDev, ...]}
    # dev_map: {logical_id: LogicalDev}
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Regex to strip _m<N> / _f<N> finger suffixes from device IDs
_FINGER_RE = re.compile(r"^(.+?)(?:_[mf]\d+)+$", re.IGNORECASE)

# Supply nets to exclude from signal-net analysis
_SUPPLY_NETS = frozenset({
    "vdd", "vss", "gnd", "vcc", "avdd", "avss", "vdda", "vssa",
    "vpwr", "vgnd", "vee", "vddq",
})


def _logical_id(finger_id: str) -> str:
    """Return the logical device name by stripping finger suffixes."""
    m = _FINGER_RE.match(finger_id)
    return m.group(1) if m else finger_id


def _is_dummy(node_id: str) -> bool:
    """Return True if this node is a filler/edge dummy."""
    nid = node_id.upper()
    return (
        nid.startswith(("FILLER_DUMMY_", "EDGE_DUMMY", "DUMMY_MATRIX_"))
        or (len(nid) >= 2 and nid[0] == "D" and nid[1:].isdigit())
    )


@dataclass
class LogicalDev:
    """
    Represents a logical transistor (potentially multi-finger) in the layout.

    Attributes:
        logical_id : canonical device name (e.g. 'MM1')
        dev_type   : 'pmos' | 'nmos'
        cx         : center x of all fingers (µm)
        cy         : center y of all fingers (µm)
        x_min      : leftmost finger left edge
        x_max      : rightmost finger right edge
        y_min      : bottom edge of row
        y_max      : top edge of row
        fingers    : list of finger node IDs
    """
    logical_id: str
    dev_type:   str
    cx:         float = 0.0
    cy:         float = 0.0
    x_min:      float = 0.0
    x_max:      float = 0.0
    y_min:      float = 0.0
    y_max:      float = 0.0
    fingers:    List[str] = field(default_factory=list)


def build_net_map(
    nodes: list,
    edges: Optional[list] = None,
    terminal_nets: Optional[dict] = None,
) -> tuple[Dict[str, List[LogicalDev]], Dict[str, LogicalDev]]:
    """
    Build net→logical-device and logical-id→device maps from placement data.

    Args:
        nodes:         physical placement node list (finger-level)
        edges:         list of edge dicts with 'net', 'source', 'target'
        terminal_nets: {dev_id: {'D': net, 'G': net, 'S': net, 'B': net}}

    Returns:
        (net_map, dev_map)
        net_map: {net_name: [LogicalDev, ...]}  — signal nets only
        dev_map: {logical_id: LogicalDev}
    """
    # --- 1. Build logical device bounding boxes from finger positions ----------
    dev_map: Dict[str, LogicalDev] = {}

    for node in nodes:
        nid = node.get("id", "")
        if _is_dummy(nid):
            continue
        lid = _logical_id(nid)
        geo = node.get("geometry", {})
        try:
            x = float(geo.get("x", 0))
            y = float(geo.get("y", 0))
            w = float(geo.get("width", 0))
            h = float(geo.get("height", 0))
        except (TypeError, ValueError):
            continue

        dtype = str(node.get("type", "nmos")).lower()
        if "pmos" in dtype or "p_mos" in dtype:
            dtype = "pmos"
        else:
            dtype = "nmos"

        if lid not in dev_map:
            dev_map[lid] = LogicalDev(
                logical_id=lid, dev_type=dtype,
                cx=x, cy=y, x_min=x, x_max=x + w,
                y_min=y, y_max=y + h, fingers=[nid],
            )
        else:
            d = dev_map[lid]
            d.fingers.append(nid)
            d.x_min = min(d.x_min, x)
            d.x_max = max(d.x_max, x + w)
            d.y_min = min(d.y_min, y)
            d.y_max = max(d.y_max, y + h)

    # Compute centroids
    for d in dev_map.values():
        d.cx = (d.x_min + d.x_max) / 2.0
        d.cy = (d.y_min + d.y_max) / 2.0

    # --- 2. Collect net→logical-id connections --------------------------------
    net_lids: Dict[str, set] = {}   # net → {logical_id}

    def _add(net: str, node_id: str) -> None:
        if not net or net.lower() in _SUPPLY_NETS:
            return
        lid = _logical_id(node_id)
        if lid in dev_map:
            net_lids.setdefault(net, set()).add(lid)

    for edge in (edges or []):
        net = edge.get("net", edge.get("label", ""))
        _add(net, edge.get("source", edge.get("src", "")))
        _add(net, edge.get("target", edge.get("tgt", "")))

    for dev_id, pins in (terminal_nets or {}).items():
        for pin, net in pins.items():
            if pin.upper() == "B":          # skip body/bulk pins
                continue
            _add(net, dev_id)

    # --- 3. Build final net_map -----------------------------------------------
    net_map: Dict[str, List[LogicalDev]] = {}
    for net, lids in net_lids.items():
        devs = [dev_map[lid] for lid in lids if lid in dev_map]
        if len(devs) >= 2:              # single-pin nets carry no routing cost
            net_map[net] = devs

    return net_map, dev_map

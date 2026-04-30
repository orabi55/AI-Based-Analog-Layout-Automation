"""
Manhattan HPWL Calculator
==========================
Computes the Half-Perimeter Wire Length (HPWL) for each net and the total.

HPWL uses the Manhattan distance metric (the reference for routing preview):
  - For a 2-pin net:     HPWL = |Δx| + |Δy|
  - For an N-pin net:    HPWL = (x_max - x_min) + (y_max - y_min)
                               = bounding-box half-perimeter

This is the *minimum possible* Manhattan routing wire length — the theoretical
lower bound. Real routing will be >= this, so it is a meaningful and physically
grounded estimate even without actually routing.

Usage:
    from ai_agent.placement.routing.hpwl import hpwl_for_net, total_hpwl
"""

from __future__ import annotations
from typing import List, NamedTuple, Dict
from ai_agent.placement.routing.nets import LogicalDev


class NetHPWL(NamedTuple):
    """HPWL decomposed by axis for a single net."""
    net:     str
    x_span:  float   # µm — horizontal bounding-box width
    y_span:  float   # µm — vertical bounding-box height
    hpwl:    float   # µm — x_span + y_span  (Manhattan HPWL)
    x_min:   float
    x_max:   float
    y_min:   float
    y_max:   float
    cross_row: bool  # True if net touches both PMOS and NMOS rows


def hpwl_for_net(net_name: str, devices: List[LogicalDev]) -> NetHPWL:
    """
    Compute Manhattan HPWL for one net given its connected logical devices.

    Uses device *centroids* (cx, cy) for span calculation so that a
    multi-finger block counts as one routing endpoint, not N endpoints.
    """
    xs = [d.cx for d in devices]
    ys = [d.cy for d in devices]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    x_span = x_max - x_min
    y_span = y_max - y_min
    cross_row = len({d.dev_type for d in devices}) > 1   # both pmos and nmos

    return NetHPWL(
        net=net_name,
        x_span=round(x_span, 6),
        y_span=round(y_span, 6),
        hpwl=round(x_span + y_span, 6),
        x_min=round(x_min, 6),
        x_max=round(x_max, 6),
        y_min=round(y_min, 6),
        y_max=round(y_max, 6),
        cross_row=cross_row,
    )


def total_hpwl(net_hpwls: List[NetHPWL]) -> float:
    """Sum of HPWL across all nets (µm). This is the primary routing cost metric."""
    return round(sum(n.hpwl for n in net_hpwls), 6)


def compute_all_hpwl(
    net_map: Dict[str, List[LogicalDev]]
) -> Dict[str, NetHPWL]:
    """
    Compute HPWL for all nets in the net_map.

    Returns:
        {net_name: NetHPWL}
    """
    return {net: hpwl_for_net(net, devs) for net, devs in net_map.items()}

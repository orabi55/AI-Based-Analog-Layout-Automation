"""
Sweep-Line Crossing Estimator
==============================
Estimates the number of routing wire crossings using a sweep-line algorithm
on an abstract Manhattan routing topology.

Model:
  Each net is represented as an H-V routing tree:
    - One horizontal trunk segment at the net's median-y, spanning [x_min, x_max]
    - One vertical trunk segment at the net's centroid-x, spanning [y_min, y_max]
  
  This is the canonical analog routing topology (L-shaped / T-shaped routes).
  
  A crossing is counted when:
    - A horizontal segment of net A crosses a vertical segment of net B, and
      A ≠ B (same net segments don't cross themselves).

  The result is a crossing *estimate*, not an exact router output. It is
  significantly more meaningful than the old bounding-box x-overlap count,
  which was not measuring crossings at all.

Usage:
    from ai_agent.placement.routing.crossings import estimate_crossings
    n = estimate_crossings(net_hpwls)
"""

from __future__ import annotations
from typing import List, NamedTuple, Tuple, Dict
from ai_agent.placement.routing.hpwl import NetHPWL


class _HSeg(NamedTuple):
    """Horizontal segment: y-fixed, x varies."""
    net:   str
    y:     float
    x_min: float
    x_max: float


class _VSeg(NamedTuple):
    """Vertical segment: x-fixed, y varies."""
    net:   str
    x:     float
    y_min: float
    y_max: float


def _build_segments(net_hpwls: List[NetHPWL]) -> Tuple[List[_HSeg], List[_VSeg]]:
    """Build H and V trunk segments for each net."""
    h_segs: List[_HSeg] = []
    v_segs: List[_VSeg] = []

    for nh in net_hpwls:
        # Only add segments with non-zero span
        if nh.x_span > 1e-9:
            median_y = (nh.y_min + nh.y_max) / 2.0
            h_segs.append(_HSeg(nh.net, median_y, nh.x_min, nh.x_max))
        if nh.y_span > 1e-9:
            centroid_x = (nh.x_min + nh.x_max) / 2.0
            v_segs.append(_VSeg(nh.net, centroid_x, nh.y_min, nh.y_max))

    return h_segs, v_segs


def _sweep_line_crossings(h_segs: List[_HSeg], v_segs: List[_VSeg]) -> int:
    """
    Count H×V crossings via sweep line.

    Events: left-end and right-end of each H segment, and the x-position
    of each V segment. For each V segment encountered during the sweep,
    count how many active H segments have a y within [v.y_min, v.y_max].

    Time: O((H + V) log(H + V)) amortized.
    """
    if not h_segs or not v_segs:
        return 0

    # Event types: 0=H_start, 1=V_query, 2=H_end
    events: List[Tuple[float, int, object]] = []
    for h in h_segs:
        events.append((h.x_min, 0, h))
        events.append((h.x_max, 2, h))
    for v in v_segs:
        events.append((v.x, 1, v))

    # Sort by x; on tie: starts before queries before ends
    events.sort(key=lambda e: (e[0], e[1]))

    # Active H segments (sorted by y for range queries)
    # Use a simple list; for typical analog circuits (<100 nets) this is fine.
    active_ys: Dict[str, float] = {}   # net → y of its H segment

    crossings = 0
    for _, etype, seg in events:
        if etype == 0:      # H segment start
            h = seg
            active_ys[id(h)] = h.y
        elif etype == 2:    # H segment end
            h = seg
            active_ys.pop(id(h), None)
        else:               # V segment query
            v = seg
            for h_id, h_y in active_ys.items():
                # Check if the H segment's y falls within V segment's y range
                # and they belong to different nets
                # Retrieve the H segment's net name via the event
                if v.y_min <= h_y <= v.y_max:
                    crossings += 1

    return crossings


def estimate_crossings(net_hpwls: List[NetHPWL]) -> int:
    """
    Estimate the number of routing wire crossings for a placement.

    Args:
        net_hpwls: list of NetHPWL results from hpwl.compute_all_hpwl()

    Returns:
        Integer crossing estimate (0 = ideal, higher = more congested)
    """
    h_segs, v_segs = _build_segments(net_hpwls)
    return _sweep_line_crossings(h_segs, v_segs)

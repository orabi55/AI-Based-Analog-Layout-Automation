"""
Per-Band Routing Density Estimator
=====================================
Estimates the number of routing tracks (wires) needed in each horizontal
band between adjacent device rows. This output feeds the channel_planner
node to decide how much to widen each gap.

Band definition:
  For N device rows, there are N+1 bands:
    band 0: below the bottommost row (substrate/tap area)
    band k: between row k-1 and row k  (inter-row channel)
    band N: above the topmost row (supply rails)

Track count per band:
  A net must route through a band if any of its endpoints are on opposite
  sides of the band boundary. For each such net, it needs one track.
  track_count(band) = number of distinct nets crossing that band boundary.

Recommended channel width:
  width = (track_count + CHANNEL_MARGIN_TRACKS) * METAL_PITCH_UM
  capped at MAX_CHANNEL_WIDTH_UM.

Usage:
    from ai_agent.placement.routing.density import estimate_channel_density
    channels = estimate_channel_density(net_hpwls, placement_nodes)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Tuple

from ai_agent.placement.routing.hpwl import NetHPWL
from config.design_rules import (
    METAL_PITCH_UM, CHANNEL_MARGIN_TRACKS, MAX_CHANNEL_WIDTH_UM, ROW_HEIGHT_UM
)


@dataclass
class ChannelReport:
    """
    Routing channel between two adjacent device rows.

    Attributes:
        band_index          : 0 = bottommost gap, N = topmost
        y_boundary          : Y coordinate of the boundary between rows (µm)
        y_row_below         : Y of the row below this boundary (None if lowest)
        y_row_above         : Y of the row above this boundary (None if highest)
        track_count         : estimated horizontal routing tracks needed
        nets                : net names that must cross this band
        current_width_um    : current gap between rows (usually 0 for packed layout)
        recommended_width_um: width the channel_planner should set
    """
    band_index:            int
    y_boundary:            float
    y_row_below:           float | None
    y_row_above:           float | None
    track_count:           int
    nets:                  List[str] = field(default_factory=list)
    current_width_um:      float = 0.0
    recommended_width_um:  float = 0.0


def _extract_row_ys(placement_nodes: list) -> List[float]:
    """
    Extract unique row Y values (rounded to ROW_HEIGHT_UM grid) from
    active (non-dummy) placement nodes.
    """
    ys = set()
    for n in placement_nodes:
        nid = str(n.get("id", ""))
        if nid.startswith(("FILLER_DUMMY_", "EDGE_DUMMY", "DUMMY_")):
            continue
        if len(nid) >= 2 and nid[0].upper() == "D" and nid[1:].isdigit():
            continue
        try:
            y = float(n.get("geometry", {}).get("y", 0))
            ys.add(round(y / ROW_HEIGHT_UM) * ROW_HEIGHT_UM)
        except (TypeError, ValueError):
            continue
    return sorted(ys)


def estimate_channel_density(
    net_hpwls: List[NetHPWL],
    placement_nodes: list,
) -> List[ChannelReport]:
    """
    Estimate per-band routing track counts and recommended channel widths.

    Args:
        net_hpwls        : list of NetHPWL from hpwl.compute_all_hpwl()
        placement_nodes  : physical node list from the placement pipeline

    Returns:
        List of ChannelReport, one per inter-row boundary, sorted by y_boundary.
    """
    row_ys = _extract_row_ys(placement_nodes)
    if len(row_ys) < 2:
        return []

    # Build band boundaries: between each pair of adjacent rows
    # Band k is between row_ys[k] and row_ys[k+1]
    boundaries: List[Tuple[float, float | None, float | None]] = []
    for i in range(len(row_ys) - 1):
        y_low  = row_ys[i]
        y_high = row_ys[i + 1]
        boundary_y = (y_low + y_high) / 2.0
        boundaries.append((boundary_y, y_low, y_high))

    # For each net, determine which band boundaries it crosses
    # A net crosses a boundary at y_b if y_min < y_b < y_max
    band_nets: Dict[int, List[str]] = {i: [] for i in range(len(boundaries))}
    for nh in net_hpwls:
        if nh.y_span < 1e-9:
            continue   # purely horizontal net — crosses no bands
        for i, (y_b, _, _) in enumerate(boundaries):
            if nh.y_min < y_b < nh.y_max:
                band_nets[i].append(nh.net)

    # Build ChannelReport list
    channels: List[ChannelReport] = []
    for i, (y_b, y_low, y_high) in enumerate(boundaries):
        nets = band_nets[i]
        track_count = len(nets)
        current_width = (y_high - y_low) - ROW_HEIGHT_UM   # gap between row tops/bottoms
        current_width = max(0.0, round(current_width, 6))
        recommended = min(
            (track_count + CHANNEL_MARGIN_TRACKS) * METAL_PITCH_UM,
            MAX_CHANNEL_WIDTH_UM,
        )
        recommended = round(recommended, 6)
        channels.append(ChannelReport(
            band_index=i,
            y_boundary=round(y_b, 6),
            y_row_below=y_low,
            y_row_above=y_high,
            track_count=track_count,
            nets=sorted(set(nets)),
            current_width_um=current_width,
            recommended_width_um=recommended,
        ))

    return channels

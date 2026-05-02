"""
Weighted Routing Cost Function
================================
Computes a weighted cost from per-net HPWL values.

Design choices:
  - All terms use HPWL² so that long wires are penalized disproportionately
    (encourages tight clustering of critical nets).
  - Power nets are included but down-weighted — they carry real EM/IR cost.
  - The raw `weighted_cost` is absolute (scales with circuit size).
  - `cost_per_net` is size-normalized for cross-circuit comparison — but it
    is clearly labelled so it is NOT used as the primary optimization target.

Usage:
    from ai_agent.placement.routing.cost import compute_cost, WEIGHT
"""

from __future__ import annotations
from typing import Dict, List
from ai_agent.placement.routing.hpwl import NetHPWL

# Weights by criticality — all quadratic (HPWL²) for consistent units
WEIGHT: Dict[str, float] = {
    "critical": 10.0,   # differential outputs, clocks — minimize aggressively
    "signal":    3.0,   # general signal nets
    "bias":      1.0,   # bias / tail nets — less sensitive to routing detours
    "power":     0.5,   # VDD/VSS — real cost but not a matching concern
}


def compute_cost(net_hpwls: List[NetHPWL], criticalities: Dict[str, str]) -> Dict[str, float]:
    """
    Compute weighted routing cost metrics.

    Args:
        net_hpwls:     list of NetHPWL results
        criticalities: {net_name: criticality_string}  from classify_net()

    Returns:
        dict with keys:
          'weighted_cost' — absolute quadratic cost (primary metric)
          'cost_per_net'  — normalized for size comparison (secondary)
    """
    total = 0.0
    for nh in net_hpwls:
        crit = criticalities.get(nh.net, "signal")
        w = WEIGHT.get(crit, WEIGHT["signal"])
        total += w * (nh.hpwl ** 2)

    n = max(1, len(net_hpwls))
    return {
        "weighted_cost": round(total, 4),
        "cost_per_net":  round(total / n, 4),
    }

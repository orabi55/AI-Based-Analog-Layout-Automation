"""
RoutingReport — Structured Output Dataclass
============================================
Replaces the unstructured dict returned by the old score_routing().

The legacy dict shape is preserved via to_legacy_dict() for backward
compatibility with existing callers in workers.py, placement_worker.py,
and human_viewer.py without requiring immediate migration.

Usage:
    from ai_agent.placement.routing.report import RoutingReport, build_report
    report = build_report(nodes, edges, terminal_nets)
    legacy = report.to_legacy_dict()       # drop-in replacement for old return value
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from ai_agent.placement.routing.classify  import classify_net
from ai_agent.placement.routing.nets      import build_net_map, LogicalDev
from ai_agent.placement.routing.hpwl      import compute_all_hpwl, total_hpwl, NetHPWL
from ai_agent.placement.routing.crossings import estimate_crossings
from ai_agent.placement.routing.density   import estimate_channel_density, ChannelReport
from ai_agent.placement.routing.cost      import compute_cost


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class NetReport:
    """Per-net routing analysis result."""
    name:         str
    criticality:  str       # 'critical' | 'signal' | 'bias' | 'power'
    devices:      List[str] # logical device IDs
    x_span:       float     # µm
    y_span:       float     # µm
    hpwl:         float     # µm — Manhattan HPWL = x_span + y_span
    cross_row:    bool
    x_min:        float
    x_max:        float
    y_min:        float
    y_max:        float


@dataclass
class RoutingReport:
    """
    Complete deterministic routing preview report.

    Attributes:
        nets               : per-net analysis (all signal/bias/power nets ≥ 2 endpoints)
        channels           : per-band channel density estimates
        total_hpwl_um      : total Manhattan HPWL across all nets (µm)
        estimated_crossings: sweep-line H×V crossing count
        weighted_cost      : absolute quadratic cost (primary metric)
        cost_per_net       : size-normalized cost (for comparison only)
        summary            : human-readable one-line summary
    """
    nets:                List[NetReport]    = field(default_factory=list)
    channels:            List[ChannelReport] = field(default_factory=list)
    total_hpwl_um:       float = 0.0
    estimated_crossings: int   = 0
    weighted_cost:       float = 0.0
    cost_per_net:        float = 0.0
    summary:             str   = ""

    # ── Backward-compat ──────────────────────────────────────────────────────
    def to_legacy_dict(self) -> Dict[str, Any]:
        """
        Return a dict matching the old score_routing() return shape.
        All existing callers (workers.py, placement_worker.py, human_viewer.py)
        can continue to use .get("score"), .get("placement_cost") etc.
        """
        net_spans    = {n.name: (n.x_min, n.x_max)           for n in self.nets}
        net_details  = {
            n.name: {
                "span":        n.x_span,
                "wire_length": n.hpwl,
                "criticality": n.criticality,
                "cross_row":   n.cross_row,
                "devices":     n.devices,
                "pmos_devs":   [d for d in n.devices],   # coarse — no type info here
                "nmos_devs":   [],
                "x_min":       n.x_min,
                "x_max":       n.x_max,
            }
            for n in self.nets
        }
        worst_nets = sorted(
            self.nets,
            key=lambda n: WEIGHT_ORDER.get(n.criticality, 1) * n.hpwl,
            reverse=True,
        )[:5]
        return {
            "score":             self.estimated_crossings,
            "worst_nets":        [n.name for n in worst_nets],
            "net_spans":         net_spans,
            "net_details":       net_details,
            "total_wire_length": self.total_hpwl_um,
            "placement_cost":    self.cost_per_net,
            "weighted_cost":     self.weighted_cost,
            "channels":          [
                {
                    "band_index":           c.band_index,
                    "y_boundary":           c.y_boundary,
                    "track_count":          c.track_count,
                    "nets":                 c.nets,
                    "recommended_width_um": c.recommended_width_um,
                }
                for c in self.channels
            ],
            "summary":           self.summary,
        }

    # ── Formatted terminal log ───────────────────────────────────────────────
    def format_log(self) -> str:
        """
        Return a structured multi-line log string for terminal display.
        Compatible with log_section / log_detail in ai_agent/utils/logging.py.
        """
        n_crit   = sum(1 for n in self.nets if n.criticality == "critical")
        n_sig    = sum(1 for n in self.nets if n.criticality == "signal")
        n_bias   = sum(1 for n in self.nets if n.criticality == "bias")
        n_power  = sum(1 for n in self.nets if n.criticality == "power")
        lines = [
            f"{len(self.nets)} nets analyzed  "
            f"({n_crit} critical, {n_sig} signal, {n_bias} bias, {n_power} power)",
            f"Total Manhattan HPWL : {self.total_hpwl_um:.3f} µm",
            f"Estimated crossings  : {self.estimated_crossings}",
            f"Weighted cost        : {self.weighted_cost:.1f}",
            "",
            "Worst nets (by weighted HPWL):",
        ]
        worst = sorted(self.nets, key=lambda n: WEIGHT_ORDER.get(n.criticality, 1) * n.hpwl,
                       reverse=True)[:5]
        for n in worst:
            cross_tag = "  [CROSS-ROW]" if n.cross_row else ""
            lines.append(
                f"  {n.name:<14} {n.criticality:<8} "
                f"hpwl={n.hpwl:.3f}µm  "
                f"(Δx={n.x_span:.3f}, Δy={n.y_span:.3f}){cross_tag}"
            )
        if self.channels:
            lines.append("")
            lines.append("Routing channels (inter-row bands):")
            for c in self.channels:
                arrow = (
                    f"  → widen to {c.recommended_width_um:.3f} µm"
                    if c.recommended_width_um > c.current_width_um
                    else "  ✓ adequate"
                )
                lines.append(
                    f"  band {c.band_index}  y≈{c.y_boundary:.3f}µm  "
                    f"{c.track_count} track(s){arrow}"
                )
        return "\n".join(lines)


# Cost weight order for worst-net sorting
WEIGHT_ORDER = {"critical": 10, "signal": 3, "bias": 1, "power": 0.5}


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------

def build_report(
    nodes: list,
    edges: Optional[list] = None,
    terminal_nets: Optional[dict] = None,
) -> RoutingReport:
    """
    Build a complete RoutingReport from placement data.

    This is the single entry point for the routing previewer node.
    """
    # 1. Net map with finger aggregation
    net_map, _dev_map = build_net_map(nodes, edges, terminal_nets)

    # 2. Manhattan HPWL per net
    hpwl_map: Dict[str, NetHPWL] = compute_all_hpwl(net_map)

    # 3. Net criticality
    criticalities = {net: classify_net(net) for net in hpwl_map}

    # 4. NetReport list
    net_reports: List[NetReport] = []
    for net, nh in hpwl_map.items():
        devs = net_map.get(net, [])
        net_reports.append(NetReport(
            name=net,
            criticality=criticalities[net],
            devices=[d.logical_id for d in devs],
            x_span=nh.x_span,
            y_span=nh.y_span,
            hpwl=nh.hpwl,
            cross_row=nh.cross_row,
            x_min=nh.x_min,
            x_max=nh.x_max,
            y_min=nh.y_min,
            y_max=nh.y_max,
        ))

    # 5. Crossing estimate (sweep-line)
    crossings = estimate_crossings(list(hpwl_map.values()))

    # 6. Channel density
    channels = estimate_channel_density(list(hpwl_map.values()), nodes)

    # 7. Cost
    costs = compute_cost(list(hpwl_map.values()), criticalities)

    # 8. Total HPWL
    t_hpwl = total_hpwl(list(hpwl_map.values()))

    # 9. Summary
    if crossings == 0 and t_hpwl < 2.0:
        summary = (
            f"Routing preview: 0 crossings, HPWL={t_hpwl:.3f} µm — "
            f"placement well-optimised."
        )
    else:
        worst = sorted(net_reports, key=lambda n: WEIGHT_ORDER.get(n.criticality, 1) * n.hpwl,
                       reverse=True)[:3]
        worst_str = ", ".join(f"{n.name}({n.hpwl:.3f}µm)" for n in worst)
        summary = (
            f"Routing preview: ~{crossings} crossing(s), "
            f"HPWL={t_hpwl:.3f} µm. "
            f"Worst: {worst_str}"
        )

    return RoutingReport(
        nets=net_reports,
        channels=channels,
        total_hpwl_um=t_hpwl,
        estimated_crossings=crossings,
        weighted_cost=costs["weighted_cost"],
        cost_per_net=costs["cost_per_net"],
        summary=summary,
    )

"""
Routing Analysis Core Logic
============================
Canonical implementation of routing preview and scoring.
Extracted from ai_agent/agents/routing_previewer.py — zero logic changes.

Public API:
    build_routing_report(nodes, edges, terminal_nets) -> RoutingReport
    score_routing(nodes, edges, terminal_nets) -> dict
"""

from ai_agent.placement.routing.report import build_report, RoutingReport


def build_routing_report(
    nodes: list,
    edges: list | None = None,
    terminal_nets: dict | None = None,
    *,
    user_critical_nets: set | None = None,
) -> RoutingReport:
    """
    Build a full RoutingReport from placement data.

    This is the preferred entry point for new code.
    Uses:
      - Manhattan HPWL (|Δx| + |Δy|) — correct routing preview metric
      - Finger aggregation — logical device centroids, not per-finger endpoints
      - Regex net classifier — no single-letter false positives
      - Sweep-line crossing estimate — actual geometric crossings, not bbox overlap
      - Per-band channel density — feeds channel_planner node
      - Quadratic weighted cost — all terms in consistent units

    Args:
        nodes:              physical placement node list
        edges:              edge dicts with 'net', 'source', 'target'
        terminal_nets:      {dev_id: {'D':net, 'G':net, 'S':net}}
        user_critical_nets: optional set of net names forced to criticality
                            ``"critical"`` (10× HPWL² weight) regardless of
                            the regex classifier.  Default None = no override
                            (backward-compatible).

    Returns:
        RoutingReport dataclass
    """
    return build_report(nodes, edges, terminal_nets,
                        user_critical_nets=user_critical_nets)


def score_routing(
    nodes: list,
    edges: list | None = None,
    terminal_nets: dict | None = None,
) -> dict:
    """
    Legacy entry point — returns the old dict shape for backward compat.

    Existing callers (workers.py, placement_worker.py, human_viewer.py,
    tools/scoring.py) can use this without modification.

    New code should use build_routing_report() instead.
    """
    report = build_report(nodes, edges, terminal_nets)
    return report.to_legacy_dict()

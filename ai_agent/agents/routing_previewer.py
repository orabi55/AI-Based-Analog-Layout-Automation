"""
Routing Pre-Viewer — Deterministic Scoring Engine
===================================================
Evaluates placement quality using Manhattan HPWL, sweep-line crossings,
and per-channel routing density. Produces a structured RoutingReport.

This module is a PURE-PYTHON OBSERVER — it does NOT mutate placement nodes.
It has no LLM dependency and no system prompt (dead scaffolding removed).

The old format_routing_for_llm() and ROUTING_PREVIEWER_PROMPT have been
removed (they were never called in the pipeline). Routing analysis is now
fully deterministic and expressed in the RoutingReport dataclass.

Public API (backward-compatible):
    score_routing(nodes, edges, terminal_nets) -> dict
        Returns to_legacy_dict() shape for existing callers.

    build_routing_report(nodes, edges, terminal_nets) -> RoutingReport
        Returns the full structured report.
"""

from ai_agent.placement.routing.report import build_report, RoutingReport


def build_routing_report(
    nodes: list,
    edges: list | None = None,
    terminal_nets: dict | None = None,
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
        nodes:         physical placement node list
        edges:         edge dicts with 'net', 'source', 'target'
        terminal_nets: {dev_id: {'D':net, 'G':net, 'S':net}}

    Returns:
        RoutingReport dataclass
    """
    return build_report(nodes, edges, terminal_nets)


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

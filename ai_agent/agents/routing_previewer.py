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

NOTE: build_routing_report and score_routing now live in ai_agent.core.routing.
      This module re-exports them for backwards compatibility.
"""

from ai_agent.core.routing import build_routing_report, score_routing
from ai_agent.placement.routing.report import RoutingReport

__all__ = ["build_routing_report", "score_routing", "RoutingReport"]

"""
Routing Pre-Viewer Node
========================
LangGraph node that evaluates routing complexity of the current placement
using deterministic Manhattan HPWL analysis. Pure observer — no mutations.

Functions:
- node_routing_previewer: runs the routing analysis and updates graph state.
  Inputs:  state (dict) — must contain 'placement_nodes', 'edges', 'terminal_nets'
  Outputs: {'routing_result': dict}  (legacy dict shape for backward compat)
"""

import time
from ai_agent.core.routing import build_routing_report
from ai_agent.nodes._shared import ip_step
from ai_agent.utils.logging import log_section, log_detail


def node_routing_previewer(state: dict) -> dict:
    t0 = time.time()

    nodes         = state.get("placement_nodes", [])
    edges         = state.get("edges", [])
    terminal_nets = state.get("terminal_nets", {})

    # ── Critical-nets override (user-selected via ⚡ panel) ─────────────────
    # When the panel is collapsed or priority=Low the set is None → no override.
    goals    = state.get("placement_goals") or {}
    crit_cfg = goals.get("critical_nets") or {}
    _crit_list = crit_cfg.get("nets") or []
    _priority   = crit_cfg.get("priority", "Low")
    from ai_agent.placement.critical_nets import PRIORITY_WEIGHTS
    _weight = PRIORITY_WEIGHTS.get(_priority, 0)
    user_critical: "set | None" = set(_crit_list) if (_crit_list and _weight > 0) else None

    report = build_routing_report(
        nodes, edges or [], terminal_nets or {},
        user_critical_nets=user_critical,
    )

    # ── Structured terminal log ───────────────────────────────────────────────
    log_section("Routing Pre-Viewer")
    for line in report.format_log().splitlines():
        log_detail(line)

    elapsed = time.time() - t0
    ip_step(
        "5.5/5 Routing Pre-Viewer",
        f"score={report.estimated_crossings}  "
        f"hpwl={report.total_hpwl_um:.3f}µm  "
        f"cost={report.weighted_cost:.1f} ({elapsed:.1f}s)"
    )

    routing_legacy = report.to_legacy_dict()
    routing_legacy["log_text"] = report.format_log()
    return {"routing_result": routing_legacy, "last_agent": "routing_previewer"}


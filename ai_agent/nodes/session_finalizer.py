"""
Session Finalizer Node
======================
A LangGraph node that standardises the ``assistant_text`` returned to the GUI
after any session chatbot, DRC, routing, topology, strategy, or placement node.

Functions:
- node_session_finalizer: Produces a user-facing assistant_text from state.
  - Inputs: state (dict)
  - Outputs: state update with ``assistant_text``.
"""

from __future__ import annotations

import re

from ai_agent.nodes.drc_checker import format_drc_flags
from ai_agent.utils.logging import vprint


# ---------------------------------------------------------------------------
# Internal helpers — deterministic summarisation, no LLM
# ---------------------------------------------------------------------------

def _summarise_drc(state: dict) -> str:
    """Build a human-readable DRC summary from state fields."""
    drc_pass = state.get("drc_pass")
    flags = state.get("drc_flags") or []

    if drc_pass:
        return "DRC check passed for the current placement."

    if flags:
        header = f"DRC check found {len(flags)} issue(s):"
        return header + "\n" + format_drc_flags(flags, max_items=10)

    return "DRC check completed, but no detailed result was provided."


def _summarise_routing(state: dict) -> str:
    """Extract a readable routing summary from the legacy routing_result dict."""
    routing = state.get("routing_result") or {}
    return (
        routing.get("log_text")
        or routing.get("summary")
        or "Routing preview completed."
    )


def _summarise_fix_routing(state: dict) -> str:
    """Produce actionable routing-optimization recommendations.

    Unlike the read-only ``_summarise_routing``, this version:
    * Highlights specific target nets (from ``target_nets``).
    * Includes concrete suggestions (move connected devices closer,
      reduce crossings, preserve differential symmetry).
    * Clearly states whether any layout changes were applied.
    """
    routing = state.get("routing_result") or {}
    target_nets = [
        str(n).strip()
        for n in (state.get("layout_session_target_nets") or state.get("target_nets") or [])
        if str(n).strip()
    ]
    report = (routing.get("log_text") or routing.get("summary")) if isinstance(routing, dict) else ""
    net_details = routing.get("net_details") if isinstance(routing, dict) else {}
    worst_nets = routing.get("worst_nets") if isinstance(routing, dict) else []
    worst_upper = {str(n).upper() for n in worst_nets or []}

    lines: list[str] = []
    if target_nets:
        net_list = ", ".join(target_nets)
        lines.append(f"I analyzed {net_list} for routing/parasitic reduction.")
    else:
        lines.append("I analyzed routing/parasitic optimization opportunities.")

    if target_nets and isinstance(net_details, dict):
        observations: list[str] = []
        for net in target_nets:
            detail = None
            real_name = net
            for key, value in net_details.items():
                if str(key).upper() == net.upper() and isinstance(value, dict):
                    detail = value
                    real_name = str(key)
                    break
            if not detail:
                continue
            tags: list[str] = []
            if real_name.upper() in worst_upper:
                tags.append("one of the worst HPWL nets")
            if detail.get("cross_row"):
                tags.append("cross-row")
            hpwl = detail.get("wire_length") or detail.get("span")
            tag_text = f" ({', '.join(tags)})" if tags else ""
            if hpwl is not None:
                try:
                    observations.append(f"- {real_name}: HPWL={float(hpwl):.3f} um{tag_text}.")
                except (TypeError, ValueError):
                    observations.append(f"- {real_name}: targeted net{tag_text}.")
            else:
                observations.append(f"- {real_name}: targeted net{tag_text}.")
        if observations:
            lines.append("Target-net observations:")
            lines.extend(observations)

    lines.append("Recommendations:")
    if target_nets:
        lines.append(f"1. Keep devices connected to {', '.join(target_nets)} closer to reduce HPWL/parasitics.")
    else:
        lines.append("1. Keep connected devices closer to reduce HPWL/parasitics.")
    if len(target_nets) >= 2:
        lines.append(f"2. Optimize {target_nets[0]} and {target_nets[1]} symmetrically to preserve differential balance.")
    else:
        lines.append("2. Keep matched/differential routes symmetric.")
    lines.append("3. Reduce local crossings and avoid unnecessary cross-row routes near output/load devices.")
    lines.append("No layout changes were applied automatically.")
    if report:
        lines.append("")
        lines.append("Raw routing report:")
        lines.append(str(report))

    return "\n".join(lines)


def _summarise_topology(state: dict) -> str:
    """Return the analysis result if available, else a generic message."""
    analysis = state.get("Analysis_result")
    if analysis and isinstance(analysis, str) and analysis.strip():
        # Truncate very long analysis to keep the chat widget readable
        return analysis[:2000]
    return "Topology analysis completed."


def _summarise_strategy(state: dict) -> str:
    """Return the strategy result if available, else a generic message."""
    strategy = state.get("strategy_result")
    if strategy and isinstance(strategy, str) and strategy.strip():
        return strategy[:2000]
    return "Strategy analysis completed."


# ---------------------------------------------------------------------------
# Route → summariser map
# ---------------------------------------------------------------------------

_ROUTE_SUMMARISERS: dict[str, callable] = {
    "need_drc":       _summarise_drc,
    "fix_drc":        _summarise_drc,
    "need_routing":   _summarise_routing,
    "fix_routing":    _summarise_fix_routing,
    "need_topology":  _summarise_topology,
    "need_strategy":  _summarise_strategy,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def node_session_finalizer(state: dict) -> dict:
    """Produce a final ``assistant_text`` for the GUI.

    Priority (revised — specialist results always win):
    1. For specialist routes (need_strategy, need_drc, etc.), always prefer
       the specialist summariser over any stale router placeholder text.
    2. For non-specialist routes, use existing ``assistant_text`` if non-empty.
    3. Build a deterministic summary from route-specific state fields.
    4. Fall back to a generic "Done." message.

    This node never generates layout commands and does not call the LLM.
    """
    vprint("[FINALIZER] Building assistant text", flush=True)

    route = state.get("session_route")
    layout_decision = state.get("layout_session_decision")
    summariser = _ROUTE_SUMMARISERS.get(route)

    # chat_v2 may reach finalizer without session_route.
    if summariser is None and isinstance(layout_decision, str):
        decision_map = {
            "check_drc": _summarise_drc,
            "fix_drc": _summarise_drc,
            "check_routing": _summarise_routing,
            "optimize_routing": _summarise_fix_routing,
        }
        summariser = decision_map.get(layout_decision)

    # For specialist routes, always try the summariser FIRST.
    # The router may have left a placeholder or None in assistant_text —
    # the specialist's own output (strategy_result, Analysis_result, etc.)
    # is the authoritative answer.
    text = ""
    if summariser is not None:
        specialist_text = summariser(state)
        # Use the specialist result unless it's a generic fallback
        if specialist_text and not specialist_text.endswith("completed."):
            text = specialist_text

    # If no specialist result, try existing assistant_text
    if not text:
        text = str(state.get("assistant_text") or "").strip()

    # Never surface specialist handoff placeholders.
    if text and re.search(r"\b(delegate|handoff|strategy_selector|topology_analyst|placement_specialist|routing_previewer|drc_critic)\b", text, re.IGNORECASE):
        text = ""

    # If still empty, try the summariser's generic fallback or route defaults
    if not text:
        if summariser is not None:
            text = summariser(state)

        elif route == "clarify":
            text = "I need a little more detail before changing the layout."

        elif route == "answer_only":
            text = "Done."

        else:
            text = "Done."

    vprint(f"[FINALIZER] route={route or layout_decision}  text_len={len(text)}", flush=True)

    return {"assistant_text": text}

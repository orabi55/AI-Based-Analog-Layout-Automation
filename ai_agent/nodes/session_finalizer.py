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
        lines = []
        for f in flags[:10]:
            if isinstance(f, dict):
                # Prefer a human-readable description key
                desc = (
                    f.get("description")
                    or f.get("message")
                    or f.get("value")
                    or str(f)
                )
            else:
                desc = str(f)
            lines.append(f"- {desc}")
        header = f"DRC check found {len(flags)} issue(s):"
        if len(flags) > 10:
            lines.append(f"- … and {len(flags) - 10} more.")
        return header + "\n" + "\n".join(lines)

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
    target_nets = state.get("target_nets") or []

    lines: list[str] = []

    # Routing density / HPWL report
    report = routing.get("log_text") or routing.get("summary")
    if report:
        lines.append(str(report))
    else:
        lines.append("Routing analysis completed.")

    # Target-net-specific recommendations
    if target_nets:
        net_list = ", ".join(target_nets)
        lines.append(f"")
        lines.append(f"Target nets: {net_list}")
        lines.append("")
        lines.append("Recommendations to reduce parasitics:")
        lines.append(
            f"• Move devices connected to {net_list} closer together "
            f"to reduce HPWL."
        )
        lines.append(
            "• Minimize the number of cross-row routing segments "
            "for these nets."
        )
        if len(target_nets) >= 2:
            lines.append(
                f"• Keep {target_nets[0]} and {target_nets[1]} routing "
                f"symmetric to preserve differential balance."
            )
        lines.append(
            "• Consider using \"move <device> left/right\" commands to "
            "adjust placement manually, or ask me to propose a "
            "placement adjustment."
        )
    else:
        lines.append("")
        lines.append(
            "To target specific nets, try: "
            '"reduce parasitics on VOUTP and VOUTN."'
        )

    lines.append("")
    lines.append(
        "⚠ No layout changes were applied automatically. "
        "Use placement commands to act on these recommendations."
    )

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
    if text and re.search(r"\b(delegate|handoff|strategy_selector|topology_analyst|placement_specialist)\b", text, re.IGNORECASE):
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

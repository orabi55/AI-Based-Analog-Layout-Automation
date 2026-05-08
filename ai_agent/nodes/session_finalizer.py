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
    "need_topology":  _summarise_topology,
    "need_strategy":  _summarise_strategy,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def node_session_finalizer(state: dict) -> dict:
    """Produce a final ``assistant_text`` for the GUI.

    Priority:
    1. Use existing ``state["assistant_text"]`` if non-empty.
    2. Build a deterministic summary from route-specific state fields.
    3. Fall back to a generic "Done." message.

    This node never generates layout commands and does not call the LLM.
    """
    vprint("[FINALIZER] Building assistant text", flush=True)

    text = str(state.get("assistant_text") or "").strip()

    if not text:
        route = state.get("session_route")
        summariser = _ROUTE_SUMMARISERS.get(route)

        if summariser is not None:
            text = summariser(state)

        elif route == "clarify":
            text = "I need a little more detail before changing the layout."

        elif route == "answer_only":
            text = "Done."

        else:
            text = "Done."

    vprint(f"[FINALIZER] route={state.get('session_route')}  text_len={len(text)}", flush=True)

    return {"assistant_text": text}

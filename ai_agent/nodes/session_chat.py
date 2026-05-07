"""
Session Chat Node
=================
A LangGraph node that wraps the session chat agent to classify user messages
and produce routing decisions for the interactive chatbot flow.

Functions:
- node_session_chat: Invokes the session chat agent and returns state updates.
  - Inputs: state (dict)
  - Outputs: state update with session_route, route_confidence, assistant_text,
             pending_cmds, session_commands, requires_specialist, specialist_target,
             and updated chat_history.
"""

from __future__ import annotations

from ai_agent.agents.session_chat_agent import run_session_chat_agent, normalize_route
from ai_agent.utils.logging import vprint


# ---------------------------------------------------------------------------
# Lightweight chat-history helpers (avoids importing _shared at module level
# which pulls in langchain via skill_injector).
# ---------------------------------------------------------------------------

def _append_assistant_turn(
    chat_history: list,
    user_message: str,
    assistant_text: str,
) -> list:
    """Return a copy of *chat_history* with the user + assistant turn appended.

    This is intentionally a minimal implementation.  The heavy persistence
    logic in :mod:`ai_agent.nodes._shared` (JSON save, dedup, normalisation)
    is called by the downstream finaliser node, so we only need to capture
    the turns in-memory here.
    """
    history = list(chat_history)
    if user_message:
        history.append({"role": "user", "content": user_message})
    if assistant_text:
        history.append({"role": "assistant", "content": assistant_text})
    return history


def node_session_chat(state: dict) -> dict:
    """Classify a user message and return session routing state updates.

    This node is the single entry point for the session chatbot branch of
    the LangGraph state machine.  It delegates all classification logic to
    :func:`run_session_chat_agent` (which itself applies the deterministic
    rule router first and falls back to the LLM).

    The node guarantees:
    * ``session_route`` is always a valid member of
      :data:`VALID_SESSION_ROUTES`.
    * ``pending_cmds`` is populated **only** for ``command_edit`` so the
      existing ``human_viewer`` / command-validation pipeline stays
      compatible.
    * An assistant message is appended to ``chat_history`` so downstream
      nodes and the GUI can display it.
    * No exception escapes to the graph — any internal failure is
      mapped to ``clarify``.
    """
    vprint("\n" + "─" * 60, flush=True)
    vprint("  SESSION CHAT NODE", flush=True)
    vprint("─" * 60, flush=True)

    # -- Invoke the session chat agent (safe — it handles its own errors) ----
    try:
        result = run_session_chat_agent(state)
    except Exception as exc:
        vprint(f"[SESSION] ✗ Agent crashed: {exc}", flush=True)
        result = {
            "session_route": "clarify",
            "route_confidence": 0.0,
            "assistant_text": "Something went wrong — could you rephrase?",
            "session_reason": f"Agent exception: {exc}",
        }

    # -- Normalise & extract fields ------------------------------------------
    route      = normalize_route(result.get("session_route"))
    confidence = float(result.get("route_confidence", 0.0))
    reason     = result.get("session_reason") or result.get("reason", "")
    text       = result.get("assistant_text", "")
    commands   = (
        result.get("session_commands")
        or result.get("pending_cmds")
        or []
    )

    vprint(
        f"[SESSION] route={route}  confidence={confidence:.2f}  "
        f"specialist={result.get('specialist_target')}",
        flush=True,
    )

    # -- Build state update --------------------------------------------------
    update: dict = {
        "session_route":       route,
        "route_confidence":    round(confidence, 4),
        "session_reason":      str(reason),
        "assistant_text":      str(text),
        "requires_specialist": bool(result.get("requires_specialist", False)),
        "specialist_target":   result.get("specialist_target"),
    }

    # command_edit → publish commands in both keys for back-compat
    if route == "command_edit":
        update["session_commands"] = list(commands)
        update["pending_cmds"]    = list(commands)

    # -- Append assistant turn to chat history --------------------------------
    chat_history = list(state.get("chat_history") or [])
    user_message = str(state.get("user_message") or "").strip()
    update["chat_history"] = _append_assistant_turn(
        chat_history, user_message, text or f"Routing as {route}.",
    )

    vprint(f"[SESSION] ✓ Done — route={route}", flush=True)
    return update

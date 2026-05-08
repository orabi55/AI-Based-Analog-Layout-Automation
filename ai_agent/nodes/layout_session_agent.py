"""
LangGraph node wrapper for the AI-first layout session agent.
"""

from __future__ import annotations

from ai_agent.agents.layout_session_agent import run_layout_session_agent


def node_layout_session_agent(state: dict) -> dict:
    """Invoke run_layout_session_agent and normalize a state update."""
    try:
        result = run_layout_session_agent(state)
    except Exception as exc:
        return {
            "layout_session_decision": "clarify",
            "layout_session_confidence": 0.0,
            "layout_session_reason": f"layout_session_agent failed: {exc}",
            "assistant_text": "Something went wrong while understanding that request. Could you rephrase it?",
            "pending_cmds": [],
            "session_commands": [],
        }

    update = {
        "layout_session_decision": result.get("layout_session_decision", "clarify"),
        "layout_session_confidence": result.get("layout_session_confidence", 0.0),
        "layout_session_reason": result.get("layout_session_reason", ""),
        "assistant_text": result.get("assistant_text") or "",
        "layout_session_tool_name": result.get("layout_session_tool_name"),
        "layout_session_tool_args": result.get("layout_session_tool_args") or {},
        "layout_session_specialist": result.get("layout_session_specialist"),
        "layout_session_specialist_question": result.get("layout_session_specialist_question"),
        "layout_session_memory_update": result.get("layout_session_memory_update") or {},
        "layout_session_raw_json": result.get("layout_session_raw_json") or {},
        "layout_session_target_nets": result.get("layout_session_target_nets") or [],
        "layout_session_target_devices": result.get("layout_session_target_devices") or [],
        "layout_session_needs_synthesis": bool(result.get("layout_session_needs_synthesis", False)),
    }

    if "target_nets" in result:
        update["target_nets"] = result.get("target_nets") or []
    if "target_devices" in result:
        update["target_devices"] = result.get("target_devices") or []

    if "pending_edit_intent" in result:
        update["pending_edit_intent"] = result.get("pending_edit_intent")

    if update["layout_session_decision"] == "propose_commands":
        commands = result.get("session_commands") or result.get("pending_cmds") or []
        if isinstance(commands, list):
            filtered = [cmd for cmd in commands if isinstance(cmd, dict) and cmd.get("action")]
        else:
            filtered = []
        update["session_commands"] = filtered
        update["pending_cmds"] = filtered

    if update["layout_session_decision"] in {"answer", "clarify"} and update["assistant_text"]:
        history = list(state.get("chat_history") or [])
        user_message = str(state.get("user_message") or "").strip()
        if user_message:
            history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": update["assistant_text"]})
        update["chat_history"] = history

    return update

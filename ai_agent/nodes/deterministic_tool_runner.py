"""
Deterministic Tool Runner Node
==============================
A LangGraph node that executes one deterministic tool requested by the
AI-first layout session agent.

The layout_session_agent decides *which* tool to call and provides
arguments.  This node runs the tool and converts its output into a state
update that downstream nodes (command_validator, session_finalizer) can
consume.

Functions:
- node_deterministic_tool_runner: Runs a named deterministic tool.
  - Inputs: state (dict) with layout_session_tool_name, layout_session_tool_args
  - Outputs: state update with tool result, possibly commands or assistant_text.
"""

from __future__ import annotations

from ai_agent.agents.layout_session_agent import VALID_DETERMINISTIC_TOOLS
from ai_agent.utils.logging import vprint


# ---------------------------------------------------------------------------
# Tool dispatch table
# ---------------------------------------------------------------------------

def _run_parse_direct_edit_command(state: dict, tool_args: dict) -> dict:
    """Run ``parse_direct_edit_command`` and return a state update.

    If commands are produced, promote the decision to ``propose_commands``
    so the downstream command_validator / human_viewer pipeline activates.
    If no commands, route to ``clarify`` so the user gets feedback.
    """
    from ai_agent.agents.session_chat_agent import (
        parse_direct_edit_command,
        _build_partial_move_intent,
    )

    user_message = str(
        tool_args.get("message")
        or tool_args.get("user_message")
        or state.get("user_message", "")
    )
    placement_nodes = (
        tool_args.get("placement_nodes")
        or state.get("placement_nodes")
        or state.get("nodes")
        or []
    )

    commands = parse_direct_edit_command(user_message, placement_nodes)

    if commands:
        return {
            "layout_session_decision": "propose_commands",
            "session_commands": commands,
            "pending_cmds": commands,
            "assistant_text": (
                f"I prepared the requested layout edit: "
                f"{commands[0].get('action', 'edit')} on "
                f"{commands[0].get('device_id') or commands[0].get('device_a') or commands[0].get('target', 'device')}."
            ),
            "deterministic_tool_result": {
                "tool": "parse_direct_edit_command",
                "status": "ok",
                "commands": commands,
            },
        }

    # No commands — check for partial intent (slot-filling support)
    partial = _build_partial_move_intent(user_message)
    if partial:
        missing_fields = partial.get("missing", [])
        action = partial.get("action", "edit")
        return {
            "layout_session_decision": "clarify",
            "session_commands": [],
            "pending_cmds": [],
            "assistant_text": f"Which device do you want to {action}?",
            "pending_edit_intent": partial,
            "deterministic_tool_result": {
                "tool": "parse_direct_edit_command",
                "status": "partial",
                "missing": missing_fields,
            },
        }

    return {
        "layout_session_decision": "clarify",
        "session_commands": [],
        "pending_cmds": [],
        "assistant_text": (
            "I understood the edit request, but I need the target device "
            "and operation. For example: \"move M1 left\" or \"swap M1 and M2\"."
        ),
        "deterministic_tool_result": {
            "tool": "parse_direct_edit_command",
            "status": "no_commands",
        },
    }


def _run_try_fill_edit_slots(state: dict, tool_args: dict) -> dict:
    """Run ``try_fill_edit_slots`` and return a state update.

    Completes a previous partial edit intent from the current user message.
    """
    from ai_agent.agents.session_chat_agent import try_fill_edit_slots

    user_message = str(
        tool_args.get("message")
        or tool_args.get("user_message")
        or state.get("user_message", "")
    )
    pending_intent = (
        tool_args.get("pending_edit_intent")
        or state.get("pending_edit_intent")
    )
    placement_nodes = (
        tool_args.get("placement_nodes")
        or state.get("placement_nodes")
        or state.get("nodes")
        or []
    )

    if not pending_intent or not isinstance(pending_intent, dict):
        return {
            "layout_session_decision": "clarify",
            "assistant_text": "There is no pending edit to complete.",
            "deterministic_tool_result": {
                "tool": "try_fill_edit_slots",
                "status": "no_pending_intent",
            },
        }

    filled_cmd = try_fill_edit_slots(user_message, pending_intent, placement_nodes)

    if filled_cmd:
        action = filled_cmd.get("action", "edit")
        device = filled_cmd.get("device_id", "device")
        return {
            "layout_session_decision": "propose_commands",
            "session_commands": [filled_cmd],
            "pending_cmds": [filled_cmd],
            "assistant_text": f"Executing: {action} on {device}.",
            "pending_edit_intent": None,  # clear the pending intent
            "deterministic_tool_result": {
                "tool": "try_fill_edit_slots",
                "status": "ok",
                "commands": [filled_cmd],
            },
        }

    return {
        "layout_session_decision": "clarify",
        "assistant_text": (
            "I could not fill the missing information from your message. "
            "Please specify the target device."
        ),
        "deterministic_tool_result": {
            "tool": "try_fill_edit_slots",
            "status": "could_not_fill",
        },
    }


def _run_extract_target_nets(state: dict, tool_args: dict) -> dict:
    """Run ``_extract_target_nets`` and return a state update.

    Extracts net names from the user message for routing optimization.
    """
    from ai_agent.agents.session_chat_agent import _extract_target_nets

    user_message = str(
        tool_args.get("message")
        or tool_args.get("user_message")
        or state.get("user_message", "")
    )

    target_nets = _extract_target_nets(user_message)

    if target_nets:
        return {
            "layout_session_target_nets": target_nets,
            "target_nets": target_nets,
            "assistant_text": "",  # synthesizer will produce the final answer
            "deterministic_tool_result": {
                "tool": "extract_target_nets",
                "status": "ok",
                "target_nets": target_nets,
            },
        }

    return {
        "layout_session_decision": "clarify",
        "assistant_text": (
            "Which nets should I optimize for parasitics? "
            "For example: \"reduce parasitics on VOUTP and VOUTN.\""
        ),
        "deterministic_tool_result": {
            "tool": "extract_target_nets",
            "status": "no_nets_found",
        },
    }


def _run_answer_from_initial_trace(state: dict, tool_args: dict) -> dict:
    """Run ``answer_from_initial_trace`` and return a direct answer.

    Builds an informative answer from the initial placement agent trace.
    """
    from ai_agent.agents.session_chat_agent import answer_from_initial_trace

    user_message = str(
        tool_args.get("message")
        or tool_args.get("user_message")
        or state.get("user_message", "")
    )
    initial_trace = (
        tool_args.get("initial_agent_trace")
        or state.get("initial_agent_trace")
        or {}
    )
    placement_nodes = (
        tool_args.get("placement_nodes")
        or state.get("placement_nodes")
        or state.get("nodes")
        or []
    )

    answer = answer_from_initial_trace(user_message, initial_trace, placement_nodes)

    return {
        "layout_session_decision": "answer",
        "assistant_text": answer,
        "deterministic_tool_result": {
            "tool": "answer_from_initial_trace",
            "status": "ok",
        },
    }


def _run_rule_route(state: dict, tool_args: dict) -> dict:
    """Run ``rule_route`` as a debug/fallback tool.

    Returns the deterministic route but does NOT act on it — the
    layout_session_agent should use this only for diagnostics.
    """
    from ai_agent.agents.session_chat_agent import rule_route

    user_message = str(
        tool_args.get("message")
        or tool_args.get("user_message")
        or state.get("user_message", "")
    )

    route = rule_route(user_message)

    return {
        "assistant_text": "",  # no user-facing text from debug tool
        "deterministic_tool_result": {
            "tool": "rule_route",
            "status": "ok",
            "route": route,
        },
    }


# ---------------------------------------------------------------------------
# Tool dispatch map
# ---------------------------------------------------------------------------

_TOOL_DISPATCH: dict[str, callable] = {
    "parse_direct_edit_command": _run_parse_direct_edit_command,
    "try_fill_edit_slots":       _run_try_fill_edit_slots,
    "extract_target_nets":       _run_extract_target_nets,
    "answer_from_initial_trace": _run_answer_from_initial_trace,
    "rule_route":                _run_rule_route,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def node_deterministic_tool_runner(state: dict) -> dict:
    """Execute a single deterministic tool requested by the layout session agent.

    Reads ``layout_session_tool_name`` and ``layout_session_tool_args``
    from state, dispatches to the appropriate tool function, and returns
    a state update.

    If the tool name is invalid or the tool crashes, the node returns a
    safe ``clarify`` response rather than raising an exception.
    """
    vprint("\n[TOOL_RUNNER] Deterministic tool runner invoked", flush=True)

    tool_name = state.get("layout_session_tool_name")
    tool_args = state.get("layout_session_tool_args") or {}

    if not tool_name:
        vprint("[TOOL_RUNNER] ✗ No tool name in state", flush=True)
        return {
            "layout_session_decision": "clarify",
            "assistant_text": "No deterministic tool was specified.",
            "deterministic_tool_result": {"status": "error", "message": "no tool name"},
        }

    if tool_name not in VALID_DETERMINISTIC_TOOLS:
        vprint(f"[TOOL_RUNNER] ✗ Unknown tool: {tool_name!r}", flush=True)
        return {
            "layout_session_decision": "clarify",
            "assistant_text": f"Unknown tool '{tool_name}'.",
            "deterministic_tool_result": {"status": "error", "message": f"unknown tool: {tool_name}"},
        }

    handler = _TOOL_DISPATCH.get(tool_name)
    if not handler:
        vprint(f"[TOOL_RUNNER] ✗ No handler for tool: {tool_name!r}", flush=True)
        return {
            "layout_session_decision": "clarify",
            "assistant_text": f"Tool '{tool_name}' is registered but not implemented.",
            "deterministic_tool_result": {"status": "error", "message": f"no handler: {tool_name}"},
        }

    vprint(f"[TOOL_RUNNER] Running: {tool_name}  args={tool_args}", flush=True)

    try:
        result = handler(state, tool_args)
    except Exception as exc:
        vprint(f"[TOOL_RUNNER] ✗ Tool crashed: {exc}", flush=True)
        return {
            "layout_session_decision": "clarify",
            "assistant_text": f"The tool '{tool_name}' encountered an error: {exc}",
            "deterministic_tool_result": {
                "tool": tool_name,
                "status": "error",
                "message": str(exc),
            },
        }

    vprint(
        f"[TOOL_RUNNER] ✓ {tool_name} completed — "
        f"decision={result.get('layout_session_decision', '(unchanged)')}",
        flush=True,
    )
    return result

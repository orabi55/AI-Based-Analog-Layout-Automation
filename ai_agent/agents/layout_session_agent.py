"""
AI-first layout session agent for chat_v2.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from ai_agent.tools.command_schema import SUPPORTED_COMMAND_ACTIONS

SUPPORTED_COMMAND_ACTIONS_LIST = sorted(SUPPORTED_COMMAND_ACTIONS)

VALID_LAYOUT_SESSION_DECISIONS: frozenset[str] = frozenset({
    "answer",
    "clarify",
    "call_deterministic_tool",
    "propose_commands",
    "call_specialist",
    "check_drc",
    "fix_drc",
    "check_routing",
    "optimize_routing",
})

VALID_DETERMINISTIC_TOOLS: frozenset[str] = frozenset({
    "rule_route",
    "parse_direct_edit_command",
    "try_fill_edit_slots",
    "extract_target_nets",
    "answer_from_initial_trace",
})

VALID_SPECIALISTS: frozenset[str] = frozenset({
    "topology_analyst",
    "strategy_selector",
    "placement_specialist",
    "drc_critic",
    "routing_previewer",
})

CONFIDENCE_THRESHOLD = 0.60

_LAYOUT_SESSION_PROMPT_TEMPLATE = """You are the main interactive analog layout session agent.

You are given:
- current user message
- chat history
- current layout summary
- initial placement trace from topology, strategy, placement, DRC, and routing
- pending edit memory
- supported layout command schema
- deterministic tools available
- specialist agents available

Your responsibilities:
1. Understand the user's intent.
2. Answer directly if possible using current layout and initial placement trace.
3. Use deterministic tools for parsing simple edits, slot filling, target-net extraction, and trace-based answers.
4. Call exactly one specialist only when deeper topology/strategy/placement/DRC/routing analysis is needed.
5. If the user asks for a small layout edit, either call parse_direct_edit_command or propose supported commands directly.
6. If the user asks to check DRC, choose check_drc.
7. If the user asks to fix DRC, choose fix_drc.
8. If the user asks to check routing/parasitics, choose check_routing.
9. If the user asks to reduce/optimize routing/parasitics/wirelength/crossings, choose optimize_routing.
10. If ambiguous, choose clarify and ask a specific question.
11. Do not output unsupported commands.
12. Do not modify layout directly.
13. Preserve matching, symmetry, row legality, and finger integrity.
14. Do not show delegation placeholders to the user. If a specialist is needed, set specialist fields and leave assistant_text empty.

Allowed decisions:
- answer
- clarify
- call_deterministic_tool
- propose_commands
- call_specialist
- check_drc
- fix_drc
- check_routing
- optimize_routing

Allowed deterministic tools:
- rule_route
- parse_direct_edit_command
- try_fill_edit_slots
- extract_target_nets
- answer_from_initial_trace

Allowed specialists:
- topology_analyst
- strategy_selector
- placement_specialist
- drc_critic
- routing_previewer

Return strict JSON only:
{
  "decision": "answer | clarify | call_deterministic_tool | propose_commands | call_specialist | check_drc | fix_drc | check_routing | optimize_routing",
  "confidence": 0.0,
  "reason": "short reason",
  "assistant_text": "text to show only for answer/clarify or safe direct responses",
  "tool_name": null,
  "tool_args": {},
  "specialist": null,
  "specialist_question": null,
  "commands": [],
  "target_nets": [],
  "memory_update": {}
}
"""


def build_layout_summary(
    state: dict | list,
    edges: Optional[list] = None,
    terminal_nets: Optional[dict] = None,
) -> str:
    """Summarize current placement context."""
    if isinstance(state, list):
        nodes = state
    elif isinstance(state, dict):
        nodes = state.get("placement_nodes") or state.get("nodes") or []
        if edges is None:
            edges = state.get("edges")
        if terminal_nets is None:
            terminal_nets = state.get("terminal_nets")
    else:
        nodes = []
    if not isinstance(nodes, list) or not nodes:
        return "No placement nodes available."

    pmos = 0
    nmos = 0
    ids: list[str] = []
    xs: list[float] = []
    ys: list[float] = []

    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id") or node.get("device_id") or node.get("name")
        if node_id:
            ids.append(str(node_id))
        dev_type = str(node.get("type", "")).lower()
        if dev_type.startswith("p"):
            pmos += 1
        else:
            nmos += 1
        geom = node.get("geometry") if isinstance(node.get("geometry"), dict) else node
        x = geom.get("x")
        y = geom.get("y")
        if x is not None:
            xs.append(float(x))
        if y is not None:
            ys.append(float(y))

    lines = [f"{len(nodes)} devices ({pmos} PMOS, {nmos} NMOS)"]
    if xs and ys:
        lines.append(f"bounds: x=[{min(xs):.3f}, {max(xs):.3f}] y=[{min(ys):.3f}, {max(ys):.3f}]")
    if ids:
        shown = ", ".join(ids[:24])
        if len(ids) > 24:
            shown += f", ... (+{len(ids) - 24})"
        lines.append(f"ids: {shown}")
    if isinstance(edges, list):
        lines.append(f"edges={len(edges)} connections")
    if isinstance(terminal_nets, dict):
        lines.append(f"terminal_nets={len(terminal_nets)}")
    return "\n".join(lines)


def build_initial_trace_summary(initial_agent_trace: dict) -> str:
    """Summarize initial-placement trace."""
    if not isinstance(initial_agent_trace, dict) or not initial_agent_trace:
        return "No initial placement trace available."

    lines: list[str] = []
    topology = initial_agent_trace.get("topology")
    strategy = initial_agent_trace.get("strategy")
    drc = initial_agent_trace.get("drc") or {}
    routing = initial_agent_trace.get("routing")

    if topology:
        lines.append(f"topology: {str(topology)[:300]}")
    if strategy:
        lines.append(f"strategy: {str(strategy)[:400]}")
    lines.append(f"DRC: {'PASS' if bool(drc.get('pass', False)) else 'FAIL'}")
    if isinstance(drc.get("flags"), list):
        lines.append(f"drc_flags: {len(drc.get('flags') or [])}")
    if routing:
        lines.append(f"routing: {str(routing)[:250]}")

    return "\n".join(lines)


def build_trace_summary(initial_agent_trace: dict) -> str:
    """Backward-compatible alias used by existing tests."""
    return build_initial_trace_summary(initial_agent_trace)


def build_supported_command_summary() -> str:
    """Summarize supported command actions."""
    return ", ".join(sorted(SUPPORTED_COMMAND_ACTIONS))


def parse_layout_session_json(raw_text: str) -> dict:
    """Parse strict JSON from model text, handling fenced JSON."""
    if not raw_text or not isinstance(raw_text, str):
        return {}

    cleaned = re.sub(r"```(?:json)?", "", raw_text, flags=re.IGNORECASE).replace("```", "").strip()
    if not cleaned:
        return {}

    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def normalize_layout_session_decision(decision: str | None) -> str:
    """Normalize decision into allowed set, default clarify."""
    if not isinstance(decision, str):
        return "clarify"
    token = decision.strip().lower()
    if token in VALID_LAYOUT_SESSION_DECISIONS:
        return token
    return "clarify"


def _safe_confidence(value: Any) -> float:
    try:
        conf = float(value)
    except (TypeError, ValueError):
        return 0.0
    if conf < 0.0:
        return 0.0
    if conf > 1.0:
        return 1.0
    return conf


def _base_result() -> dict:
    return {
        "layout_session_decision": "clarify",
        "layout_session_confidence": 0.0,
        "layout_session_reason": "",
        "assistant_text": "",
        "layout_session_tool_name": None,
        "layout_session_tool_args": {},
        "layout_session_specialist": None,
        "layout_session_specialist_question": None,
        "session_commands": [],
        "pending_cmds": [],
        "layout_session_target_nets": [],
        "layout_session_memory_update": {},
        "layout_session_raw_json": {},
        "layout_session_needs_synthesis": False,
    }


def normalize_layout_session_result(parsed: dict, state: dict) -> dict:
    """Normalize parsed LLM JSON into graph state fields."""
    out = _base_result()
    parsed = parsed if isinstance(parsed, dict) else {}

    decision = normalize_layout_session_decision(parsed.get("decision"))
    confidence = _safe_confidence(parsed.get("confidence", 0.0))
    reason = str(parsed.get("reason") or "")
    assistant_text = str(parsed.get("assistant_text") or "")

    if confidence < CONFIDENCE_THRESHOLD:
        decision = "clarify"
        if not assistant_text:
            assistant_text = "I need one more detail to proceed safely."

    out["layout_session_decision"] = decision
    out["layout_session_confidence"] = confidence
    out["layout_session_reason"] = reason
    out["assistant_text"] = assistant_text
    out["layout_session_raw_json"] = parsed

    memory_update = parsed.get("memory_update")
    if isinstance(memory_update, dict):
        out["layout_session_memory_update"] = memory_update

    raw_targets = parsed.get("target_nets")
    if isinstance(raw_targets, list):
        out["layout_session_target_nets"] = [str(x) for x in raw_targets if str(x).strip()]
    elif isinstance(memory_update, dict) and isinstance(memory_update.get("target_nets"), list):
        out["layout_session_target_nets"] = [
            str(x) for x in memory_update.get("target_nets") if str(x).strip()
        ]

    if decision == "call_deterministic_tool":
        tool_name = parsed.get("tool_name")
        tool_args = parsed.get("tool_args") if isinstance(parsed.get("tool_args"), dict) else {}
        if tool_name not in VALID_DETERMINISTIC_TOOLS:
            out["layout_session_decision"] = "clarify"
            out["assistant_text"] = "I could not map that request to a safe deterministic tool."
        else:
            out["layout_session_tool_name"] = tool_name
            out["layout_session_tool_args"] = tool_args

    if decision == "call_specialist":
        specialist = parsed.get("specialist")
        if specialist not in VALID_SPECIALISTS:
            out["layout_session_decision"] = "clarify"
            out["assistant_text"] = "I need a bit more context before deeper analysis."
        else:
            out["layout_session_specialist"] = specialist
            out["layout_session_specialist_question"] = str(
                parsed.get("specialist_question") or state.get("user_message") or ""
            )
            out["assistant_text"] = ""

    if decision == "propose_commands":
        commands = parsed.get("commands")
        valid_commands: list[dict] = []
        if isinstance(commands, list):
            for cmd in commands:
                if isinstance(cmd, dict) and cmd.get("action") in SUPPORTED_COMMAND_ACTIONS:
                    valid_commands.append(cmd)
        if not valid_commands:
            out["layout_session_decision"] = "clarify"
            out["assistant_text"] = (
                "I understood the edit request, but I need a supported command form "
                "such as move/swap/flip/delete/abut/add dummy with context."
            )
        else:
            out["session_commands"] = valid_commands
            out["pending_cmds"] = valid_commands

    out["layout_session_needs_synthesis"] = out["layout_session_decision"] in {
        "call_specialist",
        "check_drc",
        "fix_drc",
        "check_routing",
        "optimize_routing",
    }
    return out


def call_layout_session_llm(prompt: str, state: dict) -> str:
    """Invoke configured LLM using repository factory."""
    from ai_agent.llm.factory import get_langchain_llm

    model_name = str(state.get("selected_model") or "Gemini")
    llm = get_langchain_llm(model_name, task_weight="light")
    response = llm.invoke([
        {"role": "system", "content": _LAYOUT_SESSION_PROMPT_TEMPLATE},
        {"role": "user", "content": prompt},
    ])
    if response and hasattr(response, "content"):
        return str(response.content or "").strip()
    return ""


def convert_session_chat_result_to_layout_session_result(result: dict) -> dict:
    """Map deterministic session_chat output into layout_session shape."""
    route = result.get("session_route")

    decision = "clarify"
    specialist = None
    if route == "answer_only":
        decision = "answer"
    elif route == "clarify":
        decision = "clarify"
    elif route == "command_edit":
        decision = "propose_commands"
    elif route == "need_drc":
        decision = "check_drc"
    elif route == "fix_drc":
        decision = "fix_drc"
    elif route == "need_routing":
        decision = "check_routing"
    elif route == "fix_routing":
        decision = "optimize_routing"
    elif route == "need_strategy":
        decision = "call_specialist"
        specialist = "strategy_selector"
    elif route == "need_topology":
        decision = "call_specialist"
        specialist = "topology_analyst"
    elif route == "need_placement":
        decision = "call_specialist"
        specialist = "placement_specialist"

    out = _base_result()
    out["layout_session_decision"] = decision
    out["layout_session_confidence"] = _safe_confidence(result.get("route_confidence", 0.0))
    out["layout_session_reason"] = str(result.get("session_reason") or "deterministic fallback")
    out["assistant_text"] = str(result.get("assistant_text") or "")

    if decision == "call_specialist":
        out["layout_session_specialist"] = specialist
        out["layout_session_specialist_question"] = str(result.get("specialist_question") or "")
        out["assistant_text"] = ""

    commands = result.get("session_commands") or result.get("pending_cmds") or []
    if decision == "propose_commands" and isinstance(commands, list):
        sanitized = [cmd for cmd in commands if isinstance(cmd, dict) and cmd.get("action")]
        out["session_commands"] = sanitized
        out["pending_cmds"] = sanitized

    if isinstance(result.get("target_nets"), list):
        out["layout_session_target_nets"] = [str(x) for x in result.get("target_nets")]

    if "pending_edit_intent" in result:
        out["pending_edit_intent"] = result.get("pending_edit_intent")

    out["layout_session_needs_synthesis"] = decision in {
        "call_specialist", "check_drc", "fix_drc", "check_routing", "optimize_routing"
    }
    return out


def _build_prompt_from_state(state: dict) -> str:
    user_message = str(state.get("user_message") or "").strip()
    chat_history = state.get("chat_history") or []
    pending_intent = state.get("pending_edit_intent")

    history_lines: list[str] = []
    for turn in (chat_history if isinstance(chat_history, list) else [])[-6:]:
        if isinstance(turn, dict):
            role = str(turn.get("role") or "user")
            content = str(turn.get("content") or "")
            if content:
                history_lines.append(f"{role}: {content}")

    return (
        f"[CHAT HISTORY]\n{chr(10).join(history_lines) if history_lines else '(empty)'}\n\n"
        f"[LAYOUT SUMMARY]\n{build_layout_summary(state)}\n\n"
        f"[INITIAL TRACE SUMMARY]\n{build_initial_trace_summary(state.get('initial_agent_trace') or {})}\n\n"
        f"[PENDING EDIT INTENT]\n{pending_intent if isinstance(pending_intent, dict) else '(none)'}\n\n"
        f"[SUPPORTED COMMANDS]\n{build_supported_command_summary()}\n\n"
        f"[DETERMINISTIC TOOLS]\n{', '.join(sorted(VALID_DETERMINISTIC_TOOLS))}\n\n"
        f"[SPECIALISTS]\n{', '.join(sorted(VALID_SPECIALISTS))}\n\n"
        f"[USER MESSAGE]\n{user_message}"
    )


def _heuristic_fast_path(state: dict) -> Optional[dict]:
    """Deterministic guardrails before calling the LLM."""
    msg = str(state.get("user_message") or "").strip().lower()
    if not msg:
        out = _base_result()
        out["assistant_text"] = "Please enter a message."
        return out

    pending = state.get("pending_edit_intent")
    if isinstance(pending, dict) and pending.get("missing"):
        out = _base_result()
        out["layout_session_decision"] = "call_deterministic_tool"
        out["layout_session_confidence"] = 0.95
        out["layout_session_reason"] = "pending edit intent exists"
        out["layout_session_tool_name"] = "try_fill_edit_slots"
        out["layout_session_tool_args"] = {"message": state.get("user_message", "")}
        return out

    return None


def _build_clarify_response(reason: str = "I need more detail to proceed safely.") -> dict:
    """Backward-compatible helper retained for tests."""
    out = _base_result()
    out["layout_session_reason"] = reason
    out["assistant_text"] = reason
    return out


def run_layout_session_agent(state: dict) -> dict:
    """AI-first session agent with deterministic fallback."""
    from ai_agent.agents.session_chat_agent import run_session_chat_agent

    fast = _heuristic_fast_path(state)
    if fast is not None:
        return fast

    prompt = _build_prompt_from_state(state)

    try:
        raw = call_layout_session_llm(prompt, state)
        parsed = parse_layout_session_json(raw)
        if not parsed:
            raise ValueError("layout_session_agent returned invalid JSON")

        normalized = normalize_layout_session_result(parsed, state)
        if normalized.get("layout_session_decision") != "clarify" or parsed:
            return normalized
    except Exception:
        pass

    fallback = run_session_chat_agent(state)
    return convert_session_chat_result_to_layout_session_result(fallback)

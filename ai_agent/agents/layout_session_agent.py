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
    "evaluate_matching_edit_intent",
})

VALID_SPECIALISTS: frozenset[str] = frozenset({
    "topology_analyst",
    "strategy_selector",
    "placement_specialist",
    "routing_previewer",
})

CONFIDENCE_THRESHOLD = 0.60


def _is_matching_question_guard(message: str) -> bool:
    """Hard guard for matching questions that must not generate commands."""
    try:
        from ai_agent.agents.session_chat_agent import is_matching_question

        return is_matching_question(message)
    except Exception:
        msg = str(message or "").strip().lower()
        has_term = any(
            term in msg
            for term in (
                "matching", "matched", "match", "common centroid",
                "common-centroid", "interdigitation", "interdigitated",
                "interdig",
            )
        )
        return bool(has_term and ("?" in msg or re.match(r"^(how|what|why|is|are|should)\b", msg)))


def _is_targeted_matching_request(message: str) -> bool:
    """Action-like matching requests are explanatory until a safe planner exists."""
    try:
        from ai_agent.agents.session_chat_agent import is_targeted_matching_request

        return is_targeted_matching_request(message)
    except Exception:
        msg = str(message or "").strip().lower()
        has_term = any(term in msg for term in ("match", "common centroid", "common-centroid", "interdig"))
        return bool(has_term and re.match(r"^(match|make|use|apply|implement)\b", msg))


def _is_direct_trace_answer_request(message: str) -> bool:
    """Questions we can answer from trace/layout context without an LLM."""
    msg = str(message or "").strip().lower()
    if not msg:
        return False
    if _is_matching_question_guard(msg) or _is_targeted_matching_request(msg):
        return True
    return bool(
        "what is this circuit" in msg
        or "what circuit" in msg
        or re.search(r"^what\s+(?:is|does)\s+(?:mm|m|mn|mp|xm)\d+", msg)
        or "what devices are connected to" in msg
        or "which devices are connected to" in msg
    )

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
3a. Use answer_from_initial_trace mainly for placement/strategy/matching/why-placement questions.
3b. For "what is device X doing" or "what devices are connected to net Y", answer from topology/current layout context or call topology_analyst.
3c. Do not return a generic initial-trace dump for device-role/net-connectivity questions.
4. Call exactly one specialist only when deeper topology/strategy/placement/DRC/routing analysis is needed.
5. If the user asks for a small layout edit, either call parse_direct_edit_command or propose supported commands directly.
6. If the user asks to check DRC, choose check_drc.
7. If the user asks to fix DRC, choose fix_drc.
7a. Do not call drc_critic via call_specialist; use decision=fix_drc.
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
  "target_devices": [],
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
        "layout_session_target_devices": [],
        "layout_session_memory_update": {},
        "layout_session_raw_json": {},
        "layout_session_needs_synthesis": False,
    }


def _normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        token = value.strip()
        return [token] if token else []
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        seen: set[str] = set()
        for item in value:
            token = str(item).strip()
            if not token:
                continue
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(token)
        return out
    return []


def _extract_targets_from_message(message: str, state: dict) -> tuple[list[str], list[str]]:
    """Extract routing targets from user text without mutating state."""
    if not message:
        return [], []
    try:
        from ai_agent.agents.session_chat_agent import _extract_target_nets, _extract_devices

        placement_nodes = state.get("placement_nodes") or state.get("nodes") or []
        nets = _normalize_string_list(_extract_target_nets(message))
        devices = _normalize_string_list(_extract_devices(message, placement_nodes))
        return nets, devices
    except Exception:
        return [], []


def normalize_layout_session_result(parsed: dict, state: dict) -> dict:
    """Normalize parsed LLM JSON into graph state fields."""
    out = _base_result()
    parsed = parsed if isinstance(parsed, dict) else {}
    user_message = str(state.get("user_message") or "")

    decision = normalize_layout_session_decision(parsed.get("decision"))
    confidence = _safe_confidence(parsed.get("confidence", 0.0))
    reason = str(parsed.get("reason") or "")
    assistant_text = str(parsed.get("assistant_text") or "")
    tool_args = parsed.get("tool_args") if isinstance(parsed.get("tool_args"), dict) else {}

    out["layout_session_decision"] = decision
    out["layout_session_confidence"] = confidence
    out["layout_session_reason"] = reason
    out["assistant_text"] = assistant_text
    out["layout_session_raw_json"] = parsed

    if _is_matching_question_guard(user_message) or _is_targeted_matching_request(user_message):
        out["layout_session_decision"] = "call_deterministic_tool"
        out["layout_session_confidence"] = max(confidence, 0.95)
        out["layout_session_reason"] = "matching question/request guard"
        out["assistant_text"] = ""
        out["layout_session_tool_name"] = "answer_from_initial_trace"
        out["layout_session_tool_args"] = {"question": user_message}
        out["session_commands"] = []
        out["pending_cmds"] = []
        return out

    memory_update = parsed.get("memory_update")
    if isinstance(memory_update, dict):
        out["layout_session_memory_update"] = memory_update

    parsed_targets = _normalize_string_list(parsed.get("target_nets"))
    memory_targets = _normalize_string_list(memory_update.get("target_nets")) if isinstance(memory_update, dict) else []
    tool_targets = _normalize_string_list(tool_args.get("target_nets"))
    target_nets = parsed_targets or memory_targets or tool_targets
    out["layout_session_target_nets"] = target_nets

    parsed_devices = _normalize_string_list(parsed.get("target_devices"))
    memory_devices = (
        _normalize_string_list(memory_update.get("target_devices"))
        if isinstance(memory_update, dict) else []
    )
    tool_devices = _normalize_string_list(tool_args.get("target_devices"))
    target_devices = parsed_devices or memory_devices or tool_devices
    out["layout_session_target_devices"] = target_devices

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
        specialist = str(parsed.get("specialist") or "")
        if specialist == "drc_critic":
            out["layout_session_decision"] = "fix_drc"
            out["layout_session_specialist"] = None
            out["layout_session_specialist_question"] = None
            out["assistant_text"] = ""
        elif specialist not in VALID_SPECIALISTS:
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

    if out["layout_session_decision"] == "optimize_routing":
        if not out["layout_session_target_nets"] and not out["layout_session_target_devices"]:
            msg_targets, msg_devices = _extract_targets_from_message(
                str(state.get("user_message") or ""),
                state,
            )
            if msg_targets:
                out["layout_session_target_nets"] = msg_targets
            if msg_devices:
                out["layout_session_target_devices"] = msg_devices
        if not out["layout_session_target_nets"] and not out["layout_session_target_devices"]:
            out["layout_session_decision"] = "clarify"
            out["assistant_text"] = (
                "Which nets or devices should I optimize? "
                "For example: reduce parasitics on VOUTP and VOUTN."
            )
            out["pending_edit_intent"] = {
                "type": "optimize_routing",
                "missing": ["target_nets"],
            }

    if out["layout_session_target_nets"]:
        out["target_nets"] = list(out["layout_session_target_nets"])
    if out["layout_session_target_devices"]:
        out["target_devices"] = list(out["layout_session_target_devices"])

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
        out["target_nets"] = [str(x) for x in result.get("target_nets")]
    if isinstance(result.get("target_devices"), list):
        out["layout_session_target_devices"] = [str(x) for x in result.get("target_devices")]
        out["target_devices"] = [str(x) for x in result.get("target_devices")]

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
    route_hint = state.get("_layout_session_route_hint")
    extracted_nets = state.get("_layout_session_extracted_target_nets") or []

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
        f"[DETERMINISTIC ROUTE HINT]\n{route_hint if route_hint else '(none)'}\n\n"
        f"[EXTRACTED TARGET NETS]\n{extracted_nets if extracted_nets else '(none)'}\n\n"
        f"[ROUTE-HINT MAPPING]\n"
        f"- command_edit -> call_deterministic_tool or propose_commands\n"
        f"- need_drc -> check_drc\n"
        f"- fix_drc -> fix_drc\n"
        f"- need_routing -> check_routing\n"
        f"- fix_routing -> optimize_routing\n"
        f"- need_strategy -> call_specialist(strategy_selector)\n"
        f"- need_topology -> call_specialist(topology_analyst)\n\n"
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
        pending_type = str(pending.get("type") or pending.get("action") or "")
        if pending_type == "optimize_routing":
            out = _base_result()
            out["layout_session_decision"] = "call_deterministic_tool"
            out["layout_session_confidence"] = 0.95
            out["layout_session_reason"] = "pending routing optimization intent exists"
            out["layout_session_tool_name"] = "extract_target_nets"
            out["layout_session_tool_args"] = {
                "message": state.get("user_message", ""),
                "next_decision": "optimize_routing",
            }
            return out
        out = _base_result()
        out["layout_session_decision"] = "call_deterministic_tool"
        out["layout_session_confidence"] = 0.95
        out["layout_session_reason"] = "pending edit intent exists"
        out["layout_session_tool_name"] = "try_fill_edit_slots"
        out["layout_session_tool_args"] = {"message": state.get("user_message", "")}
        return out

    if _is_direct_trace_answer_request(str(state.get("user_message") or "")):
        out = _base_result()
        out["layout_session_decision"] = "call_deterministic_tool"
        out["layout_session_confidence"] = 0.97
        out["layout_session_reason"] = "deterministic trace/layout answer guard"
        out["layout_session_tool_name"] = "answer_from_initial_trace"
        out["layout_session_tool_args"] = {"question": state.get("user_message", "")}
        out["session_commands"] = []
        out["pending_cmds"] = []
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
    from ai_agent.agents.session_chat_agent import (
        _extract_target_nets,
        rule_route,
        run_session_chat_agent,
    )

    fast = _heuristic_fast_path(state)
    if fast is not None:
        return fast

    user_message = str(state.get("user_message") or "")
    route_hint = rule_route(user_message) if user_message else None
    extracted_nets = _extract_target_nets(user_message) if user_message else []
    prompt_state = dict(state)
    prompt_state["_layout_session_route_hint"] = route_hint
    prompt_state["_layout_session_extracted_target_nets"] = extracted_nets
    prompt = _build_prompt_from_state(prompt_state)

    try:
        raw = call_layout_session_llm(prompt, state)
        parsed = parse_layout_session_json(raw)
        if not parsed:
            raise ValueError("layout_session_agent returned invalid JSON")

        llm_confidence = _safe_confidence(parsed.get("confidence", 0.0))
        if llm_confidence < CONFIDENCE_THRESHOLD:
            fallback = run_session_chat_agent(state)
            converted = convert_session_chat_result_to_layout_session_result(fallback)
            original_reason = str(parsed.get("reason") or "").strip() or "(none)"
            converted["layout_session_reason"] = (
                "LLM confidence below threshold; used deterministic fallback. "
                f"Original reason: {original_reason}"
            )
            converted["layout_session_raw_json"] = parsed
            return converted

        normalized = normalize_layout_session_result(parsed, state)
        return normalized
    except Exception:
        pass

    fallback = run_session_chat_agent(state)
    return convert_session_chat_result_to_layout_session_result(fallback)

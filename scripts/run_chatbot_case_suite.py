"""Run the backend chatbot case suite without pytest."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_agent.agents.layout_session_agent import run_layout_session_agent
from ai_agent.graph.edges import (
    route_after_command_validator,
    route_after_deterministic_tool_runner,
    route_after_layout_session_agent,
)
from ai_agent.nodes.command_validator import node_command_validator
from ai_agent.nodes.deterministic_tool_runner import node_deterministic_tool_runner
from tests.fixtures.comparator_chat_state import make_comparator_chat_state


def _resp(**kwargs) -> str:
    base = {
        "decision": "clarify",
        "confidence": 0.95,
        "reason": "script stub",
        "assistant_text": "Please clarify.",
        "tool_name": None,
        "tool_args": {},
        "specialist": None,
        "specialist_question": None,
        "commands": [],
        "target_nets": [],
        "target_devices": [],
        "memory_update": {},
    }
    base.update(kwargs)
    return json.dumps(base)


def _stub_layout_session_llm(_prompt: str, state: dict) -> str:
    msg = str(state.get("user_message") or "").strip().lower()

    if msg in {
        "what is this circuit",
        "what is mm10 doing?",
        "what devices are connected to voutp?",
        "how matching techniques is applied in this layout now",
        "mm3 and mm0 common centroid or interdigitation?",
        "match mm9 and mm8 with common centroid",
    }:
        return _resp(
            decision="call_deterministic_tool",
            tool_name="answer_from_initial_trace",
            tool_args={"question": state.get("user_message")},
            assistant_text="",
        )

    if msg in {
        "swap mm6 with mm7",
        "move mm1 to the left",
        "align mm1 with mm2",
        "move left",
    }:
        return _resp(
            decision="call_deterministic_tool",
            tool_name="parse_direct_edit_command",
            tool_args={"message": state.get("user_message")},
            assistant_text="",
        )

    if msg == "target device is mm1":
        return _resp(
            decision="call_deterministic_tool",
            tool_name="try_fill_edit_slots",
            tool_args={"message": state.get("user_message")},
            assistant_text="",
        )

    if msg == "check drc":
        return _resp(decision="check_drc", assistant_text="")

    if msg == "remove drc violation":
        return _resp(decision="fix_drc", assistant_text="")

    if msg == "reduce parasitics":
        return _resp(decision="optimize_routing", assistant_text="")

    if msg == "reduce parasitics on voutp and voutn":
        return _resp(
            decision="optimize_routing",
            assistant_text="",
            memory_update={"target_nets": ["VOUTP", "VOUTN"]},
        )

    return _resp()


def _run_case(user_message: str, **extra_state) -> dict:
    state = make_comparator_chat_state(user_message)
    state.update(extra_state)

    with patch("ai_agent.agents.layout_session_agent.call_layout_session_llm", _stub_layout_session_llm):
        agent_update = run_layout_session_agent(state)

    merged = dict(state)
    merged.update(agent_update)

    route_after_agent = route_after_layout_session_agent(merged)
    route_after_tool = None
    route_after_validator = None

    if route_after_agent == "node_deterministic_tool_runner":
        tool_update = node_deterministic_tool_runner(merged)
        merged.update(tool_update)
        route_after_tool = route_after_deterministic_tool_runner(merged)
        if route_after_tool == "node_command_validator":
            validator_update = node_command_validator(merged)
            merged.update(validator_update)
            route_after_validator = route_after_command_validator(merged)

    return {
        "state": merged,
        "route_after_agent": route_after_agent,
        "route_after_tool": route_after_tool,
        "route_after_validator": route_after_validator,
    }


def _assert(condition: bool, message: str):
    if not condition:
        raise AssertionError(message)


def _sanitize_table_cell(text: str) -> str:
    """Keep table rows single-line and pipe-safe for downstream parsing."""
    s = str(text or "")
    s = s.replace("|", "¦")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\n", "\\n")
    return s


def _assistant_text(case: dict) -> str:
    return str(case.get("state", {}).get("assistant_text") or "").strip()


def _actual_gui_response(case: dict) -> str:
    """Return full response text that appears in the GUI assistant bubble."""
    text = _assistant_text(case)
    if text:
        return _sanitize_table_cell(text)

    # Some routes in this script stop before checker/synthesizer nodes.
    # Keep fallback metadata so Actual is never blank.
    state = case.get("state", {})
    decision = state.get("layout_session_decision")
    route_agent = case.get("route_after_agent")
    route_tool = case.get("route_after_tool")
    route_validator = case.get("route_after_validator")
    fallback = (
        f"decision={decision}, agent={route_agent}, "
        f"tool={route_tool}, validator={route_validator}"
    )
    return _sanitize_table_cell(fallback)


def _run_priority_cases() -> list[dict]:
    rows: list[dict] = []

    def add(case_id: str, user_input: str, expected: str, ok: bool, actual: str):
        rows.append(
            {
                "case": case_id,
                "input": user_input,
                "expected": expected,
                "actual": actual,
                "status": "PASS" if ok else "FAIL",
            }
        )

    c1 = _run_case("what is this circuit")
    t1 = _assistant_text(c1).lower()
    ok1 = ("dynamic" in t1 and "comparator" in t1 and "here is what the initial placement agents decided" not in t1)
    add("1", "what is this circuit", "answer mentions dynamic comparator (no generic trace dump)", ok1, _actual_gui_response(c1))

    c2 = _run_case("what is MM10 doing?")
    t2 = _assistant_text(c2).lower()
    ok2 = ("mm10" in t2 and (("tail" in t2) or ("current-source" in t2) or ("current source" in t2)) and (("clk" in t2) or ("gnd" in t2) or ("net2<3>" in t2)))
    add("2", "what is MM10 doing?", "answer includes MM10 role + CLK/GND/net2<3>", ok2, _actual_gui_response(c2))

    c3 = _run_case("what devices are connected to VOUTP?")
    t3 = _assistant_text(c3)
    ok3 = ("MM5" in t3 and "MM2" in t3 and "MM6" in t3 and "Here is what the initial placement agents decided" not in t3)
    add("3", "what devices are connected to VOUTP?", "answer includes MM5/MM2/MM6 (no generic trace dump)", ok3, _actual_gui_response(c3))

    c4 = _run_case("How matching techniques is applied in this layout now")
    t4 = _assistant_text(c4)
    t4l = t4.lower()
    ok4 = (
        all(pair in t4 for pair in ("MM8/MM9", "MM0/MM3", "MM4/MM5", "MM6/MM7", "MM1/MM2"))
        and "common-centroid" in t4l
        and "finger ordering" in t4l
        and not t4.startswith("Based on your circuit topology, here are the recommended improvement strategies")
    )
    add("4", "How matching techniques is applied in this layout now", "matching pairs + common-centroid/finger-ordering confirmation", ok4, _actual_gui_response(c4))

    c5 = _run_case("MM3 and MM0 common centroid or interdigitation?")
    t5l = _assistant_text(c5).lower()
    ok5 = ("interdig" in t5l and (("common-centroid-style" in t5l) or ("finger ordering" in t5l)) and "delegate" not in t5l and "strategy_selector" not in t5l)
    add("5", "MM3 and MM0 common centroid or interdigitation?", "mentions interdigitation + common-centroid-style/finger-ordering", ok5, _actual_gui_response(c5))

    c6 = _run_case("Match MM9 and MM8 with common centroid")
    t6l = _assistant_text(c6).lower()
    ok6 = ((("mm9/mm8" in t6l) or ("mm8/mm9" in t6l)) and (("common-centroid-style" in t6l) or ("interdig" in t6l)) and "recommended improvement strategies" not in t6l)
    add("6", "Match MM9 and MM8 with common centroid", "mentions differential-pair matching + common-centroid-style/interdigitation", ok6, _actual_gui_response(c6))

    c7 = _run_case("swap MM6 with MM7")
    cmds7 = c7["state"].get("pending_cmds") or []
    ok7 = (
        c7.get("route_after_agent") == "node_deterministic_tool_runner"
        and c7.get("route_after_tool") == "node_command_validator"
        and bool(cmds7)
        and cmds7[0].get("action") == "swap"
        and cmds7[0].get("device_a") == "MM6"
        and cmds7[0].get("device_b") == "MM7"
    )
    add("7", "swap MM6 with MM7", "propose swap command -> validator path", ok7, _actual_gui_response(c7))

    c8 = _run_case("Move MM1 to the left")
    cmds8 = c8["state"].get("pending_cmds") or []
    ok8 = bool(cmds8) and cmds8[0].get("action") == "move" and cmds8[0].get("device_id") == "MM1" and float(cmds8[0].get("dx", 0)) < 0
    add("8", "Move MM1 to the left", "move command for MM1 with negative dx", ok8, _actual_gui_response(c8))

    c9a = _run_case("move left")
    pending = c9a["state"].get("pending_edit_intent")
    c9b = _run_case("Target device is MM1", pending_edit_intent=pending)
    cmds9 = c9b["state"].get("pending_cmds") or []
    ok9 = (
        c9a["state"].get("layout_session_decision") == "clarify"
        and isinstance(pending, dict)
        and c9b.get("route_after_tool") == "node_command_validator"
        and bool(cmds9)
        and cmds9[0].get("action") == "move"
        and cmds9[0].get("device_id") == "MM1"
    )
    add("9", "move left -> Target device is MM1", "first turn clarify+pending_intent, second turn creates move command", ok9, _actual_gui_response(c9b))

    c10 = _run_case("check DRC")
    ok10 = c10["state"].get("layout_session_decision") == "check_drc" and c10.get("route_after_agent") == "node_drc_checker" and not (c10["state"].get("pending_cmds") or [])
    add("10", "check DRC", "check_drc read-only path; no pending_cmds", ok10, _actual_gui_response(c10))

    c11 = _run_case("reduce parasitics")
    t11 = _assistant_text(c11).lower()
    ok11 = c11["state"].get("layout_session_decision") == "clarify" and "which nets or devices should i optimize" in t11
    add("11", "reduce parasitics", "clarify missing target nets/devices", ok11, _actual_gui_response(c11))

    c12 = _run_case("reduce parasitics on VOUTP and VOUTN")
    target_nets = c12["state"].get("layout_session_target_nets") or []
    ok12 = c12["state"].get("layout_session_decision") == "optimize_routing" and c12.get("route_after_agent") == "node_routing_previewer" and "VOUTP" in target_nets and "VOUTN" in target_nets
    add("12", "reduce parasitics on VOUTP and VOUTN", "optimize_routing path with target nets VOUTP,VOUTN", ok12, _actual_gui_response(c12))

    return rows


def _print_table(rows: list[dict]):
    print("Case | Input | Expected | Actual | Pass/Fail")
    print("-" * 180)
    for row in rows:
        case_id = _sanitize_table_cell(row["case"])
        user_input = _sanitize_table_cell(row["input"])
        expected = _sanitize_table_cell(row["expected"])
        actual = _sanitize_table_cell(row["actual"])
        status = _sanitize_table_cell(row["status"])
        print(f"{case_id} | {user_input} | {expected} | {actual} | {status}")


def main():
    print("Running chatbot case suite...")
    rows = _run_priority_cases()
    _print_table(rows)

    total = len(rows)
    passed = sum(1 for r in rows if r["status"] == "PASS")
    print("-" * 60)
    print(f"Summary: {passed}/{total} passed, {total - passed} failed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())

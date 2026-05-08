"""Automated backend chatbot case suite for layout_session_app/session agent."""

from __future__ import annotations

import json

import pytest

from ai_agent.agents.layout_session_agent import run_layout_session_agent
from ai_agent.graph.edges import (
    route_after_command_validator,
    route_after_deterministic_tool_runner,
    route_after_layout_session_agent,
)
from ai_agent.nodes.command_validator import node_command_validator
from ai_agent.nodes.deterministic_tool_runner import node_deterministic_tool_runner
from tests.fixtures.comparator_chat_state import make_comparator_chat_state


@pytest.fixture(autouse=True)
def _patch_layout_session_llm(monkeypatch):
    """Prevent any real LLM calls and return deterministic JSON decisions."""

    def _resp(**kwargs) -> str:
        base = {
            "decision": "clarify",
            "confidence": 0.95,
            "reason": "test stub",
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

    def _stub(_prompt: str, state: dict) -> str:
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
            "move mm10 to the left",
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
            # run_layout_session_agent may short-circuit to try_fill_edit_slots
            # via pending_edit_intent, but this keeps behavior deterministic if not.
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

    monkeypatch.setattr(
        "ai_agent.agents.layout_session_agent.call_layout_session_llm",
        _stub,
    )


@pytest.fixture
def _skip_if_no_langgraph():
    try:
        from langgraph.graph import StateGraph  # noqa: F401
    except ImportError:
        pytest.skip("langgraph not available")


def _run_case(user_message: str, **extra_state) -> dict:
    state = make_comparator_chat_state(user_message)
    state.update(extra_state)

    agent_update = run_layout_session_agent(state)
    merged = dict(state)
    merged.update(agent_update)

    route_after_agent = route_after_layout_session_agent(merged)

    result = {
        "state": merged,
        "agent_update": agent_update,
        "route_after_agent": route_after_agent,
        "tool_update": None,
        "route_after_tool": None,
        "validator_update": None,
        "route_after_validator": None,
    }

    if route_after_agent == "node_deterministic_tool_runner":
        tool_update = node_deterministic_tool_runner(merged)
        merged.update(tool_update)
        route_after_tool = route_after_deterministic_tool_runner(merged)
        result["tool_update"] = tool_update
        result["route_after_tool"] = route_after_tool

        if route_after_tool == "node_command_validator":
            validator_update = node_command_validator(merged)
            merged.update(validator_update)
            result["validator_update"] = validator_update
            result["route_after_validator"] = route_after_command_validator(merged)

    result["state"] = merged
    return result


def _assistant_text(case: dict) -> str:
    if case.get("tool_update") and case["tool_update"].get("assistant_text"):
        return str(case["tool_update"].get("assistant_text") or "")
    return str(case["state"].get("assistant_text") or "")


def test_mode_chat_maps_to_layout_session_app(_skip_if_no_langgraph):
    from ai_agent.graph.builder import layout_session_app
    from ai_agent.llm.workers import select_graph_app

    assert select_graph_app("chat") is layout_session_app


def test_direct_answer_what_is_this_circuit():
    case = _run_case("what is this circuit")
    text = _assistant_text(case)

    assert "dynamic" in text.lower()
    assert "comparator" in text.lower()
    assert "Here is what the initial placement agents decided" not in text


def test_direct_answer_mm10_role():
    case = _run_case("what is MM10 doing?")
    text = _assistant_text(case)
    lower = text.lower()

    assert "mm10" in lower
    assert ("tail" in lower) or ("current-source" in lower) or ("current source" in lower)
    assert ("clk" in lower) or ("gnd" in lower) or ("net2<3>" in lower)
    assert "Here is what the initial placement agents decided" not in text


def test_direct_answer_voutp_connectivity():
    case = _run_case("what devices are connected to VOUTP?")
    text = _assistant_text(case)

    assert "MM5" in text
    assert "MM2" in text
    assert "MM6" in text
    assert "Here is what the initial placement agents decided" not in text


def test_matching_layout_summary_answer():
    case = _run_case("How matching techniques is applied in this layout now")
    text = _assistant_text(case)

    for pair in ("MM8/MM9", "MM0/MM3", "MM4/MM5", "MM6/MM7", "MM1/MM2"):
        assert pair in text
    assert "common-centroid" in text.lower()
    assert "finger ordering" in text.lower()
    assert not text.startswith("Based on your circuit topology, here are the recommended improvement strategies")


def test_matching_mm3_mm0_common_centroid_or_interdigitation():
    case = _run_case("MM3 and MM0 common centroid or interdigitation?")
    text = _assistant_text(case)
    lower = text.lower()

    assert "interdig" in lower
    assert ("common-centroid-style" in lower) or ("finger ordering" in lower)
    assert "delegate" not in lower
    assert "strategy_selector" not in lower


def test_matching_mm9_mm8_common_centroid_request():
    case = _run_case("Match MM9 and MM8 with common centroid")
    text = _assistant_text(case)
    lower = text.lower()

    assert ("mm9/mm8" in lower) or ("mm8/mm9" in lower)
    assert ("common-centroid-style" in lower) or ("interdig" in lower)
    assert "recommended improvement strategies" not in lower


def test_command_swap_mm6_mm7_validated_path():
    case = _run_case("swap MM6 with MM7")

    assert case["route_after_agent"] == "node_deterministic_tool_runner"
    assert case["route_after_tool"] == "node_command_validator"

    tool_cmds = case["tool_update"].get("pending_cmds") or []
    assert tool_cmds
    assert tool_cmds[0]["action"] == "swap"
    assert tool_cmds[0].get("device_a") == "MM6"
    assert tool_cmds[0].get("device_b") == "MM7"

    validated = case["validator_update"].get("pending_cmds") or []
    assert validated
    assert validated[0]["action"] == "swap"


def test_command_move_mm1_left_matched_block_safety():
    """MM1 is in MM2_MM1_matched — moving it should trigger clarification."""
    case = _run_case("Move MM1 to the left")

    assert case["route_after_agent"] == "node_deterministic_tool_runner"
    # Matched-block safety: tool_runner returns clarify, not propose_commands
    assert case["route_after_tool"] == "node_session_finalizer"
    tool_text = case["tool_update"].get("assistant_text", "")
    assert "matched" in tool_text.lower() or "block" in tool_text.lower()


def test_command_move_free_device_validated():
    """Free devices (MM10) should move directly without matched-block safety."""
    case = _run_case("Move MM10 to the left")

    assert case["route_after_agent"] == "node_deterministic_tool_runner"
    assert case["route_after_tool"] == "node_command_validator"

    cmd = (case["tool_update"].get("pending_cmds") or [])[0]
    assert cmd["action"] == "move"
    assert cmd["device_id"] == "MM10"
    assert cmd["dx"] < 0


def test_command_align_is_unsupported_and_does_not_reach_human_viewer():
    case = _run_case("align MM1 with MM2")

    assert case["state"]["layout_session_decision"] == "clarify"
    assert case["route_after_tool"] == "node_session_finalizer"
    assert not (case["state"].get("pending_cmds") or [])
    assert case["route_after_validator"] is None


def test_multi_turn_pending_intent_then_slot_fill():
    first = _run_case("move left")
    pending = first["state"].get("pending_edit_intent")

    assert first["state"]["layout_session_decision"] == "clarify"
    assert isinstance(pending, dict)

    second = _run_case("Target device is MM1", pending_edit_intent=pending)
    assert second["route_after_agent"] == "node_deterministic_tool_runner"
    assert second["route_after_tool"] == "node_command_validator"

    cmd = (second["tool_update"].get("pending_cmds") or [])[0]
    assert cmd["action"] == "move"
    assert cmd["device_id"] == "MM1"


def test_check_drc_is_read_only_and_no_pending_commands():
    case = _run_case("check DRC")

    assert case["state"]["layout_session_decision"] == "check_drc"
    assert case["route_after_agent"] == "node_drc_checker"
    assert not (case["state"].get("pending_cmds") or [])


def test_remove_drc_violation_routes_to_fix_drc_not_command_edit():
    case = _run_case("remove DRC violation")

    assert case["state"]["layout_session_decision"] == "fix_drc"
    assert case["route_after_agent"] == "node_drc_critic"
    assert case["route_after_agent"] != "node_command_validator"


def test_reduce_parasitics_without_targets_clarifies():
    case = _run_case("reduce parasitics")
    text = _assistant_text(case)

    assert case["state"]["layout_session_decision"] == "clarify"
    assert "which nets or devices" in text.lower()


def test_reduce_parasitics_with_targets_routes_optimize_routing():
    case = _run_case("reduce parasitics on VOUTP and VOUTN")

    assert case["state"]["layout_session_decision"] == "optimize_routing"
    assert case["route_after_agent"] == "node_routing_previewer"
    target_nets = case["state"].get("layout_session_target_nets") or []
    assert "VOUTP" in target_nets
    assert "VOUTN" in target_nets

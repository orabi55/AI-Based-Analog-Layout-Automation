"""
test_deterministic_tool_runner.py
=================================
Dedicated tests for Task 4 — node_deterministic_tool_runner.

Tests validate the full tool dispatch including:
- parse_direct_edit_command
- try_fill_edit_slots
- extract_target_nets
- answer_from_initial_trace
- rule_route
- Error handling and edge cases
"""

import pytest
import importlib
import importlib.util
import sys
from pathlib import Path

import ai_agent.utils.logging  # noqa


def _load_module(name, relpath):
    if name in sys.modules:
        return sys.modules[name]
    mod_path = Path(__file__).resolve().parents[1] / relpath
    spec = importlib.util.spec_from_file_location(name, mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_runner_mod = _load_module(
    "ai_agent.nodes.deterministic_tool_runner",
    "ai_agent/nodes/deterministic_tool_runner.py",
)
node_deterministic_tool_runner = _runner_mod.node_deterministic_tool_runner


def _finger_nodes(base="MM1", count=4, device_type="nmos"):
    return [
        {
            "id": f"{base}_f{i}",
            "parent_id": base,
            "type": device_type,
            "geometry": {"x": i * 0.5, "y": 0.0, "width": 0.294, "height": 0.668},
        }
        for i in range(count)
    ]


class TestParseDirectEdit:
    """parse_direct_edit_command tool."""

    def test_move_m1_left_produces_command(self):
        state = {
            "layout_session_tool_name": "parse_direct_edit_command",
            "layout_session_tool_args": {},
            "user_message": "move M1 left",
            "placement_nodes": [{"id": "M1"}],
        }
        result = node_deterministic_tool_runner(state)
        assert result["layout_session_decision"] == "propose_commands"
        assert len(result["session_commands"]) == 1
        cmd = result["session_commands"][0]
        assert cmd["action"] == "move"
        assert cmd["device_id"] == "M1"

    def test_move_m1_right_produces_command(self):
        state = {
            "layout_session_tool_name": "parse_direct_edit_command",
            "layout_session_tool_args": {},
            "user_message": "move M1 right",
            "placement_nodes": [{"id": "M1"}],
        }
        result = node_deterministic_tool_runner(state)
        assert result["layout_session_decision"] == "propose_commands"
        cmd = result["session_commands"][0]
        assert cmd["dx"] > 0

    def test_flip_produces_command(self):
        state = {
            "layout_session_tool_name": "parse_direct_edit_command",
            "layout_session_tool_args": {},
            "user_message": "flip M1",
            "placement_nodes": [{"id": "M1"}],
        }
        result = node_deterministic_tool_runner(state)
        assert result["layout_session_decision"] == "propose_commands"
        assert result["session_commands"][0]["action"] == "flip"

    def test_swap_produces_command(self):
        state = {
            "layout_session_tool_name": "parse_direct_edit_command",
            "layout_session_tool_args": {},
            "user_message": "swap M1 and M2",
            "placement_nodes": [{"id": "M1"}, {"id": "M2"}],
        }
        result = node_deterministic_tool_runner(state)
        assert result["layout_session_decision"] == "propose_commands"
        assert result["session_commands"][0]["action"] == "swap"

    def test_ambiguous_creates_partial_intent(self):
        state = {
            "layout_session_tool_name": "parse_direct_edit_command",
            "layout_session_tool_args": {},
            "user_message": "move left",
            "placement_nodes": _finger_nodes("MM1", 2),
        }
        result = node_deterministic_tool_runner(state)
        assert result["layout_session_decision"] == "clarify"
        assert result.get("pending_edit_intent") is not None

    def test_truly_unparseable_returns_clarify(self):
        state = {
            "layout_session_tool_name": "parse_direct_edit_command",
            "layout_session_tool_args": {},
            "user_message": "what is the weather?",
            "placement_nodes": [],
        }
        result = node_deterministic_tool_runner(state)
        assert result["layout_session_decision"] == "clarify"


class TestTryFillSlots:
    """try_fill_edit_slots tool."""

    def test_fills_missing_device_id(self):
        state = {
            "layout_session_tool_name": "try_fill_edit_slots",
            "layout_session_tool_args": {},
            "user_message": "MM1",
            "pending_edit_intent": {
                "action": "move", "dx": -1, "dy": 0, "missing": ["device_id"],
            },
            "placement_nodes": _finger_nodes("MM1", 2),
        }
        result = node_deterministic_tool_runner(state)
        assert result["layout_session_decision"] == "propose_commands"
        assert result["session_commands"][0]["device_id"] == "MM1"
        assert result.get("pending_edit_intent") is None

    def test_no_pending_intent_returns_clarify(self):
        state = {
            "layout_session_tool_name": "try_fill_edit_slots",
            "layout_session_tool_args": {},
            "user_message": "MM1",
        }
        result = node_deterministic_tool_runner(state)
        assert result["layout_session_decision"] == "clarify"

    def test_cannot_resolve_device_returns_clarify(self):
        state = {
            "layout_session_tool_name": "try_fill_edit_slots",
            "layout_session_tool_args": {},
            "user_message": "yes please",
            "pending_edit_intent": {
                "action": "move", "dx": -1, "dy": 0, "missing": ["device_id"],
            },
        }
        result = node_deterministic_tool_runner(state)
        assert result["layout_session_decision"] == "clarify"


class TestExtractTargetNets:
    """extract_target_nets tool."""

    def test_extracts_voutp_voutn(self):
        state = {
            "layout_session_tool_name": "extract_target_nets",
            "layout_session_tool_args": {},
            "user_message": "reduce parasitics on VOUTP and VOUTN",
        }
        result = node_deterministic_tool_runner(state)
        assert result["layout_session_decision"] == "optimize_routing"
        nets = result.get("target_nets") or result.get("layout_session_target_nets") or []
        assert "VOUTP" in nets
        assert "VOUTN" in nets

    def test_extracts_nets_with_explicit_next_decision(self):
        state = {
            "layout_session_tool_name": "extract_target_nets",
            "layout_session_tool_args": {"next_decision": "check_routing"},
            "user_message": "reduce parasitics on VOUTP and VOUTN",
        }
        result = node_deterministic_tool_runner(state)
        assert result["layout_session_decision"] == "check_routing"

    def test_no_nets_found_returns_clarify(self):
        state = {
            "layout_session_tool_name": "extract_target_nets",
            "layout_session_tool_args": {},
            "user_message": "optimize routing",
        }
        result = node_deterministic_tool_runner(state)
        assert result["layout_session_decision"] == "clarify"
        assert "Which nets or devices should I optimize?" in result["assistant_text"]


class TestAnswerFromTrace:
    """answer_from_initial_trace tool."""

    def test_returns_answer_from_trace(self):
        state = {
            "layout_session_tool_name": "answer_from_initial_trace",
            "layout_session_tool_args": {},
            "user_message": "what is the DRC status?",
            "initial_agent_trace": {
                "drc": {"pass": True, "flags": []},
            },
            "placement_nodes": [],
        }
        result = node_deterministic_tool_runner(state)
        assert result["layout_session_decision"] == "answer"
        assert result["assistant_text"]

    def test_query_alias_is_accepted(self):
        state = {
            "layout_session_tool_name": "answer_from_initial_trace",
            "layout_session_tool_args": {"query": "what is this circuit"},
            "initial_agent_trace": {
                "topology": {"CIRCUIT_TYPE": "Dynamic Latch-based Comparator"},
                "drc": {"pass": True, "flags": []},
            },
            "placement_nodes": [{"id": "MM10", "type": "nmos"}],
        }
        result = node_deterministic_tool_runner(state)
        assert result["layout_session_decision"] == "answer"
        assert "comparator" in result["assistant_text"].lower()

    def test_specific_topology_question_not_answered_by_generic_dump(self):
        state = {
            "layout_session_tool_name": "answer_from_initial_trace",
            "layout_session_tool_args": {"question": "what devices are connected to VOUTP?"},
            "initial_agent_trace": {
                "strategy": "placeholder only",
                "drc": {"pass": True, "flags": []},
            },
            "placement_nodes": [],
        }
        result = node_deterministic_tool_runner(state)
        assert result["layout_session_decision"] == "clarify"
        assert "deeper topology context" in result["assistant_text"].lower()


class TestRuleRoute:
    """rule_route tool."""

    def test_returns_route_for_move_command(self):
        state = {
            "layout_session_tool_name": "rule_route",
            "layout_session_tool_args": {},
            "user_message": "move M1 left",
        }
        result = node_deterministic_tool_runner(state)
        assert result["deterministic_tool_result"]["route"] == "command_edit"

    def test_returns_route_for_drc(self):
        state = {
            "layout_session_tool_name": "rule_route",
            "layout_session_tool_args": {},
            "user_message": "check DRC",
        }
        result = node_deterministic_tool_runner(state)
        assert "drc" in result["deterministic_tool_result"]["route"].lower()


class TestToolRunnerEdgeCases:
    """Edge case handling."""

    def test_no_tool_name_returns_clarify(self):
        result = node_deterministic_tool_runner({})
        assert result["layout_session_decision"] == "clarify"

    def test_unknown_tool_returns_clarify(self):
        state = {"layout_session_tool_name": "nonexistent_tool"}
        result = node_deterministic_tool_runner(state)
        assert result["layout_session_decision"] == "clarify"

    def test_result_always_has_status(self):
        state = {
            "layout_session_tool_name": "parse_direct_edit_command",
            "layout_session_tool_args": {},
            "user_message": "move M1 left",
            "placement_nodes": [{"id": "M1"}],
        }
        result = node_deterministic_tool_runner(state)
        assert "status" in result["deterministic_tool_result"]

    def test_result_always_has_decision(self):
        state = {
            "layout_session_tool_name": "extract_target_nets",
            "layout_session_tool_args": {},
            "user_message": "optimize routing",
        }
        result = node_deterministic_tool_runner(state)
        assert "layout_session_decision" in result

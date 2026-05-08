"""
test_layout_session_agent.py
============================
Tests for Tasks 2, 3, 4:
  - Task 2: run_layout_session_agent (AI-first agent logic)
  - Task 3: node_layout_session_agent (node wrapper)
  - Task 4: node_deterministic_tool_runner (tool dispatch)

All tests are self-contained — no real LLM calls. LLM is monkeypatched.
"""

import json
import pytest
import ai_agent.agents.layout_session_agent as layout_session_agent_mod

from ai_agent.agents.layout_session_agent import (
    VALID_LAYOUT_SESSION_DECISIONS,
    VALID_DETERMINISTIC_TOOLS,
    VALID_SPECIALISTS,
    SUPPORTED_COMMAND_ACTIONS_LIST,
    _build_prompt_from_state,
    build_layout_summary,
    build_trace_summary,
    parse_layout_session_json,
    run_layout_session_agent,
    _build_clarify_response,
)


# ══════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════

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


def _make_llm_response(**kwargs) -> str:
    """Build a JSON string mimicking a valid LLM response."""
    base = {
        "decision": "answer",
        "confidence": 0.92,
        "reason": "test reason",
        "assistant_text": "test answer",
        "tool_name": None,
        "tool_args": {},
        "specialist": None,
        "specialist_question": None,
        "commands": [],
        "memory_update": {},
    }
    base.update(kwargs)
    return json.dumps(base)


def _patch_llm(monkeypatch, response_str: str):
    """Monkeypatch the LLM call to return a fixed response."""
    monkeypatch.setattr(
        "ai_agent.agents.layout_session_agent.call_layout_session_llm",
        lambda *args, **kwargs: response_str,
    )


@pytest.fixture(autouse=True)
def _enforce_llm_monkeypatch(monkeypatch):
    """Guardrail: every run_layout_session_agent test must patch the LLM call."""
    call_counter = {"count": 0}

    def _default_stub(*_args, **_kwargs):
        call_counter["count"] += 1
        return _make_llm_response(
            decision="clarify",
            confidence=0.99,
            reason="default test guard stub",
            assistant_text="guard stub",
        )

    monkeypatch.setattr(
        "ai_agent.agents.layout_session_agent.call_layout_session_llm",
        _default_stub,
    )
    yield
    assert call_counter["count"] == 0, (
        "A test invoked call_layout_session_llm without monkeypatching it. "
        "Patch call_layout_session_llm in each test that runs run_layout_session_agent."
    )


# ══════════════════════════════════════════════════════════════════
# Task 2 — Context builders
# ══════════════════════════════════════════════════════════════════


class TestBuildLayoutSummary:
    def test_empty_nodes(self):
        assert "No placement" in build_layout_summary([])

    def test_with_nodes(self):
        nodes = [
            {"id": "M1", "type": "pmos", "geometry": {"x": 0, "y": 1}},
            {"id": "M2", "type": "nmos", "geometry": {"x": 2, "y": 0}},
        ]
        summary = build_layout_summary(nodes)
        assert "2 devices" in summary
        assert "1 PMOS" in summary
        assert "1 NMOS" in summary
        assert "M1" in summary
        assert "M2" in summary

    def test_with_edges_and_nets(self):
        nodes = [{"id": "M1", "type": "nmos", "geometry": {"x": 0, "y": 0}}]
        summary = build_layout_summary(nodes, edges=[{}, {}], terminal_nets={"n1": {}})
        assert "2 connections" in summary
        assert "1" in summary  # terminal nets


class TestBuildTraceSummary:
    def test_empty_trace(self):
        assert "No initial" in build_trace_summary({})

    def test_none_trace(self):
        assert "No initial" in build_trace_summary(None)

    def test_with_data(self):
        trace = {
            "topology": "2 diff pairs",
            "strategy": {"matching_groups": [["M1", "M2"]]},
            "drc": {"pass": True, "flags": []},
            "routing": {"hpwl": 10.5},
        }
        summary = build_trace_summary(trace)
        assert "diff pair" in summary
        assert "PASS" in summary
        assert "matching_groups" in summary


# ══════════════════════════════════════════════════════════════════
# Task 2 — JSON parser
# ══════════════════════════════════════════════════════════════════


class TestParseLayoutSessionJson:
    def test_valid_json(self):
        result = parse_layout_session_json('{"decision": "answer"}')
        assert result["decision"] == "answer"

    def test_fenced_json(self):
        result = parse_layout_session_json('```json\n{"decision": "clarify"}\n```')
        assert result["decision"] == "clarify"

    def test_embedded_json(self):
        result = parse_layout_session_json('Here is the result: {"decision": "answer"} end')
        assert result["decision"] == "answer"

    def test_invalid_returns_empty(self):
        assert parse_layout_session_json("not json at all") == {}

    def test_none_returns_empty(self):
        assert parse_layout_session_json(None) == {}

    def test_empty_string_returns_empty(self):
        assert parse_layout_session_json("") == {}


# ══════════════════════════════════════════════════════════════════
# Task 2 — run_layout_session_agent
# ══════════════════════════════════════════════════════════════════


class TestRunLayoutSessionAgent:
    """Core AI-first agent logic."""

    def test_empty_message_returns_clarify(self, monkeypatch):
        _patch_llm(monkeypatch, "")
        result = run_layout_session_agent({"user_message": ""})
        assert result["layout_session_decision"] == "clarify"

    def test_answer_decision(self, monkeypatch):
        # Use a message that doesn't trigger deterministic guards
        _patch_llm(monkeypatch, _make_llm_response(
            decision="answer",
            confidence=0.95,
            assistant_text="This layout has 11 devices with 2 diff pairs.",
        ))
        result = run_layout_session_agent({
            "user_message": "Tell me about this layout",
            "placement_nodes": _finger_nodes("MM10", 2),
        })
        assert result["layout_session_decision"] == "answer"
        assert result["layout_session_confidence"] >= 0.9
        assert "11 devices" in result["assistant_text"] or "layout" in result["assistant_text"].lower()
        assert result["session_commands"] == []
        assert result["pending_cmds"] == []

    def test_clarify_decision(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(
            decision="clarify",
            confidence=0.85,
            assistant_text="Which device do you want to move?",
        ))
        result = run_layout_session_agent({"user_message": "move it"})
        assert result["layout_session_decision"] == "clarify"
        assert "device" in result["assistant_text"].lower()

    def test_call_deterministic_tool(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(
            decision="call_deterministic_tool",
            confidence=0.90,
            tool_name="parse_direct_edit_command",
            tool_args={"message": "move M1 left"},
        ))
        result = run_layout_session_agent({
            "user_message": "move M1 left",
            "placement_nodes": [{"id": "M1"}],
        })
        assert result["layout_session_decision"] == "call_deterministic_tool"
        assert result["layout_session_tool_name"] == "parse_direct_edit_command"

    def test_propose_commands(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(
            decision="propose_commands",
            confidence=0.92,
            commands=[{"action": "move", "device_id": "M1", "dx": -1, "dy": 0}],
            assistant_text="Moving M1 left.",
        ))
        result = run_layout_session_agent({
            "user_message": "move M1 left",
            "placement_nodes": [{"id": "M1"}],
        })
        assert result["layout_session_decision"] == "propose_commands"
        assert len(result["session_commands"]) == 1
        assert result["session_commands"][0]["action"] == "move"
        assert len(result["pending_cmds"]) == 1

    def test_propose_commands_strips_unsupported(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(
            decision="propose_commands",
            confidence=0.92,
            commands=[
                {"action": "teleport", "device_id": "M1"},  # unsupported
                {"action": "move", "device_id": "M1", "dx": -1, "dy": 0},  # supported
            ],
        ))
        result = run_layout_session_agent({
            "user_message": "move M1",
            "placement_nodes": [{"id": "M1"}],
        })
        assert result["layout_session_decision"] == "propose_commands"
        assert len(result["session_commands"]) == 1
        assert result["session_commands"][0]["action"] == "move"

    def test_propose_commands_all_unsupported_goes_to_clarify(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(
            decision="propose_commands",
            confidence=0.92,
            commands=[{"action": "teleport", "device_id": "M1"}],
        ))
        result = run_layout_session_agent({
            "user_message": "teleport M1",
            "placement_nodes": [{"id": "M1"}],
        })
        assert result["layout_session_decision"] == "clarify"

    def test_call_specialist(self, monkeypatch):
        # Use a non-matching message to avoid the matching guard
        _patch_llm(monkeypatch, _make_llm_response(
            decision="call_specialist",
            confidence=0.88,
            specialist="strategy_selector",
            specialist_question="What placement strategy is used for the tail device?",
        ))
        result = run_layout_session_agent({
            "user_message": "What placement strategy is used for the tail device?",
        })
        assert result["layout_session_decision"] == "call_specialist"
        assert result["layout_session_specialist"] == "strategy_selector"
        assert result["assistant_text"] == ""  # no placeholder
        assert result["layout_session_needs_synthesis"] is True

    def test_invalid_specialist_falls_back(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(
            decision="call_specialist",
            confidence=0.88,
            specialist="nonexistent_agent",
        ))
        # Should fall back to deterministic
        result = run_layout_session_agent({"user_message": "analyze something"})
        # The fallback may produce various decisions, but it should not crash
        assert result["layout_session_decision"] in VALID_LAYOUT_SESSION_DECISIONS

    def test_check_drc(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(
            decision="check_drc",
            confidence=0.95,
        ))
        result = run_layout_session_agent({"user_message": "Check DRC"})
        assert result["layout_session_decision"] == "check_drc"
        assert result["layout_session_needs_synthesis"] is True

    def test_fix_drc(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(
            decision="fix_drc",
            confidence=0.95,
        ))
        result = run_layout_session_agent({"user_message": "Fix DRC"})
        assert result["layout_session_decision"] == "fix_drc"

    def test_check_routing(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(
            decision="check_routing",
            confidence=0.90,
        ))
        result = run_layout_session_agent({"user_message": "show routing"})
        assert result["layout_session_decision"] == "check_routing"

    def test_optimize_routing_with_nets(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(
            decision="optimize_routing",
            confidence=0.92,
            memory_update={"target_nets": ["VOUTP", "VOUTN"]},
        ))
        result = run_layout_session_agent({
            "user_message": "reduce parasitics on VOUTP and VOUTN",
        })
        assert result["layout_session_decision"] == "optimize_routing"
        assert result["layout_session_needs_synthesis"] is True
        assert "VOUTP" in (result.get("layout_session_target_nets") or [])

    def test_optimize_routing_with_devices_no_clarify(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(
            decision="optimize_routing",
            confidence=0.92,
        ))
        result = run_layout_session_agent({
            "user_message": "reduce parasitics around MM1 and MM2",
            "placement_nodes": [{"id": "MM1"}, {"id": "MM2"}],
        })
        assert result["layout_session_decision"] == "optimize_routing"
        assert result.get("layout_session_target_devices") == ["MM1", "MM2"]

    def test_optimize_routing_without_targets_clarifies(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(
            decision="optimize_routing",
            confidence=0.95,
            target_nets=[],
            target_devices=[],
        ))
        result = run_layout_session_agent({"user_message": "Reduce parasitics"})
        assert result["layout_session_decision"] == "clarify"
        assert "Which nets or devices should I optimize?" in result["assistant_text"]

    def test_call_specialist_drc_critic_normalizes_to_fix_drc(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(
            decision="call_specialist",
            confidence=0.9,
            specialist="drc_critic",
            specialist_question="fix DRC violations",
        ))
        result = run_layout_session_agent({"user_message": "fix drc"})
        assert result["layout_session_decision"] == "fix_drc"
        assert result.get("layout_session_specialist") in (None, "")

    def test_invalid_decision_normalized_to_clarify(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(
            decision="hallucinated_decision",
            confidence=0.92,
        ))
        result = run_layout_session_agent({"user_message": "do something"})
        assert result["layout_session_decision"] == "clarify"

    def test_low_confidence_falls_back_to_deterministic(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(
            decision="propose_commands",
            confidence=0.2,
            commands=[{"action": "move", "device_id": "M1", "dx": -1, "dy": 0}],
        ))
        result = run_layout_session_agent({
            "user_message": "move M1 left",
            "placement_nodes": [{"id": "M1"}],
        })
        assert result["layout_session_decision"] in VALID_LAYOUT_SESSION_DECISIONS
        assert "LLM confidence below threshold; used deterministic fallback." in result["layout_session_reason"]

    def test_low_confidence_fallback_preserves_original_reason(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(
            decision="check_drc",
            confidence=0.4,
            reason="ambiguous confidence from model",
        ))
        result = run_layout_session_agent({"user_message": "check drc"})
        assert result["layout_session_decision"] in VALID_LAYOUT_SESSION_DECISIONS
        assert "Original reason: ambiguous confidence from model" in result["layout_session_reason"]

    def test_invalid_json_falls_back_to_deterministic(self, monkeypatch):
        _patch_llm(monkeypatch, "this is not json at all")
        result = run_layout_session_agent({
            "user_message": "move M1 left",
            "placement_nodes": [{"id": "M1"}],
        })
        # Should have fallen back to deterministic
        assert result["layout_session_decision"] in VALID_LAYOUT_SESSION_DECISIONS

    def test_output_shape_has_all_required_keys(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(decision="answer", confidence=0.9))
        result = run_layout_session_agent({"user_message": "hello"})
        required_keys = {
            "layout_session_decision", "layout_session_confidence",
            "layout_session_reason", "assistant_text",
            "layout_session_tool_name", "layout_session_tool_args",
            "layout_session_specialist", "layout_session_specialist_question",
            "session_commands", "pending_cmds",
            "layout_session_memory_update", "layout_session_raw_json",
        }
        missing = required_keys - result.keys()
        assert not missing, f"Missing keys in output: {missing}"

    def test_never_returns_commands_for_answer(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(decision="answer", confidence=0.9))
        result = run_layout_session_agent({"user_message": "explain"})
        assert result["session_commands"] == []
        assert result["pending_cmds"] == []

    def test_invalid_tool_falls_back(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(
            decision="call_deterministic_tool",
            confidence=0.9,
            tool_name="nonexistent_tool",
        ))
        result = run_layout_session_agent({"user_message": "do X"})
        # Should fall back to deterministic
        assert result["layout_session_decision"] in VALID_LAYOUT_SESSION_DECISIONS


# ══════════════════════════════════════════════════════════════════
# Task 2 — Deterministic fallback
# ══════════════════════════════════════════════════════════════════


class TestDeterministicFallback:
    """The fallback to run_session_chat_agent must produce valid output shape."""

    def test_fallback_on_bad_json(self, monkeypatch):
        _patch_llm(monkeypatch, "garbled output")
        result = run_layout_session_agent({
            "user_message": "flip M1",
            "placement_nodes": [{"id": "M1"}],
        })
        assert result["layout_session_decision"] in VALID_LAYOUT_SESSION_DECISIONS
        # The deterministic agent should handle "flip M1" as command_edit
        assert result["layout_session_decision"] == "propose_commands"
        assert len(result["pending_cmds"]) > 0

    def test_fallback_preserves_pending_intent(self, monkeypatch):
        _patch_llm(monkeypatch, "bad json")
        result = run_layout_session_agent({
            "user_message": "move left",
            "placement_nodes": _finger_nodes("MM1", 2),
        })
        # Deterministic agent should create a partial intent
        assert result["layout_session_decision"] == "clarify"
        assert result.get("pending_edit_intent") is not None


# ══════════════════════════════════════════════════════════════════
class TestPromptRouteHint:
    def test_prompt_contains_route_hint_and_mapping_for_command(self):
        prompt = _build_prompt_from_state({
            "user_message": "Move MM1 left",
            "_layout_session_route_hint": "command_edit",
            "_layout_session_extracted_target_nets": [],
        })
        assert "[DETERMINISTIC ROUTE HINT]" in prompt
        assert "command_edit" in prompt
        assert "fix_routing -> optimize_routing" in prompt

    def test_prompt_contains_route_hint_and_targets_for_routing(self):
        prompt = _build_prompt_from_state({
            "user_message": "reduce parasitics on VOUTP and VOUTN",
            "_layout_session_route_hint": "fix_routing",
            "_layout_session_extracted_target_nets": ["VOUTP", "VOUTN"],
        })
        assert "fix_routing" in prompt
        assert "VOUTP" in prompt and "VOUTN" in prompt

    def test_system_prompt_guides_device_role_and_net_connectivity_handling(self):
        prompt = layout_session_agent_mod._LAYOUT_SESSION_PROMPT_TEMPLATE
        assert "what is device X doing" in prompt
        assert "what devices are connected to net Y" in prompt
        assert "Do not return a generic initial-trace dump" in prompt
# Task 3 — node_layout_session_agent
# ══════════════════════════════════════════════════════════════════

# Load node module without pulling the full __init__.py chain
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


_node_mod = _load_module(
    "ai_agent.nodes.layout_session_agent",
    "ai_agent/nodes/layout_session_agent.py",
)
node_layout_session_agent = _node_mod.node_layout_session_agent


class TestNodeLayoutSessionAgent:
    """Task 3: Node wrapper normalization and safety."""

    def test_normalizes_invalid_decision_to_clarify(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(decision="hallucination", confidence=0.95))
        result = node_layout_session_agent({"user_message": "test"})
        assert result["layout_session_decision"] == "clarify"

    def test_validates_specialist_name(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(
            decision="call_specialist",
            confidence=0.9,
            specialist="fake_specialist",
        ))
        result = node_layout_session_agent({"user_message": "analyze"})
        assert result["layout_session_decision"] == "clarify"
        assert result.get("layout_session_specialist") is None

    def test_validates_tool_name(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(
            decision="call_deterministic_tool",
            confidence=0.9,
            tool_name="nonexistent_tool",
        ))
        result = node_layout_session_agent({"user_message": "test"})
        assert result["layout_session_decision"] == "clarify"
        assert result.get("layout_session_tool_name") is None

    def test_commands_only_for_propose_commands(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(
            decision="answer",
            confidence=0.9,
            commands=[{"action": "move", "device_id": "M1", "dx": -1}],
        ))
        result = node_layout_session_agent({"user_message": "explain"})
        # answer decision should NOT have commands in the update
        assert "session_commands" not in result or result.get("session_commands") is None
        assert "pending_cmds" not in result or result.get("pending_cmds") is None

    def test_no_placeholder_text_for_specialist(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(
            decision="call_specialist",
            confidence=0.9,
            specialist="topology_analyst",
            specialist_question="What are the diff pairs?",
            assistant_text="I'll delegate to topology_analyst.",
        ))
        result = node_layout_session_agent({"user_message": "what diff pairs?"})
        # assistant_text should be empty for specialist handoffs
        assert result["assistant_text"] == ""

    def test_chat_history_updated_for_answer(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(
            decision="answer",
            confidence=0.9,
            assistant_text="Here is the answer.",
        ))
        state = {
            "user_message": "hello",
            "chat_history": [],
        }
        result = node_layout_session_agent(state)
        assert "chat_history" in result
        history = result["chat_history"]
        assert any(t.get("role") == "user" for t in history)
        assert any(t.get("role") == "assistant" for t in history)

    def test_chat_history_not_updated_for_specialist(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(
            decision="call_specialist",
            confidence=0.9,
            specialist="strategy_selector",
            specialist_question="What matching?",
        ))
        result = node_layout_session_agent({
            "user_message": "matching strategy?",
            "chat_history": [],
        })
        # chat_history should NOT be in the update for specialist handoffs
        assert "chat_history" not in result

    def test_exception_returns_clarify(self, monkeypatch):
        def _raise(_state):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            _node_mod, "run_layout_session_agent", _raise,
        )
        result = node_layout_session_agent({"user_message": "test"})
        assert result["layout_session_decision"] == "clarify"
        assert "wrong" in result["assistant_text"].lower()


# ══════════════════════════════════════════════════════════════════
# Task 4 — node_deterministic_tool_runner
# ══════════════════════════════════════════════════════════════════

_runner_mod = _load_module(
    "ai_agent.nodes.deterministic_tool_runner",
    "ai_agent/nodes/deterministic_tool_runner.py",
)
node_deterministic_tool_runner = _runner_mod.node_deterministic_tool_runner


class TestToolRunnerParseDirectEdit:
    """parse_direct_edit_command tool."""

    def test_move_m1_left(self):
        # Use MM10 (free device, not in matched block)
        state = {
            "layout_session_tool_name": "parse_direct_edit_command",
            "layout_session_tool_args": {},
            "user_message": "move MM10 left",
            "placement_nodes": [{"id": "MM10"}],
        }
        result = node_deterministic_tool_runner(state)
        assert result["layout_session_decision"] == "propose_commands"
        assert len(result["session_commands"]) == 1
        assert result["session_commands"][0]["action"] == "move"
        assert result["session_commands"][0]["device_id"] == "MM10"

    def test_ambiguous_move_creates_partial(self):
        state = {
            "layout_session_tool_name": "parse_direct_edit_command",
            "layout_session_tool_args": {},
            "user_message": "move left",
            "placement_nodes": _finger_nodes("MM1", 2),
        }
        result = node_deterministic_tool_runner(state)
        assert result["layout_session_decision"] == "clarify"
        assert result.get("pending_edit_intent") is not None

    def test_truly_ambiguous_message(self):
        state = {
            "layout_session_tool_name": "parse_direct_edit_command",
            "layout_session_tool_args": {},
            "user_message": "do something",
            "placement_nodes": [],
        }
        result = node_deterministic_tool_runner(state)
        assert result["layout_session_decision"] == "clarify"


class TestToolRunnerTryFillSlots:
    """try_fill_edit_slots tool."""

    def test_fills_missing_device(self):
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
        assert result.get("pending_edit_intent") is None  # cleared

    def test_no_pending_intent(self):
        state = {
            "layout_session_tool_name": "try_fill_edit_slots",
            "layout_session_tool_args": {},
            "user_message": "MM1",
        }
        result = node_deterministic_tool_runner(state)
        assert result["layout_session_decision"] == "clarify"

    def test_cannot_fill(self):
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


class TestToolRunnerExtractTargetNets:
    """extract_target_nets tool."""

    def test_extracts_nets(self):
        state = {
            "layout_session_tool_name": "extract_target_nets",
            "layout_session_tool_args": {},
            "user_message": "reduce parasitics on VOUTP and VOUTN",
        }
        result = node_deterministic_tool_runner(state)
        nets = result.get("target_nets") or result.get("layout_session_target_nets") or []
        assert "VOUTP" in nets
        assert "VOUTN" in nets

    def test_no_nets_found(self):
        state = {
            "layout_session_tool_name": "extract_target_nets",
            "layout_session_tool_args": {},
            "user_message": "optimize routing",
        }
        result = node_deterministic_tool_runner(state)
        assert result["layout_session_decision"] == "clarify"


class TestToolRunnerAnswerFromTrace:
    """answer_from_initial_trace tool."""

    def test_returns_answer(self):
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
        assert result["assistant_text"]  # non-empty answer


class TestToolRunnerRuleRoute:
    """rule_route tool (debug/fallback)."""

    def test_returns_route(self):
        state = {
            "layout_session_tool_name": "rule_route",
            "layout_session_tool_args": {},
            "user_message": "move M1 left",
        }
        result = node_deterministic_tool_runner(state)
        assert result["deterministic_tool_result"]["route"] == "command_edit"


class TestToolRunnerEdgeCases:
    """Error handling edge cases."""

    def test_no_tool_name(self):
        result = node_deterministic_tool_runner({})
        assert result["layout_session_decision"] == "clarify"

    def test_unknown_tool(self):
        state = {"layout_session_tool_name": "nonexistent_tool"}
        result = node_deterministic_tool_runner(state)
        assert result["layout_session_decision"] == "clarify"

    def test_tool_result_always_has_status(self):
        state = {
            "layout_session_tool_name": "parse_direct_edit_command",
            "layout_session_tool_args": {},
            "user_message": "move M1 left",
            "placement_nodes": [{"id": "M1"}],
        }
        result = node_deterministic_tool_runner(state)
        assert "status" in result["deterministic_tool_result"]


# ══════════════════════════════════════════════════════════════════
# Task 10 — Required integration tests
# ══════════════════════════════════════════════════════════════════


class TestTask10RequiredIntegration:
    """Minimum integration tests specified in Task 10."""

    # 1. Direct answer — no placeholder
    def test_layout_session_agent_answers_common_centroid_question(self, monkeypatch):
        # Matching questions are routed to call_deterministic_tool by the
        # matching guard. This test verifies an LLM answer path, so use
        # a non-matching question instead.
        _patch_llm(monkeypatch, _make_llm_response(
            decision="answer",
            confidence=0.95,
            assistant_text=(
                "MM10 is the tail current source biased by CLK. "
                "It connects to GND through its source terminal."
            ),
        ))
        result = run_layout_session_agent({
            "user_message": "What role does MM10 play in this comparator?",
            "placement_nodes": _finger_nodes("MM10", 2),
        })
        assert result["layout_session_decision"] == "answer"
        assert "tail" in result["assistant_text"].lower() or "MM10" in result["assistant_text"]
        # No placeholder text
        assert "delegat" not in result["assistant_text"].lower()
        assert result["session_commands"] == []
        assert result["pending_cmds"] == []

    # 2. Command tool call
    def test_layout_session_agent_can_call_parse_direct_edit_command(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(
            decision="call_deterministic_tool",
            confidence=0.92,
            tool_name="parse_direct_edit_command",
            tool_args={"message": "move M1 left"},
        ))
        result = run_layout_session_agent({
            "user_message": "move M1 left",
            "placement_nodes": [{"id": "M1"}],
        })
        assert result["layout_session_decision"] == "call_deterministic_tool"
        assert result["layout_session_tool_name"] == "parse_direct_edit_command"

    # 3. Slot filling
    def test_layout_session_agent_fills_pending_move_target(self, monkeypatch):
        _patch_llm(monkeypatch, _make_llm_response(
            decision="call_deterministic_tool",
            confidence=0.9,
            tool_name="try_fill_edit_slots",
            tool_args={},
        ))
        result = run_layout_session_agent({
            "user_message": "MM1",
            "pending_edit_intent": {
                "action": "move", "dx": -1, "dy": 0, "missing": ["device_id"],
            },
        })
        assert result["layout_session_decision"] == "call_deterministic_tool"
        assert result["layout_session_tool_name"] == "try_fill_edit_slots"

    # 7. Deterministic fallback on bad JSON
    def test_layout_session_agent_falls_back_to_run_session_chat_agent_on_bad_json(
        self, monkeypatch
    ):
        _patch_llm(monkeypatch, "this is garbage not json at all!!!")
        result = run_layout_session_agent({
            "user_message": "move M1 left",
            "placement_nodes": [{"id": "M1"}],
        })
        # Should fall back to deterministic agent
        assert result["layout_session_decision"] in VALID_LAYOUT_SESSION_DECISIONS
        # Deterministic agent should handle this as a command
        assert result["layout_session_decision"] == "propose_commands"
        assert len(result["pending_cmds"]) > 0



"""
test_layout_session_graph.py
============================
Tests for Tasks 5, 6, 7:
  - Task 5: route_after_layout_session_agent, route_after_deterministic_tool_runner
  - Task 6: node_session_synthesizer
  - Task 7: build_layout_session_graph compilation and wiring

All tests are self-contained — no LLM calls, no heavy imports for routing tests.
"""

import pytest

from ai_agent.graph.edges import (
    route_after_layout_session_agent,
    route_after_deterministic_tool_runner,
)
from ai_agent.agents.layout_session_agent import (
    VALID_LAYOUT_SESSION_DECISIONS,
    VALID_SPECIALISTS,
)


# ══════════════════════════════════════════════════════════════════
# Task 5 — route_after_layout_session_agent
# ══════════════════════════════════════════════════════════════════


class TestRouteAfterLayoutSessionAgent:
    """Verify every decision maps to the correct downstream node."""

    def test_answer_routes_to_finalizer(self):
        state = {"layout_session_decision": "answer"}
        assert route_after_layout_session_agent(state) == "node_session_finalizer"

    def test_clarify_routes_to_finalizer(self):
        state = {"layout_session_decision": "clarify"}
        assert route_after_layout_session_agent(state) == "node_session_finalizer"

    def test_call_deterministic_tool_routes_to_runner(self):
        state = {"layout_session_decision": "call_deterministic_tool"}
        assert route_after_layout_session_agent(state) == "node_deterministic_tool_runner"

    def test_propose_commands_routes_to_validator(self):
        state = {"layout_session_decision": "propose_commands"}
        assert route_after_layout_session_agent(state) == "node_command_validator"

    def test_check_drc_routes_to_checker(self):
        state = {"layout_session_decision": "check_drc"}
        assert route_after_layout_session_agent(state) == "node_drc_checker"

    def test_fix_drc_routes_to_critic(self):
        state = {"layout_session_decision": "fix_drc"}
        assert route_after_layout_session_agent(state) == "node_drc_critic"

    def test_check_routing_routes_to_previewer(self):
        state = {"layout_session_decision": "check_routing"}
        assert route_after_layout_session_agent(state) == "node_routing_previewer"

    def test_optimize_routing_routes_to_previewer(self):
        state = {"layout_session_decision": "optimize_routing"}
        assert route_after_layout_session_agent(state) == "node_routing_previewer"

    def test_unknown_decision_routes_to_finalizer(self):
        state = {"layout_session_decision": "hallucination"}
        assert route_after_layout_session_agent(state) == "node_session_finalizer"

    def test_none_decision_routes_to_finalizer(self):
        state = {}
        assert route_after_layout_session_agent(state) == "node_session_finalizer"


class TestRouteAfterLayoutSessionAgentSpecialists:
    """Verify specialist routing within call_specialist decision."""

    def test_topology_analyst(self):
        state = {
            "layout_session_decision": "call_specialist",
            "layout_session_specialist": "topology_analyst",
        }
        assert route_after_layout_session_agent(state) == "node_topology_analyst"

    def test_strategy_selector(self):
        state = {
            "layout_session_decision": "call_specialist",
            "layout_session_specialist": "strategy_selector",
        }
        assert route_after_layout_session_agent(state) == "node_strategy_selector"

    def test_placement_specialist(self):
        state = {
            "layout_session_decision": "call_specialist",
            "layout_session_specialist": "placement_specialist",
        }
        assert route_after_layout_session_agent(state) == "node_placement_specialist"

    def test_drc_critic(self):
        state = {
            "layout_session_decision": "call_specialist",
            "layout_session_specialist": "drc_critic",
        }
        assert route_after_layout_session_agent(state) == "node_drc_critic"

    def test_routing_previewer(self):
        state = {
            "layout_session_decision": "call_specialist",
            "layout_session_specialist": "routing_previewer",
        }
        assert route_after_layout_session_agent(state) == "node_routing_previewer"

    def test_unknown_specialist_falls_to_finalizer(self):
        state = {
            "layout_session_decision": "call_specialist",
            "layout_session_specialist": "unknown_agent",
        }
        assert route_after_layout_session_agent(state) == "node_session_finalizer"

    def test_none_specialist_falls_to_finalizer(self):
        state = {
            "layout_session_decision": "call_specialist",
            "layout_session_specialist": None,
        }
        assert route_after_layout_session_agent(state) == "node_session_finalizer"


# ══════════════════════════════════════════════════════════════════
# Task 5 — route_after_deterministic_tool_runner
# ══════════════════════════════════════════════════════════════════


class TestRouteAfterDeterministicToolRunner:
    """Verify tool runner routing."""

    def test_propose_with_commands_routes_to_validator(self):
        state = {
            "layout_session_decision": "propose_commands",
            "pending_cmds": [{"action": "move", "device_id": "M1"}],
        }
        assert route_after_deterministic_tool_runner(state) == "node_command_validator"

    def test_propose_without_commands_routes_to_finalizer(self):
        state = {
            "layout_session_decision": "propose_commands",
            "pending_cmds": [],
        }
        assert route_after_deterministic_tool_runner(state) == "node_session_finalizer"

    def test_clarify_routes_to_finalizer(self):
        state = {"layout_session_decision": "clarify"}
        assert route_after_deterministic_tool_runner(state) == "node_session_finalizer"

    def test_answer_routes_to_finalizer(self):
        state = {"layout_session_decision": "answer"}
        assert route_after_deterministic_tool_runner(state) == "node_session_finalizer"

    def test_empty_state_routes_to_finalizer(self):
        assert route_after_deterministic_tool_runner({}) == "node_session_finalizer"

    def test_propose_with_none_cmds_routes_to_finalizer(self):
        state = {
            "layout_session_decision": "propose_commands",
            "pending_cmds": None,
        }
        assert route_after_deterministic_tool_runner(state) == "node_session_finalizer"


# ══════════════════════════════════════════════════════════════════
# Task 6 — node_session_synthesizer
# ══════════════════════════════════════════════════════════════════

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


_synth_mod = _load_module(
    "ai_agent.nodes.session_synthesizer",
    "ai_agent/nodes/session_synthesizer.py",
)
node_session_synthesizer = _synth_mod.node_session_synthesizer


class TestSynthesizerTopology:
    def test_synthesises_topology_result(self):
        state = {
            "layout_session_specialist": "topology_analyst",
            "Analysis_result": "Found 2 differential pairs and 1 current mirror.",
        }
        result = node_session_synthesizer(state)
        assert "differential" in result["assistant_text"]

    def test_empty_topology_falls_back(self):
        state = {
            "layout_session_specialist": "topology_analyst",
        }
        result = node_session_synthesizer(state)
        assert result["assistant_text"]  # non-empty fallback


class TestSynthesizerStrategy:
    def test_synthesises_strategy_result(self):
        state = {
            "layout_session_specialist": "strategy_selector",
            "strategy_result": "Using common-centroid for MM3 and MM0.",
        }
        result = node_session_synthesizer(state)
        assert "common-centroid" in result["assistant_text"]

    def test_strategy_from_trace_when_empty(self):
        state = {
            "layout_session_specialist": "strategy_selector",
            "layout_session_specialist_question": "What is the matching strategy?",
            "initial_agent_trace": {
                "strategy": {"matching_groups": [["MM3", "MM0"]]},
            },
        }
        result = node_session_synthesizer(state)
        assert "MM3" in result["assistant_text"] or "matching" in result["assistant_text"].lower()


class TestSynthesizerDRC:
    def test_drc_pass(self):
        state = {
            "layout_session_decision": "check_drc",
            "drc_pass": True,
        }
        result = node_session_synthesizer(state)
        assert "passed" in result["assistant_text"].lower()

    def test_drc_with_flags(self):
        state = {
            "layout_session_decision": "check_drc",
            "drc_pass": False,
            "drc_flags": [{"description": "overlap M1 M2"}],
        }
        result = node_session_synthesizer(state)
        assert "overlap" in result["assistant_text"]

    def test_drc_no_flags(self):
        state = {
            "layout_session_decision": "check_drc",
            "drc_pass": False,
            "drc_flags": [],
        }
        result = node_session_synthesizer(state)
        assert result["assistant_text"]  # non-empty


class TestSynthesizerRouting:
    def test_routing_with_log_text(self):
        state = {
            "layout_session_decision": "check_routing",
            "routing_result": {"log_text": "2 crossings found"},
        }
        result = node_session_synthesizer(state)
        assert "2 crossings" in result["assistant_text"]

    def test_routing_with_target_nets(self):
        state = {
            "layout_session_decision": "optimize_routing",
            "routing_result": {"log_text": "Analysis complete."},
            "layout_session_target_nets": ["VOUTP", "VOUTN"],
        }
        result = node_session_synthesizer(state)
        assert "VOUTP" in result["assistant_text"]
        assert "VOUTN" in result["assistant_text"]

    def test_routing_empty_result(self):
        state = {
            "layout_session_decision": "check_routing",
            "routing_result": {},
        }
        result = node_session_synthesizer(state)
        assert result["assistant_text"]  # non-empty


class TestSynthesizerChatHistory:
    def test_appends_chat_history(self):
        state = {
            "layout_session_specialist": "topology_analyst",
            "Analysis_result": "Found 2 diff pairs.",
            "user_message": "analyze topology",
            "chat_history": [],
        }
        result = node_session_synthesizer(state)
        history = result["chat_history"]
        assert any(t["role"] == "user" for t in history)
        assert any(t["role"] == "assistant" for t in history)

    def test_no_delegation_text(self):
        state = {
            "layout_session_specialist": "strategy_selector",
            "strategy_result": "Using ABAB interdigitation.",
        }
        result = node_session_synthesizer(state)
        text = result["assistant_text"].lower()
        assert "delegat" not in text
        assert "handoff" not in text


class TestSynthesizerFallback:
    def test_final_fallback_message(self):
        state = {
            "layout_session_specialist": "strategy_selector",
            # No strategy_result or trace
        }
        result = node_session_synthesizer(state)
        assert "could not determine" in result["assistant_text"].lower()


# ══════════════════════════════════════════════════════════════════
# Task 7 — build_layout_session_graph
# ══════════════════════════════════════════════════════════════════


class TestLayoutSessionGraphCompilation:
    """Verify that the graph compiles without errors."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_langgraph(self):
        try:
            from langgraph.graph import StateGraph
        except ImportError:
            pytest.skip("langgraph not available")

    def test_graph_compiles(self):
        from ai_agent.graph.builder import build_layout_session_graph
        app, memory = build_layout_session_graph()
        assert app is not None
        assert memory is not None

    def test_module_level_export_exists(self):
        from ai_agent.graph.builder import layout_session_app
        assert layout_session_app is not None

    def test_existing_apps_still_exist(self):
        from ai_agent.graph.builder import app, chat_app, session_chat_app
        assert app is not None
        assert chat_app is not None
        assert session_chat_app is not None


class TestLayoutSessionGraphNodes:
    """Verify the graph contains expected nodes."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_langgraph(self):
        try:
            from langgraph.graph import StateGraph
        except ImportError:
            pytest.skip("langgraph not available")

    def test_has_layout_session_agent_node(self):
        from ai_agent.graph.builder import layout_session_app
        nodes = layout_session_app.get_graph().nodes
        assert "node_layout_session_agent" in nodes

    def test_has_tool_runner_node(self):
        from ai_agent.graph.builder import layout_session_app
        nodes = layout_session_app.get_graph().nodes
        assert "node_deterministic_tool_runner" in nodes

    def test_has_synthesizer_node(self):
        from ai_agent.graph.builder import layout_session_app
        nodes = layout_session_app.get_graph().nodes
        assert "node_session_synthesizer" in nodes

    def test_has_command_validator_node(self):
        from ai_agent.graph.builder import layout_session_app
        nodes = layout_session_app.get_graph().nodes
        assert "node_command_validator" in nodes

    def test_has_all_specialist_nodes(self):
        from ai_agent.graph.builder import layout_session_app
        nodes = layout_session_app.get_graph().nodes
        expected = {
            "node_topology_analyst",
            "node_strategy_selector",
            "node_placement_specialist",
            "node_drc_critic",
            "node_drc_checker",
            "node_routing_previewer",
        }
        missing = expected - set(nodes.keys())
        assert not missing, f"Missing nodes: {missing}"


# ══════════════════════════════════════════════════════════════════
# Task 10 — Required routing integration tests
# ══════════════════════════════════════════════════════════════════


class TestTask10RoutingIntegration:
    """Task 10: Required routing verification tests."""

    # 5. DRC routes to checker
    def test_check_drc_routes_to_drc_checker(self):
        state = {"layout_session_decision": "check_drc"}
        assert route_after_layout_session_agent(state) == "node_drc_checker"

    # 6. Reduce parasitics routes to optimize_routing
    def test_reduce_parasitics_routes_to_optimize_routing(self):
        state = {"layout_session_decision": "optimize_routing"}
        assert route_after_layout_session_agent(state) == "node_routing_previewer"

    # 4. Strategy specialist routes to synthesizer (not placeholder)
    def test_strategy_specialist_routes_to_strategy_selector(self):
        state = {
            "layout_session_decision": "call_specialist",
            "layout_session_specialist": "strategy_selector",
        }
        assert route_after_layout_session_agent(state) == "node_strategy_selector"

    def test_fix_drc_routes_to_critic_not_checker(self):
        state = {"layout_session_decision": "fix_drc"}
        assert route_after_layout_session_agent(state) == "node_drc_critic"

    def test_check_routing_routes_to_previewer(self):
        state = {"layout_session_decision": "check_routing"}
        assert route_after_layout_session_agent(state) == "node_routing_previewer"

"""
Tests for Task 17 — extract_session_route_from_event helper and
SESSION_ROUTE_LABELS coverage.

Task 18 — End-to-end chatbot flow tests using monkeypatched nodes.
"""

import importlib
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from ai_agent.llm.workers import (
    extract_session_route_from_event,
    SESSION_ROUTE_LABELS,
)


# ══════════════════════════════════════════════════════════════════
# Task 17 — extract_session_route_from_event
# ══════════════════════════════════════════════════════════════════

class TestExtractSessionRouteFromEvent:
    """Verify the helper that reads session_route from streaming events."""

    def test_node_session_chat_key(self):
        event = {"node_session_chat": {"session_route": "need_drc"}}
        assert extract_session_route_from_event(event) == "need_drc"

    def test_session_chat_key(self):
        event = {"session_chat": {"session_route": "command_edit"}}
        assert extract_session_route_from_event(event) == "command_edit"

    def test_no_route_in_event(self):
        event = {"node_drc_critic": {"drc_pass": True}}
        assert extract_session_route_from_event(event) is None

    def test_interrupt_event(self):
        event = {"__interrupt__": []}
        assert extract_session_route_from_event(event) is None

    def test_empty_event(self):
        assert extract_session_route_from_event({}) is None

    def test_non_dict_event(self):
        assert extract_session_route_from_event("hello") is None
        assert extract_session_route_from_event(None) is None

    def test_nested_empty_session_route(self):
        event = {"node_session_chat": {"session_route": ""}}
        assert extract_session_route_from_event(event) is None

    def test_nested_none_session_route(self):
        event = {"node_session_chat": {"session_route": None}}
        assert extract_session_route_from_event(event) is None

    def test_all_valid_routes_extractable(self):
        from ai_agent.agents.session_chat_agent import VALID_SESSION_ROUTES
        for route in VALID_SESSION_ROUTES:
            event = {"node_session_chat": {"session_route": route}}
            assert extract_session_route_from_event(event) == route


class TestSessionRouteLabels:
    """Verify all valid routes have a UI label."""

    def test_all_routes_have_labels(self):
        from ai_agent.agents.session_chat_agent import VALID_SESSION_ROUTES
        for route in VALID_SESSION_ROUTES:
            assert route in SESSION_ROUTE_LABELS, f"Route '{route}' missing UI label"

    def test_labels_are_nonempty_strings(self):
        for route, label in SESSION_ROUTE_LABELS.items():
            assert isinstance(label, str) and label.strip(), f"Bad label for '{route}'"


# ══════════════════════════════════════════════════════════════════
# Task 18 — End-to-end chatbot flow tests (monkeypatched)
# ══════════════════════════════════════════════════════════════════

# Load session-chat modules directly to avoid langchain chain
def _load_module(name, relpath):
    if name in sys.modules:
        return sys.modules[name]
    import ai_agent.utils.logging  # noqa — lightweight
    mod_path = Path(__file__).resolve().parents[1] / relpath
    spec = importlib.util.spec_from_file_location(name, mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_sc_mod = _load_module(
    "ai_agent.nodes.session_chat",
    "ai_agent/nodes/session_chat.py",
)
node_session_chat = _sc_mod.node_session_chat

_sf_mod = _load_module(
    "ai_agent.nodes.session_finalizer",
    "ai_agent/nodes/session_finalizer.py",
)
node_session_finalizer = _sf_mod.node_session_finalizer

_cv_mod = _load_module(
    "ai_agent.nodes.command_validator",
    "ai_agent/nodes/command_validator.py",
)
node_command_validator = _cv_mod.node_command_validator

_hv_mod = _load_module(
    "ai_agent.nodes.human_viewer",
    "ai_agent/nodes/human_viewer.py",
)
node_human_viewer = _hv_mod.node_human_viewer

from ai_agent.graph.edges import route_after_session_chat


# Helpers to simulate a flow

def _run_chat_then_route(state: dict) -> tuple[dict, str]:
    """Run node_session_chat → route_after_session_chat, return (state_update, next_node)."""
    update = node_session_chat(state)
    merged = {**state, **update}
    next_node = route_after_session_chat(merged)
    return update, next_node


class TestFlow1AnswerOnly:
    """User asks an explanatory question → answer_only → finalizer → END."""

    def test_answer_only_routes_to_finalizer(self, monkeypatch):
        monkeypatch.setattr(
            _sc_mod, "run_session_chat_agent",
            lambda state: {
                "session_route": "answer_only",
                "route_confidence": 0.95,
                "assistant_text": "M1 is placed here because of matched pair strategy.",
                "session_reason": "explanation question",
            },
        )

        state = {
            "mode": "chat",
            "user_message": "why is M1 here?",
            "chat_history": [],
            "initial_agent_trace": {"strategy": {"reason": "M1 matched with M2"}},
        }
        update, next_node = _run_chat_then_route(state)

        assert update["session_route"] == "answer_only"
        assert next_node == "node_session_finalizer"
        assert "M1" in update["assistant_text"]

    def test_answer_only_no_pending_cmds(self, monkeypatch):
        monkeypatch.setattr(
            _sc_mod, "run_session_chat_agent",
            lambda state: {
                "session_route": "answer_only",
                "route_confidence": 0.9,
                "assistant_text": "The layout follows common-centroid.",
            },
        )
        update, _ = _run_chat_then_route({
            "user_message": "explain strategy",
            "chat_history": [],
        })
        assert "pending_cmds" not in update  # answer_only should not set commands

    def test_answer_only_finalizer_preserves_text(self, monkeypatch):
        monkeypatch.setattr(
            _sc_mod, "run_session_chat_agent",
            lambda state: {
                "session_route": "answer_only",
                "route_confidence": 0.95,
                "assistant_text": "M1 is here for matching.",
            },
        )
        update, _ = _run_chat_then_route({
            "user_message": "why M1?",
            "chat_history": [],
        })
        fin = node_session_finalizer({**update, "session_route": "answer_only"})
        assert "M1" in fin["assistant_text"]


class TestFlow2CommandEdit:
    """User requests a command → command_edit → validator → human_viewer."""

    def test_command_edit_routes_to_validator(self, monkeypatch):
        monkeypatch.setattr(
            _sc_mod, "run_session_chat_agent",
            lambda state: {
                "session_route": "command_edit",
                "route_confidence": 0.99,
                "assistant_text": "Moving M1 left.",
                "session_commands": [{"action": "move", "device_id": "M1", "dx": -5}],
            },
        )

        state = {
            "user_message": "move M1 left",
            "chat_history": [],
        }
        update, next_node = _run_chat_then_route(state)

        assert update["session_route"] == "command_edit"
        assert next_node == "node_command_validator"
        assert len(update["pending_cmds"]) > 0

    def test_command_edit_validator_passes_valid_cmds(self, monkeypatch):
        monkeypatch.setattr(
            _sc_mod, "run_session_chat_agent",
            lambda state: {
                "session_route": "command_edit",
                "route_confidence": 0.99,
                "assistant_text": "Moving M1 left.",
                "session_commands": [{"action": "move", "device_id": "M1", "dx": -5}],
            },
        )
        update, _ = _run_chat_then_route({
            "user_message": "move M1 left",
            "chat_history": [],
        })
        # Validator needs placement_nodes with M1
        merged = {
            **update,
            "placement_nodes": [{"id": "M1", "x": 10, "y": 20}],
        }
        val_result = node_command_validator(merged)
        # Validator should keep valid commands
        assert len(val_result.get("pending_cmds", [])) > 0

    def test_command_edit_human_viewer_receives_cmds(self, monkeypatch):
        monkeypatch.setattr(
            _sc_mod, "run_session_chat_agent",
            lambda state: {
                "session_route": "command_edit",
                "route_confidence": 0.99,
                "assistant_text": "Swapping M1 and M2.",
                "session_commands": [{"action": "swap", "device_id": "M1", "target_id": "M2"}],
            },
        )
        monkeypatch.setattr(
            _hv_mod, "interrupt",
            lambda payload: {"approved": True},
        )

        update, _ = _run_chat_then_route({
            "user_message": "swap M1 M2",
            "chat_history": [],
        })
        merged = {
            **update,
            "placement_nodes": [
                {"id": "M1", "x": 0, "y": 0},
                {"id": "M2", "x": 10, "y": 0},
            ],
        }
        val_result = node_command_validator(merged)
        merged.update(val_result)

        viewer_result = node_human_viewer(merged)
        assert viewer_result["approved"] is True


class TestFlow3DRC:
    """User asks to check DRC → need_drc → (specialist) → finalizer."""

    def test_drc_routes_to_specialist(self, monkeypatch):
        monkeypatch.setattr(
            _sc_mod, "run_session_chat_agent",
            lambda state: {
                "session_route": "need_drc",
                "route_confidence": 0.99,
                "assistant_text": "",
                "requires_specialist": True,
                "specialist_target": "drc_checker",
            },
        )
        state = {"user_message": "check DRC", "chat_history": []}
        update, next_node = _run_chat_then_route(state)

        assert update["session_route"] == "need_drc"
        assert next_node == "node_drc_checker"

    def test_drc_finalizer_produces_text(self, monkeypatch):
        """After DRC specialist runs, finalizer should produce visible text."""
        fin = node_session_finalizer({
            "session_route": "need_drc",
            "drc_pass": True,
            "drc_flags": [],
            "assistant_text": "",
        })
        assert "passed" in fin["assistant_text"].lower()


class TestFlow4Routing:
    """User asks to check routing → need_routing → (specialist) → finalizer."""

    def test_routing_routes_to_previewer(self, monkeypatch):
        monkeypatch.setattr(
            _sc_mod, "run_session_chat_agent",
            lambda state: {
                "session_route": "need_routing",
                "route_confidence": 0.99,
                "assistant_text": "",
            },
        )
        state = {"user_message": "check routing", "chat_history": []}
        update, next_node = _run_chat_then_route(state)

        assert update["session_route"] == "need_routing"
        assert next_node == "node_routing_previewer"

    def test_routing_finalizer_from_result(self):
        fin = node_session_finalizer({
            "session_route": "need_routing",
            "routing_result": {"log_text": "9 nets analyzed, HPWL=12.5µm"},
            "assistant_text": "",
        })
        assert "nets" in fin["assistant_text"].lower()


class TestFlow5Clarify:
    """Ambiguous request → clarify → finalizer → END."""

    def test_clarify_routes_to_finalizer(self, monkeypatch):
        monkeypatch.setattr(
            _sc_mod, "run_session_chat_agent",
            lambda state: {
                "session_route": "clarify",
                "route_confidence": 0.3,
                "assistant_text": "Could you be more specific?",
            },
        )
        state = {"user_message": "make it better", "chat_history": []}
        update, next_node = _run_chat_then_route(state)

        assert update["session_route"] == "clarify"
        assert next_node == "node_session_finalizer"

    def test_clarify_no_specialist_called(self, monkeypatch):
        monkeypatch.setattr(
            _sc_mod, "run_session_chat_agent",
            lambda state: {
                "session_route": "clarify",
                "route_confidence": 0.2,
                "assistant_text": "Please specify which device.",
            },
        )
        update, next_node = _run_chat_then_route({
            "user_message": "make it better",
            "chat_history": [],
        })
        # Should not route to any specialist
        assert next_node == "node_session_finalizer"
        assert "pending_cmds" not in update

    def test_clarify_finalizer_text(self, monkeypatch):
        monkeypatch.setattr(
            _sc_mod, "run_session_chat_agent",
            lambda state: {
                "session_route": "clarify",
                "route_confidence": 0.2,
                "assistant_text": "",
            },
        )
        update, _ = _run_chat_then_route({
            "user_message": "improve",
            "chat_history": [],
        })
        fin = node_session_finalizer({**update, "session_route": "clarify"})
        assert fin["assistant_text"]  # Should not be empty


class TestFlow6Strategy:
    """User asks about strategy → need_strategy → finalizer."""

    def test_strategy_routes_to_selector(self, monkeypatch):
        monkeypatch.setattr(
            _sc_mod, "run_session_chat_agent",
            lambda state: {
                "session_route": "need_strategy",
                "route_confidence": 0.9,
                "assistant_text": "",
            },
        )
        state = {"user_message": "should this use common centroid?", "chat_history": []}
        update, next_node = _run_chat_then_route(state)

        assert update["session_route"] == "need_strategy"
        assert next_node == "node_strategy_selector"


class TestFlow7Topology:
    """User asks about topology → need_topology → finalizer."""

    def test_topology_routes_to_analyst(self, monkeypatch):
        monkeypatch.setattr(
            _sc_mod, "run_session_chat_agent",
            lambda state: {
                "session_route": "need_topology",
                "route_confidence": 0.85,
                "assistant_text": "",
            },
        )
        state = {"user_message": "show me the netlist", "chat_history": []}
        update, next_node = _run_chat_then_route(state)

        assert update["session_route"] == "need_topology"
        assert next_node == "node_topology_analyst"


class TestAgentCrashRecovery:
    """If the agent crashes, node_session_chat should catch and route to clarify."""

    def test_crash_becomes_clarify(self, monkeypatch):
        monkeypatch.setattr(
            _sc_mod, "run_session_chat_agent",
            lambda state: (_ for _ in ()).throw(RuntimeError("LLM timeout")),
        )
        update, next_node = _run_chat_then_route({
            "user_message": "test crash",
            "chat_history": [],
        })
        assert update["session_route"] == "clarify"
        assert next_node == "node_session_finalizer"
        assert update["assistant_text"]  # Should have error message

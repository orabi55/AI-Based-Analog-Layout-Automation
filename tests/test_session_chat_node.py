"""
Tests for node_session_chat (Task 5) and route_after_session_chat (Task 6).

All agent calls are monkeypatched so no real API keys are needed.
The node module is loaded via importlib.util to avoid triggering the
ai_agent.nodes.__init__.py import chain (which requires langchain).
"""

import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

# Import the edge function directly (lightweight — no langchain chain)
from ai_agent.graph.edges import route_after_session_chat


# ---------------------------------------------------------------------------
# Load ai_agent.nodes.session_chat without triggering __init__.py
# ---------------------------------------------------------------------------
def _load_session_chat_module():
    """Import session_chat.py directly by file path, skipping __init__.py."""
    mod_name = "ai_agent.nodes.session_chat"
    if mod_name in sys.modules:
        return sys.modules[mod_name]

    # Ensure the parent package exists in sys.modules as a namespace stub
    # so that "from ai_agent.nodes._shared import ..." inside the function
    # body will work at runtime (it's a lazy import, only hit during real
    # execution, not at import time).
    for pkg in ("ai_agent", "ai_agent.nodes"):
        if pkg not in sys.modules:
            sys.modules[pkg] = types.ModuleType(pkg)

    mod_path = (
        Path(__file__).resolve().parents[1]
        / "ai_agent" / "nodes" / "session_chat.py"
    )
    spec = importlib.util.spec_from_file_location(mod_name, mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


_session_chat_mod = _load_session_chat_module()
node_session_chat = _session_chat_mod.node_session_chat


# ──────────────────────────────────────────────────────────────────
# Task 5 — node_session_chat
# ──────────────────────────────────────────────────────────────────

class TestNodeSessionChatCommandEdit:
    """command_edit route must populate both session_commands and pending_cmds."""

    def test_basic_command_edit(self, monkeypatch):
        def fake_agent(state):
            return {
                "session_route": "command_edit",
                "route_confidence": 0.95,
                "assistant_text": "Moving M1 left.",
                "session_commands": [{"action": "move", "device_id": "M1", "dx": -1, "dy": 0}],
                "requires_specialist": False,
                "specialist_target": None,
            }

        monkeypatch.setattr(
            _session_chat_mod, "run_session_chat_agent", fake_agent,
        )

        result = node_session_chat({"user_message": "move M1 left"})
        assert result["session_route"] == "command_edit"
        assert result["pending_cmds"][0]["action"] == "move"
        assert result["session_commands"] == result["pending_cmds"]
        assert result["route_confidence"] == 0.95
        assert result["assistant_text"] == "Moving M1 left."
        assert result["requires_specialist"] is False


class TestNodeSessionChatInvalidRoute:
    """Unknown routes from the agent must be normalised to clarify."""

    def test_bad_route_becomes_clarify(self, monkeypatch):
        def fake_agent(state):
            return {
                "session_route": "bad",
                "route_confidence": 0.1,
                "assistant_text": "Need clarification.",
            }

        monkeypatch.setattr(
            _session_chat_mod, "run_session_chat_agent", fake_agent,
        )

        result = node_session_chat({"user_message": "hello"})
        assert result["session_route"] == "clarify"
        # pending_cmds should NOT be set for non-command routes
        assert "pending_cmds" not in result


class TestNodeSessionChatAnswerOnly:
    """answer_only route should not set pending_cmds."""

    def test_answer_only(self, monkeypatch):
        def fake_agent(state):
            return {
                "session_route": "answer_only",
                "route_confidence": 0.95,
                "assistant_text": "The topology shows a diff pair.",
                "session_reason": "Deterministic keyword match → answer_only",
                "requires_specialist": False,
                "specialist_target": None,
            }

        monkeypatch.setattr(
            _session_chat_mod, "run_session_chat_agent", fake_agent,
        )

        result = node_session_chat({"user_message": "explain the topology"})
        assert result["session_route"] == "answer_only"
        assert "pending_cmds" not in result
        assert result["assistant_text"] == "The topology shows a diff pair."


class TestNodeSessionChatEmptyState:
    """Node must not crash on completely empty state."""

    def test_empty_state(self, monkeypatch):
        def fake_agent(state):
            return {
                "session_route": "clarify",
                "route_confidence": 0.0,
                "assistant_text": "Please enter a message.",
                "session_reason": "Empty input",
            }

        monkeypatch.setattr(
            _session_chat_mod, "run_session_chat_agent", fake_agent,
        )

        result = node_session_chat({})
        assert result["session_route"] == "clarify"
        assert isinstance(result["route_confidence"], float)


class TestNodeSessionChatSpecialist:
    """Specialist flags should be forwarded but no specialist called."""

    def test_specialist_forwarded(self, monkeypatch):
        def fake_agent(state):
            return {
                "session_route": "need_drc",
                "route_confidence": 0.90,
                "assistant_text": "I'll run a DRC check.",
                "session_reason": "DRC keyword detected",
                "requires_specialist": True,
                "specialist_target": "drc_critic",
            }

        monkeypatch.setattr(
            _session_chat_mod, "run_session_chat_agent", fake_agent,
        )

        result = node_session_chat({"user_message": "check DRC"})
        assert result["session_route"] == "need_drc"
        assert result["requires_specialist"] is True
        assert result["specialist_target"] == "drc_critic"
        # No pending_cmds for specialist routes
        assert "pending_cmds" not in result


class TestNodeSessionChatAgentCrash:
    """If run_session_chat_agent raises, node should still return clarify."""

    def test_crash_becomes_clarify(self, monkeypatch):
        def crash_agent(state):
            raise RuntimeError("LLM exploded")

        monkeypatch.setattr(
            _session_chat_mod, "run_session_chat_agent", crash_agent,
        )

        result = node_session_chat({"user_message": "move M1"})
        assert result["session_route"] == "clarify"
        assert result["route_confidence"] == 0.0
        assert "chat_history" in result


class TestNodeSessionChatHistory:
    """Chat history should be updated with the assistant reply."""

    def test_chat_history_appended(self, monkeypatch):
        def fake_agent(state):
            return {
                "session_route": "answer_only",
                "route_confidence": 0.95,
                "assistant_text": "Here is my answer.",
                "session_reason": "explanation match",
                "requires_specialist": False,
                "specialist_target": None,
            }

        monkeypatch.setattr(
            _session_chat_mod, "run_session_chat_agent", fake_agent,
        )

        state = {
            "user_message": "why did you place M1?",
            "chat_history": [],
        }
        result = node_session_chat(state)
        assert "chat_history" in result
        assert len(result["chat_history"]) >= 1


# ──────────────────────────────────────────────────────────────────
# Task 6 — route_after_session_chat
# ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "route, expected",
    [
        ("answer_only",    "node_session_finalizer"),
        ("clarify",        "node_session_finalizer"),
        ("command_edit",   "node_command_validator"),
        ("need_topology",  "node_topology_analyst"),
        ("need_strategy",  "node_strategy_selector"),
        ("need_placement", "node_placement_specialist"),
        ("need_drc",       "node_drc_critic"),
        ("need_routing",   "node_routing_previewer"),
        ("unknown",        "node_session_finalizer"),
        (None,             "node_session_finalizer"),
        ("",               "node_session_finalizer"),
    ],
)
def test_route_after_session_chat(route, expected):
    assert route_after_session_chat({"session_route": route}) == expected


def test_route_after_session_chat_empty_state():
    """Completely empty state must not crash."""
    assert route_after_session_chat({}) == "node_session_finalizer"

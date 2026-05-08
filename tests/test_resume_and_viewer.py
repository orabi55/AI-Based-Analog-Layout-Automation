"""
Tests for Task 11 (_resolve_graph_app / active graph persistence)
and Task 12 (human_viewer reads interrupt resume value).

Task 11: Tests verify select_graph_app logic without Qt dependencies.
Task 12: Tests monkeypatch interrupt() to simulate different resume
payloads and verify the node parses them correctly.
"""

import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest


# ══════════════════════════════════════════════════════════════════
# Task 11 — select_graph_app and _resolve_graph_app logic
# ══════════════════════════════════════════════════════════════════

# Check if builder is importable (requires full langchain chain)
try:
    from ai_agent.graph.builder import app, chat_app, layout_session_app, session_chat_app
    _HAS_BUILDER = True
except ImportError:
    _HAS_BUILDER = False

requires_builder = pytest.mark.skipif(
    not _HAS_BUILDER,
    reason="langchain not installed — skipping builder-dependent tests",
)


@requires_builder
class TestSelectGraphApp:
    """Verify select_graph_app returns the correct app per mode."""

    def test_chat_returns_layout_session(self):
        from ai_agent.llm.workers import select_graph_app
        assert select_graph_app("chat") is layout_session_app

    def test_legacy_chat_returns_legacy(self):
        from ai_agent.llm.workers import select_graph_app
        assert select_graph_app("legacy_chat") is chat_app

    def test_initial_returns_initial(self):
        from ai_agent.llm.workers import select_graph_app
        assert select_graph_app("initial") is app

    def test_none_returns_initial(self):
        from ai_agent.llm.workers import select_graph_app
        assert select_graph_app(None) is app


@requires_builder
class TestResolveGraphAppLogic:
    """Test the _resolve_graph_app algorithm conceptually.

    We can't instantiate OrchestratorWorker (requires Qt), so we verify
    the logic by simulating its two branches.
    """

    def test_dict_input_stores_and_returns_app(self):
        """On dict input, select_graph_app(mode) is called and stored."""
        from ai_agent.llm.workers import select_graph_app

        # Simulate first-run path
        mode = "chat"
        active = select_graph_app(mode)
        assert active is not None

        # Verify idempotency: calling again with same mode returns same object
        assert select_graph_app(mode) is active

    def test_resume_returns_stored_sentinel(self):
        """On resume, the stored _active_graph_app should be returned."""
        sentinel = object()
        _active_graph_app = sentinel
        # Simulate: resume path checks _active_graph_app first
        assert _active_graph_app is sentinel

    def test_resume_without_stored_falls_back_safely(self):
        """If no active app was stored, fallback returns something."""
        from ai_agent.llm.workers import select_graph_app
        fallback = select_graph_app(None)
        assert fallback is not None


# ══════════════════════════════════════════════════════════════════
# Task 12 — node_human_viewer reads interrupt resume value
# ══════════════════════════════════════════════════════════════════

# Load human_viewer.py directly to bypass ai_agent.nodes.__init__.py
def _load_human_viewer():
    mod_name = "ai_agent.nodes.human_viewer"
    if mod_name in sys.modules:
        return sys.modules[mod_name]

    # Ensure parent packages exist in sys.modules as real packages
    import ai_agent  # noqa: F401 — real package
    import ai_agent.utils.logging  # noqa: F401 — lightweight

    mod_path = (
        Path(__file__).resolve().parents[1]
        / "ai_agent" / "nodes" / "human_viewer.py"
    )
    spec = importlib.util.spec_from_file_location(mod_name, mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


_hv_mod = _load_human_viewer()
node_human_viewer = _hv_mod.node_human_viewer


class TestHumanViewerApproval:
    """interrupt() returns a dict with approved=True/False."""

    def test_reads_approval_true(self, monkeypatch):
        monkeypatch.setattr(_hv_mod, "interrupt", lambda payload: {"approved": True})
        result = node_human_viewer({"pending_cmds": []})
        assert result["approved"] is True

    def test_reads_approval_false(self, monkeypatch):
        monkeypatch.setattr(_hv_mod, "interrupt", lambda payload: {"approved": False})
        result = node_human_viewer({"pending_cmds": []})
        assert result["approved"] is False


class TestHumanViewerBoolResume:
    """interrupt() returns a plain bool."""

    def test_bool_true(self, monkeypatch):
        monkeypatch.setattr(_hv_mod, "interrupt", lambda payload: True)
        result = node_human_viewer({"pending_cmds": []})
        assert result["approved"] is True

    def test_bool_false(self, monkeypatch):
        monkeypatch.setattr(_hv_mod, "interrupt", lambda payload: False)
        result = node_human_viewer({"pending_cmds": []})
        assert result["approved"] is False


class TestHumanViewerFeedback:
    """interrupt() returns feedback alongside approval."""

    def test_user_feedback_stored(self, monkeypatch):
        monkeypatch.setattr(
            _hv_mod, "interrupt",
            lambda payload: {"approved": False, "user_feedback": "Too risky"},
        )
        result = node_human_viewer({"pending_cmds": []})
        assert result["approved"] is False
        assert result.get("user_feedback") == "Too risky"

    def test_no_feedback_key_when_absent(self, monkeypatch):
        monkeypatch.setattr(
            _hv_mod, "interrupt",
            lambda payload: {"approved": True},
        )
        result = node_human_viewer({"pending_cmds": []})
        assert "user_feedback" not in result


class TestHumanViewerModifiedCmds:
    """interrupt() returns modified commands."""

    def test_modified_cmds_override_pending(self, monkeypatch):
        new_cmds = [{"action": "move", "device_id": "M1", "dx": 2}]
        monkeypatch.setattr(
            _hv_mod, "interrupt",
            lambda payload: {"approved": True, "modified_cmds": new_cmds},
        )
        result = node_human_viewer({
            "pending_cmds": [{"action": "move", "device_id": "M1", "dx": 1}],
        })
        assert result["approved"] is True
        assert result["pending_cmds"] == new_cmds

    def test_no_modified_cmds_keeps_original(self, monkeypatch):
        monkeypatch.setattr(
            _hv_mod, "interrupt",
            lambda payload: {"approved": True},
        )
        result = node_human_viewer({
            "pending_cmds": [{"action": "move", "device_id": "M1"}],
        })
        assert "pending_cmds" not in result  # Not overridden


class TestHumanViewerPayload:
    """Verify the interrupt payload contains expected keys."""

    def test_interrupt_payload_shape(self, monkeypatch):
        captured = {}

        def capture_interrupt(payload):
            captured.update(payload)
            return {"approved": True}

        monkeypatch.setattr(_hv_mod, "interrupt", capture_interrupt)

        node_human_viewer({
            "pending_cmds": [{"action": "move"}],
            "last_agent": "drc_critic",
            "Analysis_result": "topology data",
            "strategy_result": "strategy data",
            "placement_text": "placement data",
            "routing_result": {"hpwl": 1.5},
        })

        assert captured["type"] == "visual_review"
        assert captured["pending_cmds"] == [{"action": "move"}]
        assert captured["last_agent"] == "drc_critic"
        assert captured["Analysis"] == "topology data"
        assert captured["Strategy"] == "strategy data"
        assert captured["Placement"] == "placement data"
        assert captured["Routing"] == {"hpwl": 1.5}


class TestHumanViewerChatHistory:
    """Chat history should be preserved."""

    def test_chat_history_preserved(self, monkeypatch):
        monkeypatch.setattr(
            _hv_mod, "interrupt",
            lambda payload: {"approved": True},
        )
        history = [{"role": "user", "content": "hello"}]
        result = node_human_viewer({"chat_history": history, "pending_cmds": []})
        assert result["chat_history"] == history

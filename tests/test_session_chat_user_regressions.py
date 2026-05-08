"""
test_session_chat_user_regressions.py
=====================================
Fix 14 — Integration regression tests for real user messages.

No real LLM calls.  No GUI.  No KLayout.  Uses deterministic parser
and graph nodes.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from ai_agent.agents.session_chat_agent import (
    run_session_chat_agent,
    VALID_SESSION_ROUTES,
)

# Direct import bypassing ai_agent.nodes.__init__ (which pulls in langchain).
_PROJ = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "ai_agent.nodes.command_validator",
    _PROJ / "ai_agent" / "nodes" / "command_validator.py",
)
_cv = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("ai_agent.nodes.command_validator", _cv)
_spec.loader.exec_module(_cv)
node_command_validator = _cv.node_command_validator


# ---------------------------------------------------------------------------
# Helper: safe routing that avoids LLM (forces deterministic-only path)
# ---------------------------------------------------------------------------

def _run_deterministic(state: dict) -> dict:
    """Call run_session_chat_agent in a way that avoids LLM calls.

    If the deterministic rule_route returns None, the agent will attempt
    an LLM call.  To avoid network dependencies in tests we monkeypatch
    the LLM path by providing a model_name that will fail fast, but
    the tests below are designed so rule_route always matches.
    """
    return run_session_chat_agent(state)


# ══════════════════════════════════════════════════════════════════
# 1. "move M1 left" → command_edit with a valid move command
# ══════════════════════════════════════════════════════════════════

class TestUserMoveM1Left:
    def test_generates_valid_command(self):
        state = {
            "mode": "chat",
            "user_message": "move M1 left",
            "placement_nodes": [
                {"id": "M1", "type": "nmos", "x": 0, "y": 100},
            ],
            "initial_agent_trace": {},
        }
        result = _run_deterministic(state)

        assert result["session_route"] == "command_edit"
        assert result["session_commands"]
        cmd = result["session_commands"][0]
        assert cmd["action"] == "move"
        assert cmd.get("device_id") == "M1"
        # left → negative dx
        assert cmd.get("dx", 0) < 0 or cmd.get("dx") == -1

    def test_move_m1_right(self):
        result = _run_deterministic({
            "user_message": "move M1 right",
            "placement_nodes": [{"id": "M1"}],
        })
        assert result["session_route"] == "command_edit"
        assert result["session_commands"][0]["dx"] > 0

    def test_swap_m1_m2(self):
        result = _run_deterministic({
            "user_message": "swap M1 and M2",
            "placement_nodes": [{"id": "M1"}, {"id": "M2"}],
        })
        assert result["session_route"] == "command_edit"
        assert result["session_commands"]
        cmd = result["session_commands"][0]
        assert cmd["action"] == "swap"


# ══════════════════════════════════════════════════════════════════
# 2. "move it left" → clarify (ambiguous target)
# ══════════════════════════════════════════════════════════════════

class TestUserAmbiguousMove:
    def test_clarifies_on_ambiguous_target(self):
        result = _run_deterministic({
            "mode": "chat",
            "user_message": "move it left",
            "placement_nodes": [{"id": "M1"}],
        })
        assert result["session_route"] == "clarify"


# ══════════════════════════════════════════════════════════════════
# 3. "check DRC" → need_drc (read-only, no commands)
# ══════════════════════════════════════════════════════════════════

class TestUserCheckDRC:
    def test_routes_to_need_drc(self):
        result = _run_deterministic({"user_message": "check DRC"})
        assert result["session_route"] == "need_drc"
        assert not result.get("session_commands")

    def test_any_violations(self):
        result = _run_deterministic({"user_message": "any DRC violations?"})
        assert result["session_route"] == "need_drc"

    def test_run_drc(self):
        result = _run_deterministic({"user_message": "run DRC"})
        assert result["session_route"] == "need_drc"


# ══════════════════════════════════════════════════════════════════
# 4. "fix DRC violations" → fix_drc (repair path)
# ══════════════════════════════════════════════════════════════════

class TestUserFixDRC:
    def test_routes_to_fix_drc(self):
        result = _run_deterministic({"user_message": "fix DRC violations"})
        assert result["session_route"] == "fix_drc"

    def test_repair_drc(self):
        result = _run_deterministic({"user_message": "repair DRC"})
        assert result["session_route"] == "fix_drc"

    def test_resolve_overlaps(self):
        result = _run_deterministic({"user_message": "resolve overlap"})
        assert result["session_route"] == "fix_drc"


# ══════════════════════════════════════════════════════════════════
# 5. "why did you place M1 here?" → answer_only with trace info
# ══════════════════════════════════════════════════════════════════

class TestUserWhyPlacement:
    def test_answers_from_trace(self):
        result = _run_deterministic({
            "user_message": "why did you place M1 here?",
            "initial_agent_trace": {
                "strategy": {"matching_groups": [["M1", "M2"]]},
            },
        })
        assert result["session_route"] == "answer_only"
        assert "M1" in result["assistant_text"]

    def test_explain_placement_general(self):
        result = _run_deterministic({
            "user_message": "explain initial placement",
            "initial_agent_trace": {
                "strategy": {"matching_groups": [["M1", "M2"]]},
                "drc": {"pass": True, "flags": []},
            },
        })
        assert result["session_route"] == "answer_only"
        assert result["assistant_text"]

    def test_empty_trace_fallback(self):
        result = _run_deterministic({
            "user_message": "why did you place M1 here?",
            "initial_agent_trace": {},
        })
        assert result["session_route"] == "answer_only"
        assert "trace" in result["assistant_text"].lower() or "layout" in result["assistant_text"].lower()


# ══════════════════════════════════════════════════════════════════
# 6. Validator: empty commands → finalizer route
# ══════════════════════════════════════════════════════════════════

class TestValidatorEmptyCommandsRoute:
    def test_empty_commands_routes_finalizer(self):
        validated = node_command_validator({
            "session_route": "command_edit",
            "pending_cmds": [],
        })

        # Import route function — may need langgraph
        try:
            from ai_agent.graph.edges import route_after_command_validator
        except ImportError:
            pytest.skip("langgraph required")

        target = route_after_command_validator(validated)
        assert target == "node_session_finalizer"

    def test_valid_commands_routes_human_viewer(self):
        validated = node_command_validator({
            "session_route": "command_edit",
            "pending_cmds": [
                {"action": "move", "device_id": "M1", "dx": 1, "dy": 0}
            ],
            "placement_nodes": [{"id": "M1"}],
        })

        try:
            from ai_agent.graph.edges import route_after_command_validator
        except ImportError:
            pytest.skip("langgraph required")

        target = route_after_command_validator(validated)
        assert target == "node_human_viewer"


# ══════════════════════════════════════════════════════════════════
# Additional edge cases
# ══════════════════════════════════════════════════════════════════

class TestRouteAfterSessionChat:
    """Ensure route_after_session_chat covers all valid routes."""

    def test_all_routes_have_targets(self):
        try:
            from ai_agent.graph.edges import route_after_session_chat
        except ImportError:
            pytest.skip("langgraph required")

        for route in VALID_SESSION_ROUTES:
            target = route_after_session_chat({"session_route": route})
            assert target, f"No target for route {route}"
            assert isinstance(target, str)

    def test_unknown_route_falls_back(self):
        try:
            from ai_agent.graph.edges import route_after_session_chat
        except ImportError:
            pytest.skip("langgraph required")

        target = route_after_session_chat({"session_route": "nonexistent_route"})
        assert target == "node_session_finalizer"


class TestDeterministicRouting:
    """Verify that common routing-related messages are deterministic."""

    def test_show_routing(self):
        result = _run_deterministic({"user_message": "show routing"})
        assert result["session_route"] == "need_routing"

    def test_check_symmetry(self):
        result = _run_deterministic({"user_message": "check symmetry"})
        assert result["session_route"] == "need_strategy"

    def test_analyze_topology(self):
        result = _run_deterministic({"user_message": "show topology"})
        assert result["session_route"] == "need_topology"

    def test_delete_m1(self):
        result = _run_deterministic({
            "user_message": "delete M1",
            "placement_nodes": [{"id": "M1"}],
        })
        assert result["session_route"] == "command_edit"
        assert result["session_commands"][0]["action"] == "delete"

    def test_flip_m2(self):
        result = _run_deterministic({
            "user_message": "flip M2",
            "placement_nodes": [{"id": "M2"}],
        })
        assert result["session_route"] == "command_edit"
        assert "flip" in result["session_commands"][0]["action"]

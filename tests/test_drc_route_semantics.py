"""
test_drc_route_semantics.py
===========================
Tests for:
  Fix 3 — Two-mode DRC route semantics (need_drc = read-only, fix_drc = active).
  Fix 4 — assistant_text on every DRC return path.
"""

import pytest

import importlib
import importlib.util
import sys
from pathlib import Path

from ai_agent.agents.session_chat_agent import (
    rule_route,
    VALID_SESSION_ROUTES,
    SPECIALIST_BY_ROUTE,
    run_session_chat_agent,
)
from ai_agent.graph.edges import (
    route_after_session_chat,
    route_after_session_drc,
    route_after_command_validator,
)


# ---------------------------------------------------------------------------
# Load drc_checker directly to avoid ai_agent.nodes.__init__ (langchain dep)
# ---------------------------------------------------------------------------
def _load_module(name, relpath):
    if name in sys.modules:
        return sys.modules[name]
    mod_path = Path(__file__).resolve().parents[1] / relpath
    spec = importlib.util.spec_from_file_location(name, mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_dc_mod = _load_module(
    "ai_agent.nodes.drc_checker",
    "ai_agent/nodes/drc_checker.py",
)
node_drc_checker = _dc_mod.node_drc_checker
_format_drc_assistant_text = _dc_mod._format_drc_assistant_text


# ══════════════════════════════════════════════════════════════════
# Fix 3 — Route constants and rule_route
# ══════════════════════════════════════════════════════════════════

class TestFixDrcRouteConstants:
    """fix_drc must be a first-class route."""

    def test_fix_drc_in_valid_session_routes(self):
        assert "fix_drc" in VALID_SESSION_ROUTES

    def test_fix_drc_in_specialist_by_route(self):
        assert SPECIALIST_BY_ROUTE.get("fix_drc") == "drc_critic"

    def test_need_drc_maps_to_drc_checker(self):
        assert SPECIALIST_BY_ROUTE.get("need_drc") == "drc_checker"


class TestRuleRouteCheckDrc:
    """Read-only DRC keywords should route to need_drc."""

    @pytest.mark.parametrize("msg", [
        "check DRC",
        "run DRC",
        "check DRC violations",
        "DRC status",
        "is there a spacing violation?",
        "any spacing violation?",
    ])
    def test_check_drc_routes_to_need_drc(self, msg):
        assert rule_route(msg) == "need_drc"


class TestRuleRouteFixDrc:
    """Fix/repair DRC keywords should route to fix_drc."""

    @pytest.mark.parametrize("msg", [
        "fix DRC",
        "fix DRC violations",
        "repair DRC",
        "fix the DRC",
        "resolve violation",
        "fix violation",
        "fix overlap",
        "repair spacing",
        "fix spacing",
        "resolve overlap",
        "repair violation",
        "repair overlap",
        "fix design rule",
        "heal DRC",
    ])
    def test_fix_drc_routes_to_fix_drc(self, msg):
        assert rule_route(msg) == "fix_drc"

    def test_fix_drc_beats_generic_drc(self):
        """'fix DRC violations' must be fix_drc, not need_drc."""
        assert rule_route("fix DRC violations") == "fix_drc"


class TestRuleRouteFixDrcPriority:
    """fix_drc keywords must be checked BEFORE generic DRC words."""

    def test_fix_violation_is_fix_drc(self):
        assert rule_route("fix violation please") == "fix_drc"

    def test_check_drc_is_still_need_drc(self):
        assert rule_route("check DRC") == "need_drc"

    def test_command_edit_still_beats_fix_drc(self):
        """Strong command verbs still win over fix_drc."""
        assert rule_route("move M1 to fix DRC violation") == "command_edit"


# ══════════════════════════════════════════════════════════════════
# Fix 3 — Edge functions
# ══════════════════════════════════════════════════════════════════

class TestRouteAfterSessionChat:
    """route_after_session_chat must handle both DRC routes."""

    def test_need_drc_routes_to_checker(self):
        assert route_after_session_chat({"session_route": "need_drc"}) == "node_drc_checker"

    def test_fix_drc_routes_to_critic(self):
        assert route_after_session_chat({"session_route": "fix_drc"}) == "node_drc_critic"


class TestRouteAfterSessionDrc:
    """Conditional edge after node_drc_critic in session graph."""

    def test_fix_drc_with_commands_goes_to_validator(self):
        state = {
            "session_route": "fix_drc",
            "pending_cmds": [{"action": "move", "device": "M1"}],
        }
        assert route_after_session_drc(state) == "node_command_validator"

    def test_fix_drc_without_commands_goes_to_finalizer(self):
        state = {
            "session_route": "fix_drc",
            "pending_cmds": [],
        }
        assert route_after_session_drc(state) == "node_session_finalizer"

    def test_need_drc_always_goes_to_finalizer(self):
        """need_drc should never route to validator even with stale cmds."""
        state = {
            "session_route": "need_drc",
            "pending_cmds": [{"action": "move", "device": "M1"}],
        }
        assert route_after_session_drc(state) == "node_session_finalizer"

    def test_fix_drc_none_cmds_goes_to_finalizer(self):
        state = {"session_route": "fix_drc", "pending_cmds": None}
        assert route_after_session_drc(state) == "node_session_finalizer"

    def test_missing_route_goes_to_finalizer(self):
        state = {"pending_cmds": [{"action": "move"}]}
        assert route_after_session_drc(state) == "node_session_finalizer"


# ══════════════════════════════════════════════════════════════════
# Fix 3 — run_session_chat_agent integration
# ══════════════════════════════════════════════════════════════════

class TestRunSessionChatAgentDrcRoutes:
    """run_session_chat_agent should distinguish need_drc and fix_drc."""

    def test_check_drc_produces_need_drc(self):
        result = run_session_chat_agent({"user_message": "check DRC"})
        assert result["session_route"] == "need_drc"
        assert result["requires_specialist"] is True
        assert result["specialist_target"] == "drc_checker"

    def test_fix_drc_produces_fix_drc(self):
        result = run_session_chat_agent({"user_message": "fix DRC violations"})
        assert result["session_route"] == "fix_drc"
        assert result["requires_specialist"] is True
        assert result["specialist_target"] == "drc_critic"

    def test_need_drc_has_no_commands(self):
        result = run_session_chat_agent({"user_message": "check DRC"})
        assert result["pending_cmds"] == []
        assert result["session_commands"] == []


# ══════════════════════════════════════════════════════════════════
# Fix 3 — node_drc_checker (read-only DRC)
# ══════════════════════════════════════════════════════════════════

class TestNodeDrcChecker:
    """node_drc_checker must be read-only — no placement mutation."""

    def test_no_placement_mutation(self):
        """placement_nodes should not appear in the return dict."""
        nodes = [
            {
                "id": "M1", "type": "nmos",
                "geometry": {"x": 0, "y": 0, "width": 1, "height": 1},
            },
        ]
        result = node_drc_checker({"placement_nodes": nodes})
        assert "placement_nodes" not in result

    def test_no_pending_cmds(self):
        """pending_cmds should not appear in the return dict."""
        nodes = [
            {
                "id": "M1", "type": "nmos",
                "geometry": {"x": 0, "y": 0, "width": 1, "height": 1},
            },
        ]
        result = node_drc_checker({"placement_nodes": nodes})
        assert "pending_cmds" not in result

    def test_drc_pass_has_assistant_text(self):
        nodes = [
            {
                "id": "M1", "type": "nmos",
                "geometry": {"x": 0, "y": 0, "width": 0.3, "height": 0.5},
            },
        ]
        result = node_drc_checker({"placement_nodes": nodes})
        assert result["assistant_text"]
        assert result["drc_pass"] is True
        assert "pass" in result["assistant_text"].lower()

    def test_drc_fail_has_assistant_text(self):
        """Two overlapping devices should produce violations and assistant text."""
        nodes = [
            {
                "id": "M1", "type": "nmos",
                "geometry": {"x": 0, "y": 0, "width": 1, "height": 1},
            },
            {
                "id": "M2", "type": "nmos",
                "geometry": {"x": 0.5, "y": 0, "width": 1, "height": 1},
            },
        ]
        result = node_drc_checker({"placement_nodes": nodes})
        assert result["drc_pass"] is False
        assert result["assistant_text"]
        assert "violation" in result["assistant_text"].lower()
        assert len(result["drc_flags"]) > 0

    def test_last_agent_is_drc_checker(self):
        result = node_drc_checker({"placement_nodes": []})
        assert result["last_agent"] == "drc_checker"

    def test_empty_nodes(self):
        """Empty placement should pass DRC."""
        result = node_drc_checker({"placement_nodes": []})
        assert result["drc_pass"] is True


# ══════════════════════════════════════════════════════════════════
# Fix 4 — _format_drc_assistant_text helper
# ══════════════════════════════════════════════════════════════════

class TestFormatDrcAssistantText:
    """Tests for the shared DRC text formatting helper."""

    def test_pass(self):
        text = _format_drc_assistant_text(True, [])
        assert "pass" in text.lower()
        assert "no violations" in text.lower()

    def test_pass_with_prefix(self):
        text = _format_drc_assistant_text(True, [], prefix="Read-only ")
        assert text.startswith("Read-only ")

    def test_fail_with_flags(self):
        flags = [{"value": "OVERLAP: M1 vs M2"}]
        text = _format_drc_assistant_text(False, flags)
        assert "1 violation" in text
        assert "OVERLAP" in text

    def test_fail_many_flags(self):
        flags = [{"value": f"Violation {i}"} for i in range(15)]
        text = _format_drc_assistant_text(False, flags)
        assert "15 violation" in text
        assert "… and 5 more" in text

    def test_fail_empty_flags(self):
        text = _format_drc_assistant_text(False, [])
        assert "completed" in text.lower()

    def test_fail_with_description_key(self):
        flags = [{"description": "GAP between M1 and M2"}]
        text = _format_drc_assistant_text(False, flags)
        assert "GAP between" in text


# ══════════════════════════════════════════════════════════════════
# Fix 4 — node_drc_critic early-pass assistant_text
# ══════════════════════════════════════════════════════════════════

class TestDrcCriticEarlyPassAssistantText:
    """Every return path from node_drc_critic must include assistant_text."""

    def test_early_pass_has_assistant_text(self, monkeypatch):
        """Monkeypatch run_drc_check to return pass → early return must have text."""
        # drc_critic imports _shared which needs langchain; skip if unavailable
        try:
            _drc_mod = _load_module(
                "ai_agent.nodes.drc_critic",
                "ai_agent/nodes/drc_critic.py",
            )
        except (ImportError, ModuleNotFoundError):
            pytest.skip("langchain not installed — cannot import drc_critic node")

        def fake_drc_check(nodes, gap_um, **kwargs):
            return {"pass": True, "violations": [], "structured": [], "summary": "OK"}

        monkeypatch.setattr(_drc_mod, "run_drc_check", fake_drc_check)

        state = {
            "placement_nodes": [{"id": "M1", "geometry": {"x": 0, "y": 0, "width": 1, "height": 1}}],
            "pending_cmds": [],
            "gap_px": 0.0,
            "terminal_nets": {},
            "edges": [],
            "user_message": "",
            "constraint_text": "",
            "selected_model": "Gemini",
            "chat_history": [],
        }
        result = _drc_mod.node_drc_critic(state)
        assert result["drc_pass"] is True
        assert result.get("assistant_text"), "Early-pass return must include assistant_text"
        assert "pass" in result["assistant_text"].lower()


# ══════════════════════════════════════════════════════════════════
# Fix 3 — End-to-end: need_drc flow produces no commands
# ══════════════════════════════════════════════════════════════════

class TestNeedDrcFlowNoMutation:
    """The need_drc → drc_checker → finalizer path should never produce commands."""

    def test_need_drc_full_path_no_pending_cmds(self):
        # Step 1: Agent routes to need_drc
        agent_result = run_session_chat_agent({"user_message": "check DRC"})
        assert agent_result["session_route"] == "need_drc"
        assert agent_result["pending_cmds"] == []

        # Step 2: DRC checker produces read-only result
        checker_result = node_drc_checker({"placement_nodes": []})
        assert "pending_cmds" not in checker_result
        assert "placement_nodes" not in checker_result

        # Step 3: Route after checker goes to finalizer
        merged = {**agent_result, **checker_result}
        # The finalizer edge is unconditional for drc_checker (add_edge),
        # but verify the DRC conditional edge would also go to finalizer
        assert route_after_session_drc(merged) == "node_session_finalizer"

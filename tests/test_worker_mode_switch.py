"""
test_worker_mode_switch.py
==========================
Tests for Task 8 (Worker mode switch) and Task 9 (Deterministic tool stability).

Task 8: Verifies select_graph_app routing, decision labels, and event extraction.
Task 9: Verifies deterministic tool checklist (parser, validator, slot filling).
"""

import pytest


# ══════════════════════════════════════════════════════════════════
# Task 8 — Worker mode switch
# ══════════════════════════════════════════════════════════════════


class TestSelectGraphApp:
    """Verify select_graph_app routes modes to correct apps."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_langgraph(self):
        try:
            from langgraph.graph import StateGraph
        except ImportError:
            pytest.skip("langgraph not available")

    def test_chat_v2_routes_to_layout_session_app(self):
        from ai_agent.llm.workers import select_graph_app
        from ai_agent.graph.builder import layout_session_app
        assert select_graph_app("chat_v2") is layout_session_app

    def test_chat_routes_to_session_chat_app(self):
        from ai_agent.llm.workers import select_graph_app
        from ai_agent.graph.builder import session_chat_app
        assert select_graph_app("chat") is session_chat_app

    def test_session_chat_legacy_routes_to_session_chat_app(self):
        from ai_agent.llm.workers import select_graph_app
        from ai_agent.graph.builder import session_chat_app
        assert select_graph_app("session_chat_legacy") is session_chat_app

    def test_legacy_chat_routes_to_chat_app(self):
        from ai_agent.llm.workers import select_graph_app
        from ai_agent.graph.builder import chat_app
        assert select_graph_app("legacy_chat") is chat_app

    def test_initial_routes_to_initial_app(self):
        from ai_agent.llm.workers import select_graph_app
        from ai_agent.graph.builder import app
        assert select_graph_app("initial") is app

    def test_none_routes_to_initial_app(self):
        from ai_agent.llm.workers import select_graph_app
        from ai_agent.graph.builder import app
        assert select_graph_app(None) is app


class TestGraphAppLabel:
    """Verify _graph_app_label returns correct labels."""

    @pytest.fixture(autouse=True)
    def _skip_if_no_langgraph(self):
        try:
            from langgraph.graph import StateGraph
        except ImportError:
            pytest.skip("langgraph not available")

    def test_layout_session_app_label(self):
        from ai_agent.llm.workers import _graph_app_label
        from ai_agent.graph.builder import layout_session_app
        assert _graph_app_label(layout_session_app) == "layout_session_app"

    def test_session_chat_app_label(self):
        from ai_agent.llm.workers import _graph_app_label
        from ai_agent.graph.builder import session_chat_app
        assert _graph_app_label(session_chat_app) == "session_chat_app"


class TestLayoutSessionDecisionLabels:
    """Verify LAYOUT_SESSION_DECISION_LABELS covers all valid decisions."""

    def test_labels_cover_all_decisions(self):
        from ai_agent.llm.workers import LAYOUT_SESSION_DECISION_LABELS
        from ai_agent.agents.layout_session_agent import VALID_LAYOUT_SESSION_DECISIONS
        missing = VALID_LAYOUT_SESSION_DECISIONS - set(LAYOUT_SESSION_DECISION_LABELS.keys())
        assert not missing, f"Missing labels for decisions: {missing}"

    def test_no_extra_labels(self):
        from ai_agent.llm.workers import LAYOUT_SESSION_DECISION_LABELS
        from ai_agent.agents.layout_session_agent import VALID_LAYOUT_SESSION_DECISIONS
        extra = set(LAYOUT_SESSION_DECISION_LABELS.keys()) - VALID_LAYOUT_SESSION_DECISIONS
        assert not extra, f"Extra labels not in decisions: {extra}"

    def test_all_labels_are_strings(self):
        from ai_agent.llm.workers import LAYOUT_SESSION_DECISION_LABELS
        for k, v in LAYOUT_SESSION_DECISION_LABELS.items():
            assert isinstance(v, str), f"Label for {k} is not a string"


class TestExtractLayoutSessionDecisionFromEvent:
    """Verify streaming event extraction for chat_v2 graph."""

    def test_extracts_decision_from_valid_event(self):
        from ai_agent.llm.workers import extract_layout_session_decision_from_event
        event = {
            "node_layout_session_agent": {
                "layout_session_decision": "check_drc",
            },
        }
        assert extract_layout_session_decision_from_event(event) == "check_drc"

    def test_returns_none_for_empty_event(self):
        from ai_agent.llm.workers import extract_layout_session_decision_from_event
        assert extract_layout_session_decision_from_event({}) is None

    def test_returns_none_for_non_dict(self):
        from ai_agent.llm.workers import extract_layout_session_decision_from_event
        assert extract_layout_session_decision_from_event("not a dict") is None

    def test_returns_none_for_missing_decision(self):
        from ai_agent.llm.workers import extract_layout_session_decision_from_event
        event = {"node_layout_session_agent": {"some_other_key": "value"}}
        assert extract_layout_session_decision_from_event(event) is None

    def test_returns_none_for_wrong_node(self):
        from ai_agent.llm.workers import extract_layout_session_decision_from_event
        event = {"node_session_chat": {"layout_session_decision": "answer"}}
        assert extract_layout_session_decision_from_event(event) is None


class TestChatModeEnv:
    def test_chat_mode_env_valid(self, monkeypatch):
        from ai_agent.llm.workers import get_chat_mode_from_env
        monkeypatch.setenv("ANALOG_LAYOUT_CHAT_MODE", "chat_v2")
        assert get_chat_mode_from_env() == "chat_v2"

    def test_chat_mode_env_invalid(self, monkeypatch):
        from ai_agent.llm.workers import get_chat_mode_from_env
        monkeypatch.setenv("ANALOG_LAYOUT_CHAT_MODE", "bad")
        assert get_chat_mode_from_env() == "chat"


# ══════════════════════════════════════════════════════════════════
# Task 9 — Deterministic tool stability checklist
# ══════════════════════════════════════════════════════════════════

from ai_agent.agents.session_chat_agent import (
    parse_direct_edit_command,
    try_fill_edit_slots,
    _build_partial_move_intent,
    _extract_target_nets,
    rule_route,
)
from ai_agent.nodes.command_validator import node_command_validator


class TestDeterministicToolChecklist:
    """Task 9: Each item in the stability checklist."""

    # 1. parse_direct_edit_command("Move MM1 to the left") returns move command
    def test_move_mm1_to_the_left(self):
        cmds = parse_direct_edit_command("Move MM1 to the left", [{"id": "MM1"}])
        assert len(cmds) == 1
        assert cmds[0]["action"] == "move"
        assert cmds[0]["device_id"] == "MM1"

    # 2. parse_direct_edit_command("move left") creates pending intent or empty
    def test_move_left_without_device(self):
        cmds = parse_direct_edit_command("move left", [{"id": "M1"}, {"id": "M2"}])
        # Should return empty (ambiguous) — not an invalid command
        assert cmds == []
        # Should be able to create a partial intent
        partial = _build_partial_move_intent("move left")
        assert partial is not None
        assert "device_id" in partial.get("missing", [])

    # 3. try_fill_edit_slots("Target device is MM1") completes move-left
    def test_fill_slot_with_mm1(self):
        pending = {"action": "move", "dx": -1, "dy": 0, "missing": ["device_id"]}
        result = try_fill_edit_slots("Target device is MM1", pending, [{"id": "MM1"}])
        assert result is not None
        assert result["device_id"] == "MM1"
        assert result["action"] == "move"

    # 4. parse_direct_edit_command("align M1 with M2") returns unsupported/empty
    def test_align_is_unsupported(self):
        cmds = parse_direct_edit_command("align M1 with M2", [{"id": "M1"}, {"id": "M2"}])
        # Should return empty — align is not supported
        assert cmds == []

    # 5. parse_direct_edit_command("add dummy") clarifies
    def test_add_dummy_without_context(self):
        cmds = parse_direct_edit_command("add dummy", [{"id": "M1"}])
        # Should return empty — needs target+side
        assert cmds == []

    # 6. parse_direct_edit_command("add dummy left of M1") returns target+side
    def test_add_dummy_left_of_m1(self):
        nodes = [{"id": "M1", "type": "nmos", "geometry": {"x": 1.0, "y": 0.0, "width": 0.294}}]
        cmds = parse_direct_edit_command("add dummy left of M1", nodes)
        assert len(cmds) >= 1
        cmd = cmds[0]
        assert cmd["action"] in ("add_dummy", "add_dummies", "dummy", "add dummy")
        assert cmd.get("target") == "M1" or cmd.get("side") == "left"

    # 7. command_validator rejects unsupported actions
    def test_validator_rejects_unsupported(self):
        state = {
            "pending_cmds": [{"action": "align", "device_id": "M1"}],
            "placement_nodes": [{"id": "M1"}],
        }
        result = node_command_validator(state)
        assert len(result["pending_cmds"]) == 0

    # 8. command_validator expands or rejects move_pair
    def test_validator_handles_move_pair(self):
        state = {
            "pending_cmds": [{"action": "move_pair", "devices": ["M1", "M2"], "dx": 1, "dy": 0}],
            "placement_nodes": [{"id": "M1"}, {"id": "M2"}],
        }
        result = node_command_validator(state)
        # Should expand into individual move commands
        assert len(result["pending_cmds"]) == 2
        assert all(cmd["action"] == "move" for cmd in result["pending_cmds"])

    # 9. cmd_parser applies device_id for move/swap/flip/delete
    def test_parser_device_id_for_move(self):
        cmds = parse_direct_edit_command("move M1 right", [{"id": "M1"}])
        assert cmds[0].get("device_id") == "M1"

    def test_parser_device_id_for_flip(self):
        cmds = parse_direct_edit_command("flip M1", [{"id": "M1"}])
        assert cmds[0].get("device_id") == "M1"

    def test_parser_device_id_for_swap(self):
        cmds = parse_direct_edit_command("swap M1 and M2", [{"id": "M1"}, {"id": "M2"}])
        assert cmds[0].get("device_a") == "M1" or cmds[0].get("device_id") == "M1"

    def test_parser_device_id_for_delete(self):
        cmds = parse_direct_edit_command("delete M1", [{"id": "M1"}])
        assert cmds[0].get("device_id") == "M1"

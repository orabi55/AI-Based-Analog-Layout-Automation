"""
test_drc_context_passes_critical.py
=====================================
Verifies that node_drc_critic forwards placement_goals (including
critical_nets) to build_placement_context.

Uses unittest.mock to intercept the call without running the full LLM pipeline.
"""
import json
import unittest.mock as mock
import pytest


_MOCK_DRC_VIOLATIONS = {
    "pass": False,
    "violations": ["overlap: MM1 @ (0,0) and MM2 @ (0,0)"],
    "structured": [],
}

_MOCK_NODES = [
    {"id": "MM1", "type": "nmos",
     "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.668}},
    {"id": "MM2", "type": "nmos",
     "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.668}},
]

_GOALS_WITH_CRIT = {
    "matching_priority": "High",
    "critical_nets": {
        "priority": "High",
        "nets": ["VOUTP", "VOUTN"],
    },
}


class TestDrcCriticPassesCritical:

    def _run_drc_with_goals(self, goals):
        """Run node_drc_critic with mocked LLM and check build_placement_context args."""
        state = {
            "placement_nodes": _MOCK_NODES,
            "pending_cmds": [],
            "gap_px": 0.0,
            "terminal_nets": {},
            "edges": [],
            "user_message": "fix",
            "constraint_text": "",
            "selected_model": "Gemini",
            "chat_history": [],
            "deterministic_snapshot": _MOCK_NODES,
            "drc_retry_count": 0,
            "placement_goals": goals,
        }

        captured_goals = {}

        def _fake_build_ctx(nodes, constraint_text, *, terminal_nets=None,
                            edges=None, placement_goals=None, **kwargs):
            captured_goals["value"] = placement_goals
            return "FAKE_CONTEXT"

        with mock.patch(
            "ai_agent.nodes.drc_critic.build_placement_context",
            side_effect=_fake_build_ctx,
        ), mock.patch(
            "ai_agent.nodes.drc_critic.run_drc_check",
            return_value=_MOCK_DRC_VIOLATIONS,
        ), mock.patch(
            "ai_agent.nodes.drc_critic.format_drc_violations_for_llm",
            return_value="VIOLATIONS",
        ), mock.patch(
            "ai_agent.nodes.drc_critic._invoke_with_retry",
            return_value=mock.MagicMock(content="no fixes"),
        ), mock.patch(
            "ai_agent.nodes.drc_critic.compute_prescriptive_fixes",
            return_value=[],
        ), mock.patch(
            "ai_agent.nodes.drc_critic.aggregate_to_logical_devices",
            return_value=[],
        ), mock.patch(
            "ai_agent.nodes.drc_critic.apply_cmds_to_nodes",
            return_value=_MOCK_NODES,
        ), mock.patch(
            "ai_agent.nodes.drc_critic.resolve_overlaps",
            return_value=[],
        ), mock.patch(
            "ai_agent.nodes.drc_critic.enforce_reflection_symmetry",
            return_value=_MOCK_NODES,
        ), mock.patch(
            "ai_agent.nodes.drc_critic.legalize_vertical_rows",
            return_value=_MOCK_NODES,
        ), mock.patch(
            "ai_agent.nodes.drc_critic._update_and_save_chat_history",
            return_value=[],
        ):
            # run_drc_check is called twice (initial + re-check), mock both
            with mock.patch(
                "ai_agent.nodes.drc_critic.run_drc_check",
                side_effect=[_MOCK_DRC_VIOLATIONS, {"pass": True, "violations": [], "structured": []}],
            ):
                from ai_agent.nodes.drc_critic import node_drc_critic
                node_drc_critic(state)

        return captured_goals.get("value")

    def test_placement_goals_forwarded(self):
        """build_placement_context must receive placement_goals from state."""
        result = self._run_drc_with_goals(_GOALS_WITH_CRIT)
        assert result is not None
        assert result.get("critical_nets", {}).get("priority") == "High"

    def test_no_goals_forwarded_as_none_or_empty(self):
        """When state has no placement_goals, forwarded value is None or {}."""
        result = self._run_drc_with_goals(None)
        # Either None or empty dict is acceptable — the key point is no crash
        assert result is None or result == {}

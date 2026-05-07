"""
test_initial_agent_trace.py
===========================
Unit tests for build_initial_agent_trace() in ai_agent.graph.state_utils.

Tests:
- Correct extraction of all five top-level keys from a populated state.
- Graceful handling of a completely empty state dict.
- Graceful handling of partially populated states (missing DRC, routing, etc.).
- Placement sub-dict always contains the three expected keys.
- Defaults: missing lists are [], missing booleans/ints are falsy but present.
"""

import pytest
from ai_agent.graph.state_utils import build_initial_agent_trace


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FULL_STATE = {
    "Analysis_result":        {"diff_pairs": [["M1", "M2"]]},
    "strategy_result":        {"symmetry_axis": "vertical"},
    "original_placement_cmds": [{"action": "move", "device": "M1", "x": 0, "y": 0}],
    "placement_nodes":        [{"id": "M1"}],
    "deterministic_snapshot": [{"id": "M1"}],
    "routing_result":         {"hpwl": 12.5, "log_text": "ok"},
    "drc_pass":               True,
    "drc_flags":              [],
    "drc_retry_count":        0,
}


# ---------------------------------------------------------------------------
# 1. Task-prescribed tests
# ---------------------------------------------------------------------------

class TestBuildInitialAgentTrace:

    def test_build_initial_agent_trace_handles_missing_fields(self):
        """Partial state: only topology, strategy and drc_pass are present."""
        state = {
            "Analysis_result": {"diff_pairs": [["M1", "M2"]]},
            "strategy_result": {"symmetry_axis": "vertical"},
            "drc_pass": True,
        }

        trace = build_initial_agent_trace(state)

        assert trace["topology"] == {"diff_pairs": [["M1", "M2"]]}
        assert trace["strategy"] == {"symmetry_axis": "vertical"}
        assert trace["drc"]["pass"] is True
        assert "placement" in trace
        assert "routing" in trace

    def test_build_initial_agent_trace_empty_state(self):
        """Completely empty state must still produce a valid trace skeleton."""
        trace = build_initial_agent_trace({})

        assert isinstance(trace, dict)
        assert "topology" in trace
        assert "strategy" in trace
        assert "placement" in trace
        assert "routing" in trace
        assert "drc" in trace


# ---------------------------------------------------------------------------
# 2. Extended correctness tests
# ---------------------------------------------------------------------------

class TestBuildInitialAgentTraceExtended:

    def test_full_state_topology_and_strategy(self):
        trace = build_initial_agent_trace(_FULL_STATE)
        assert trace["topology"] == {"diff_pairs": [["M1", "M2"]]}
        assert trace["strategy"] == {"symmetry_axis": "vertical"}

    def test_full_state_placement_sub_keys(self):
        trace = build_initial_agent_trace(_FULL_STATE)
        placement = trace["placement"]
        assert "original_placement_cmds" in placement
        assert "placement_nodes" in placement
        assert "deterministic_snapshot" in placement

    def test_full_state_placement_contents(self):
        trace = build_initial_agent_trace(_FULL_STATE)
        assert trace["placement"]["original_placement_cmds"] == [
            {"action": "move", "device": "M1", "x": 0, "y": 0}
        ]
        assert trace["placement"]["placement_nodes"] == [{"id": "M1"}]
        assert trace["placement"]["deterministic_snapshot"] == [{"id": "M1"}]

    def test_full_state_routing(self):
        trace = build_initial_agent_trace(_FULL_STATE)
        assert trace["routing"]["hpwl"] == pytest.approx(12.5)

    def test_full_state_drc(self):
        trace = build_initial_agent_trace(_FULL_STATE)
        assert trace["drc"]["pass"] is True
        assert trace["drc"]["flags"] == []
        assert trace["drc"]["retry_count"] == 0

    # ------------------------------------------------------------------
    # Missing individual sections
    # ------------------------------------------------------------------

    def test_missing_drc_fields_default_to_falsy(self):
        """State with no DRC fields: sub-dict must be well-formed."""
        trace = build_initial_agent_trace({"Analysis_result": "ok"})
        drc = trace["drc"]
        assert "pass" in drc
        assert "flags" in drc
        assert "retry_count" in drc
        assert drc["flags"] == []
        assert drc["retry_count"] == 0

    def test_missing_routing_defaults_to_empty_dict(self):
        trace = build_initial_agent_trace({})
        assert trace["routing"] == {}

    def test_missing_placement_lists_default_to_empty(self):
        trace = build_initial_agent_trace({})
        p = trace["placement"]
        assert p["original_placement_cmds"] == []
        assert p["placement_nodes"] == []
        assert p["deterministic_snapshot"] == []

    def test_none_fields_treated_as_missing(self):
        """Explicit None values must fall back to defaults (not propagate None)."""
        state = {
            "original_placement_cmds": None,
            "placement_nodes":         None,
            "drc_flags":               None,
            "routing_result":          None,
        }
        trace = build_initial_agent_trace(state)
        assert trace["placement"]["original_placement_cmds"] == []
        assert trace["placement"]["placement_nodes"] == []
        assert trace["drc"]["flags"] == []
        assert trace["routing"] == {}

    # ------------------------------------------------------------------
    # Isolation: returned trace must not alias state lists
    # ------------------------------------------------------------------

    def test_trace_returns_new_dict(self):
        """build_initial_agent_trace must return a fresh dict each call."""
        state = {"Analysis_result": "x"}
        trace1 = build_initial_agent_trace(state)
        trace2 = build_initial_agent_trace(state)
        assert trace1 is not trace2

    # ------------------------------------------------------------------
    # Re-export from placement_worker
    # ------------------------------------------------------------------

    def test_reexported_from_placement_worker(self):
        """build_initial_agent_trace must be importable from placement_worker."""
        from ai_agent.llm.placement_worker import build_initial_agent_trace as fn
        assert callable(fn)
        result = fn({})
        assert "topology" in result

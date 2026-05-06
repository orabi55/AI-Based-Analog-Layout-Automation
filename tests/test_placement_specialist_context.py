"""
test_placement_specialist_context.py
=====================================
Tests that build_placement_context():
  - Appends a [CRITICAL_NETS] block when critical nets are ON.
  - Does NOT append the block when OFF (same context as before).
"""
import pytest
from ai_agent.agents.placement_specialist import build_placement_context


# Minimal node fixtures
_NODES = [
    {"id": "MM1_f0", "type": "nmos", "electrical": {"nf": 1},
     "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.668}},
    {"id": "MM2_f0", "type": "nmos", "electrical": {"nf": 1},
     "geometry": {"x": 0.294, "y": 0.0, "width": 0.294, "height": 0.668}},
    {"id": "MM3_f0", "type": "pmos", "electrical": {"nf": 1},
     "geometry": {"x": 0.0, "y": 0.668, "width": 0.294, "height": 0.818}},
]

_TERMINAL_NETS = {
    "MM1_f0": {"D": "VOUTP", "G": "VIN",  "S": "VSS"},
    "MM2_f0": {"D": "VOUTN", "G": "VIN",  "S": "VSS"},
    "MM3_f0": {"D": "VDD",   "G": "VOUTP","S": "VDD"},
}

_EDGES = [
    {"source": "MM1_f0", "target": "MM3_f0", "net": "VOUTP"},
]


def _build(placement_goals=None):
    return build_placement_context(
        _NODES, "",
        terminal_nets=_TERMINAL_NETS,
        edges=_EDGES,
        placement_goals=placement_goals,
    )


class TestPlacementSpecialistContext:

    def test_off_path_no_block(self):
        """When feature is off, [CRITICAL_NETS] must NOT appear in context."""
        ctx = _build(placement_goals=None)
        assert "[CRITICAL_NETS]" not in ctx

    def test_low_priority_no_block(self):
        """Low priority = feature off."""
        goals = {"critical_nets": {"priority": "Low", "nets": ["VOUTP"]}}
        ctx = _build(placement_goals=goals)
        assert "[CRITICAL_NETS]" not in ctx

    def test_high_priority_block_present(self):
        """High priority with nets → block must appear."""
        goals = {"critical_nets": {"priority": "High", "nets": ["VOUTP"]}}
        ctx = _build(placement_goals=goals)
        assert "[CRITICAL_NETS]" in ctx
        assert "[/CRITICAL_NETS]" in ctx
        assert "net=VOUTP" in ctx
        assert "weight=10" in ctx

    def test_medium_priority_block_present(self):
        goals = {"critical_nets": {"priority": "Medium", "nets": ["VOUTP"]}}
        ctx = _build(placement_goals=goals)
        assert "[CRITICAL_NETS]" in ctx
        assert "weight=5" in ctx

    def test_absent_phrase_present(self):
        """The mandatory safety phrase must be in the system prompt."""
        from ai_agent.agents.placement_specialist import PLACEMENT_SPECIALIST_PROMPT
        assert "If the block is ABSENT or empty, IGNORE these instructions" in \
               PLACEMENT_SPECIALIST_PROMPT

    def test_off_path_context_unchanged(self):
        """Turning the feature off should give the same context as not passing goals."""
        ctx_none  = _build(placement_goals=None)
        ctx_empty = _build(placement_goals={})
        # Both should lack [CRITICAL_NETS]
        assert "[CRITICAL_NETS]" not in ctx_none
        assert "[CRITICAL_NETS]" not in ctx_empty

    def test_supply_net_not_in_block(self):
        """Supply nets like VDD must be dropped even if user tried to add them."""
        goals = {"critical_nets": {"priority": "High", "nets": ["VDD", "VOUTP"]}}
        ctx = _build(placement_goals=goals)
        if "[CRITICAL_NETS]" in ctx:
            assert "net=VDD" not in ctx

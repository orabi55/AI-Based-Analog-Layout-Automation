"""
test_session_router.py
======================
Tests for the deterministic session rule router in
ai_agent.agents.session_chat_agent.

Covers:
- Task-prescribed parametrised cases (all 13 examples from the spec).
- normalize_route() with invalid / None inputs.
- Edge cases: empty string, mixed case, partial keyword in longer word.
- VALID_SESSION_ROUTES completeness check.
"""

import pytest
from ai_agent.agents.session_chat_agent import (
    rule_route,
    normalize_route,
    VALID_SESSION_ROUTES,
)


# ---------------------------------------------------------------------------
# 1. Task-prescribed parametrised test (exact spec)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "message, expected",
    [
        ("move M1 left",                           "command_edit"),
        ("swap M1 and M2",                         "command_edit"),
        ("flip M3",                                "command_edit"),
        ("check DRC",                              "need_drc"),
        ("any spacing violation?",                 "need_drc"),
        ("check routing crossings",                "need_routing"),
        ("is wirelength okay?",                    "need_routing"),
        ("use common centroid",                    "need_strategy"),
        ("preserve symmetry",                      "need_strategy"),
        ("what devices are connected to net out?", "need_topology"),
        ("find the current mirror",                "need_topology"),
        ("why did you place M1 here?",             "answer_only"),
        ("explain initial placement",              "answer_only"),
    ],
)
def test_rule_route(message, expected):
    assert rule_route(message) == expected


def test_unknown_route_normalizes_to_clarify():
    assert normalize_route("abc")  == "clarify"
    assert normalize_route(None)   == "clarify"


# ---------------------------------------------------------------------------
# 2. Acceptance criteria from the spec (explicit one-liners)
# ---------------------------------------------------------------------------

class TestAcceptanceCriteria:

    def test_move_is_command_edit(self):
        assert rule_route("move M1 left") == "command_edit"

    def test_check_drc_is_need_drc(self):
        assert rule_route("check DRC") == "need_drc"

    def test_routing_okay_is_need_routing(self):
        assert rule_route("is routing okay?") == "need_routing"

    def test_why_is_answer_only(self):
        assert rule_route("why did you place M1 here?") == "answer_only"

    def test_bad_route_normalizes_to_clarify(self):
        assert normalize_route("bad_route") == "clarify"


# ---------------------------------------------------------------------------
# 3. Extended edge-case tests
# ---------------------------------------------------------------------------

class TestRuleRouteEdgeCases:

    def test_empty_string_returns_none(self):
        assert rule_route("") is None

    def test_none_input_returns_none(self):
        assert rule_route(None) is None  # type: ignore[arg-type]

    def test_mixed_case_is_handled(self):
        """Rule matching must be case-insensitive."""
        assert rule_route("MOVE M1 LEFT") == "command_edit"
        assert rule_route("Check DRC") == "need_drc"
        assert rule_route("WHY did this happen?") == "answer_only"

    def test_unrecognised_message_returns_none(self):
        """Truly ambiguous messages must return None (escalate to LLM)."""
        assert rule_route("hello there") is None
        assert rule_route("great job") is None

    # Priority: command_edit beats answer_only (whole-word: "move" != "movement")
    def test_command_beats_explanation(self):
        """'explain then move M1 left' should be command_edit, not answer_only."""
        assert rule_route("explain then move M1 left") == "command_edit"

    # Priority: command_edit beats DRC
    def test_command_beats_drc(self):
        """'move M1 to fix DRC violation' must be command_edit."""
        assert rule_route("move M1 to fix DRC violation") == "command_edit"

    # DRC before routing
    def test_drc_before_routing(self):
        """'DRC routing violation' must be need_drc, not need_routing."""
        assert rule_route("DRC routing violation") == "need_drc"

    def test_delete_is_command_edit(self):
        assert rule_route("delete dummy device") == "command_edit"

    def test_align_is_command_edit(self):
        assert rule_route("align M1 and M2 to the same row") == "command_edit"

    def test_rotate_is_command_edit(self):
        assert rule_route("rotate M4 by 180 degrees") == "command_edit"

    def test_design_rule_violation_is_need_drc(self):
        assert rule_route("fix the design rule violation") == "need_drc"

    def test_interconnect_is_need_routing(self):
        assert rule_route("minimise interconnect length") == "need_routing"

    def test_wire_is_need_routing(self):
        assert rule_route("draw a wire from VDD to VDD") == "need_routing"

    def test_matching_is_need_strategy(self):
        assert rule_route("improve device matching") == "need_strategy"

    def test_diff_pair_is_need_topology(self):
        assert rule_route("show me the diff pair") == "need_topology"

    def test_tell_me_is_answer_only(self):
        assert rule_route("tell me what happened") == "answer_only"

    def test_summarize_is_answer_only(self):
        assert rule_route("summarize the placement result") == "answer_only"


# ---------------------------------------------------------------------------
# 4. normalize_route — all valid routes survive intact
# ---------------------------------------------------------------------------

class TestNormalizeRoute:

    @pytest.mark.parametrize("route", sorted(VALID_SESSION_ROUTES))
    def test_valid_routes_pass_through(self, route):
        assert normalize_route(route) == route

    def test_empty_string_becomes_clarify(self):
        assert normalize_route("") == "clarify"

    def test_whitespace_becomes_clarify(self):
        assert normalize_route("   ") == "clarify"

    def test_none_becomes_clarify(self):
        assert normalize_route(None) == "clarify"

    def test_llm_hallucination_becomes_clarify(self):
        assert normalize_route("topology") == "clarify"   # not a valid route name
        assert normalize_route("placement") == "clarify"


# ---------------------------------------------------------------------------
# 5. VALID_SESSION_ROUTES completeness
# ---------------------------------------------------------------------------

class TestValidSessionRoutes:

    def test_all_required_routes_present(self):
        required = {
            "answer_only", "command_edit",
            "need_topology", "need_strategy", "need_placement",
            "need_drc", "fix_drc", "need_routing", "clarify",
        }
        assert required <= VALID_SESSION_ROUTES

    def test_is_frozenset(self):
        assert isinstance(VALID_SESSION_ROUTES, frozenset)

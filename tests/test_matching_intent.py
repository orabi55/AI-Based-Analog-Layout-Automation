"""Tests for ai_agent.tools.matching_intent."""

from __future__ import annotations

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_agent.tools.matching_intent import (
    parse_matching_edit_intent,
    evaluate_matching_edit_intent,
)
from tests.fixtures.comparator_chat_state import (
    make_comparator_finger_state,
    make_comparator_chat_state,
)


# ---------------------------------------------------------------------------
# parse_matching_edit_intent
# ---------------------------------------------------------------------------

class TestParseMatchingEditIntent:

    def test_question_detected(self):
        intent = parse_matching_edit_intent(
            "Should I use common centroid for MM8 and MM9?"
        )
        assert intent["is_question"] is True
        assert intent["is_matching_edit"] is False
        assert intent["normalized_technique"] == "common_centroid"

    def test_command_detected(self):
        intent = parse_matching_edit_intent(
            "Make MM8 and MM9 interdigitated"
        )
        assert intent["is_matching_edit"] is True
        assert intent["is_question"] is False
        assert intent["normalized_technique"] == "interdigitated"
        assert "MM8" in intent["target_devices"]
        assert "MM9" in intent["target_devices"]

    def test_alias_input_pair(self):
        intent = parse_matching_edit_intent(
            "Apply common centroid to the input pair"
        )
        assert "MM8" in intent["target_devices"]
        assert "MM9" in intent["target_devices"]

    def test_ambiguous_latch_pair(self):
        intent = parse_matching_edit_intent(
            "Match the latch pair with interdigitation"
        )
        assert intent["ambiguous_alias"] == "latch pair"
        assert intent["target_devices"] == []

    def test_pmos_latch_disambiguated(self):
        intent = parse_matching_edit_intent(
            "Match the PMOS latch pair with interdigitation"
        )
        assert intent["ambiguous_alias"] is None
        assert "MM4" in intent["target_devices"]
        assert "MM5" in intent["target_devices"]

    def test_nmos_latch_disambiguated(self):
        intent = parse_matching_edit_intent(
            "Match the NMOS latch pair with interdigitation"
        )
        assert "MM6" in intent["target_devices"]
        assert "MM7" in intent["target_devices"]

    def test_no_technique_returns_empty(self):
        intent = parse_matching_edit_intent("move M1 left")
        assert intent["is_matching_edit"] is False
        assert intent["normalized_technique"] == ""


# ---------------------------------------------------------------------------
# evaluate_matching_edit_intent
# ---------------------------------------------------------------------------

class TestEvaluateMatchingEditIntent:

    @pytest.fixture
    def finger_state(self):
        return make_comparator_finger_state("test")

    def test_mm8_mm9_common_centroid_not_recommended(self, finger_state):
        intent = parse_matching_edit_intent(
            "Apply common centroid to MM8 and MM9", finger_state
        )
        result = evaluate_matching_edit_intent(intent, finger_state)
        assert result["layout_session_decision"] == "answer"
        assert "not recommended" in result["assistant_text"].lower() or \
               "no layout changes" in result["assistant_text"].lower()
        assert result["pending_cmds"] == []

    def test_mm8_mm9_already_interdigitated(self, finger_state):
        intent = parse_matching_edit_intent(
            "Make MM8 and MM9 interdigitated", finger_state
        )
        result = evaluate_matching_edit_intent(intent, finger_state)
        assert "already interdigitated" in result["assistant_text"].lower()
        assert result["pending_cmds"] == []

    def test_mm4_mm5_latch_pair(self, finger_state):
        intent = parse_matching_edit_intent(
            "Apply interdigitation to MM4 and MM5", finger_state
        )
        result = evaluate_matching_edit_intent(intent, finger_state)
        # Should warn that interdigitation is not typical for cross-coupled latch
        assert "cross-coupled" in result["assistant_text"].lower() or \
               "not" in result["assistant_text"].lower()
        assert result["pending_cmds"] == []

    def test_mm6_mm7_single_finger_not_applicable(self, finger_state):
        intent = parse_matching_edit_intent(
            "Make MM6 and MM7 interdigitated", finger_state
        )
        result = evaluate_matching_edit_intent(intent, finger_state)
        assert "not applicable" in result["assistant_text"].lower() or \
               "single-finger" in result["assistant_text"].lower()

    def test_ambiguous_alias_asks_clarification(self, finger_state):
        intent = parse_matching_edit_intent(
            "Match the latch pair with common centroid", finger_state
        )
        result = evaluate_matching_edit_intent(intent, finger_state)
        assert result["layout_session_decision"] == "clarify"
        assert "MM4" in result["assistant_text"] or "PMOS" in result["assistant_text"]

    def test_no_devices_asks_clarification(self, finger_state):
        intent = parse_matching_edit_intent(
            "Apply interdigitation", finger_state
        )
        result = evaluate_matching_edit_intent(intent, finger_state)
        assert result["layout_session_decision"] == "clarify"

    def test_no_commands_generated(self, finger_state):
        """All matching edit evaluations must produce no layout commands."""
        test_messages = [
            "Make MM8 and MM9 interdigitated",
            "Apply common centroid to MM0 and MM3",
            "Use symmetric matching for MM1 and MM2",
        ]
        for msg in test_messages:
            intent = parse_matching_edit_intent(msg, finger_state)
            result = evaluate_matching_edit_intent(intent, finger_state)
            assert result["pending_cmds"] == [], \
                f"Commands should be empty for: {msg}"

    def test_mm0_mm3_abab_already_satisfied(self, finger_state):
        """MM0/MM3 should be detected as already ABAB interdigitated."""
        intent = parse_matching_edit_intent(
            "Make MM0 and MM3 interdigitated", finger_state
        )
        result = evaluate_matching_edit_intent(intent, finger_state)
        assert "already" in result["assistant_text"].lower() or \
               "abab" in result["assistant_text"].lower()

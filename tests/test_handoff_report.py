"""
Tests for ai_agent/core/handoff_report.py

Required coverage (per spec):
  - Heuristic PDK rule always produces low confidence
  - needs_human_review populated when any rule is null
  - Report serializes cleanly to JSON (no non-serializable fields)
"""

import os
import sys
import json

_root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

import pytest
from ai_agent.core.handoff_report import (
    generate_handoff_report,
    render_handoff_report_html,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _node(nid, ntype, x=0.0, y=0.0, w=0.294, h=0.568):
    return {
        "id": nid, "type": ntype,
        "geometry": {"x": x, "y": y, "width": w, "height": h},
    }


@pytest.fixture
def two_node_state():
    return {
        "placement_nodes": [
            _node("M1", "nmos", 0.0,   0.0),
            _node("M2", "pmos", 0.294, 0.668),
        ],
        "drc_pass":      True,
        "drc_flags":     [],
        "groups":        [{"id": "G1", "type": "diff_pair",
                           "devices": ["M1", "M2"],
                           "matching": True, "placement_style": "ABBA"}],
        "terminal_nets": {},
        "pdk_name":      "saed14nm",
    }


@pytest.fixture
def fully_confirmed_pdk():
    """All rules explicitly provided in the PDK dict."""
    return {
        "fin_pitch_um":        0.014,
        "tap_max_distance_um": 2.5,
        "endcap_cell_names":   ["MY_ENDCAP"],
        "endcap_width_um":     0.294,
    }


# ---------------------------------------------------------------------------
# 1. Spec requirement: heuristic rule always low confidence
# ---------------------------------------------------------------------------

class TestHeuristicConfidence:
    def test_heuristic_source_yields_low_confidence(self, two_node_state):
        # Empty PDK → tap_max_distance_um is yield-critical → heuristic fallback
        report = generate_handoff_report(two_node_state, {})
        tap = next(d for d in report["confidence_per_decision"]
                   if d["decision"] == "Tap cell spacing")
        assert tap["rule_source"] == "heuristic"
        assert tap["confidence"]  == "low"

    def test_explicit_pdk_overrides_heuristic(self, two_node_state, fully_confirmed_pdk):
        report = generate_handoff_report(two_node_state, fully_confirmed_pdk)
        tap = next(d for d in report["confidence_per_decision"]
                   if d["decision"] == "Tap cell spacing")
        assert tap["rule_source"] == "confirmed_pdk"
        assert tap["confidence"]  == "high"

    def test_literature_prior_yields_medium_confidence(self, two_node_state):
        # fin_pitch_um is in _SAED14_DEFAULTS but NOT in _YIELD_CRITICAL
        # → falls back as literature_prior → medium confidence
        report = generate_handoff_report(two_node_state, {})
        fin = next(d for d in report["confidence_per_decision"]
                   if d["decision"] == "Fin grid snap")
        assert fin["rule_source"] == "literature_prior"
        assert fin["confidence"]  == "medium"

    def test_pdk_explicit_value_is_high_even_if_same_as_default(self, two_node_state):
        # Same value as the heuristic, but explicitly provided → confirmed_pdk → high
        pdk = {"tap_max_distance_um": 2.5}
        report = generate_handoff_report(two_node_state, pdk)
        tap = next(d for d in report["confidence_per_decision"]
                   if d["decision"] == "Tap cell spacing")
        assert tap["rule_source"] == "confirmed_pdk"
        assert tap["confidence"]  == "high"

    def test_nested_pdk_lookup_recognised_as_confirmed(self, two_node_state):
        pdk = {"drc_rules": {"fin_pitch_um": 0.020}}
        report = generate_handoff_report(two_node_state, pdk)
        fin = next(d for d in report["confidence_per_decision"]
                   if d["decision"] == "Fin grid snap")
        assert fin["rule_source"] == "confirmed_pdk"
        assert fin["confidence"]  == "high"


# ---------------------------------------------------------------------------
# 2. Spec requirement: needs_human_review populated on null rules
# ---------------------------------------------------------------------------

class TestNeedsHumanReview:
    def test_null_pdk_value_appears_in_review(self, two_node_state):
        pdk = {"tap_max_distance_um": None}
        report = generate_handoff_report(two_node_state, pdk)
        # The tap decision should be low confidence due to null
        tap = next(d for d in report["confidence_per_decision"]
                   if d["decision"] == "Tap cell spacing")
        assert tap["confidence"] == "low"
        # And appear in the review list
        assert any("Tap" in item for item in report["needs_human_review"])

    def test_null_value_low_confidence_regardless_of_source(self, two_node_state):
        # Top-level explicit None → confirmed_pdk source but null value → still low
        pdk = {"endcap_cell_names": None}
        report = generate_handoff_report(two_node_state, pdk)
        cap = next(d for d in report["confidence_per_decision"]
                   if d["decision"] == "Endcap cell name")
        assert cap["confidence"] == "low"

    def test_drc_failure_appears_first_in_review(self):
        state = {
            "placement_nodes": [_node("M1", "nmos")],
            "drc_pass":  False,
            "drc_flags": [{"kind": "OVERLAP", "dev_a": "M1", "dev_b": "M2"}],
        }
        report = generate_handoff_report(state, {})
        assert len(report["needs_human_review"]) > 0
        # DRC failure should appear at index 0
        assert "DRC violation" in report["needs_human_review"][0]

    def test_review_includes_every_low_confidence_decision(self, two_node_state):
        # Empty PDK → tap (heuristic, low) + others
        report = generate_handoff_report(two_node_state, {})
        low_decisions = [d["decision"] for d in report["confidence_per_decision"]
                         if d["confidence"] == "low"]
        for label in low_decisions:
            assert any(label in item for item in report["needs_human_review"]), (
                f"Low-confidence decision {label!r} not surfaced in needs_human_review"
            )

    def test_guard_ring_listed_as_unimplemented(self, two_node_state, fully_confirmed_pdk):
        report = generate_handoff_report(two_node_state, fully_confirmed_pdk)
        assert any("Guard ring" in item for item in report["needs_human_review"])

    def test_review_items_are_strings(self, two_node_state):
        report = generate_handoff_report(two_node_state, {})
        for item in report["needs_human_review"]:
            assert isinstance(item, str)
            assert len(item) > 0


# ---------------------------------------------------------------------------
# 3. Spec requirement: report serializes cleanly to JSON
# ---------------------------------------------------------------------------

class TestJsonSerializable:
    def test_round_trip_json(self, two_node_state):
        report = generate_handoff_report(two_node_state, {})
        s = json.dumps(report)
        loaded = json.loads(s)
        assert loaded == report

    def test_no_non_serializable_fields_with_empty_state(self):
        report = generate_handoff_report({}, {})
        json.dumps(report)  # must not raise

    def test_no_non_serializable_fields_with_full_pdk(self, two_node_state, fully_confirmed_pdk):
        report = generate_handoff_report(two_node_state, fully_confirmed_pdk)
        json.dumps(report)

    def test_report_indented_pretty_print_ok(self, two_node_state):
        report = generate_handoff_report(two_node_state, {})
        s = json.dumps(report, indent=2)
        assert "\n" in s

    def test_drc_fail_state_serializes(self):
        state = {
            "placement_nodes": [_node("M1", "nmos")],
            "drc_pass":  False,
            "drc_flags": [{"kind": "OVERLAP", "x1_a": 0.0, "x2_a": 0.294}],
        }
        report = generate_handoff_report(state, {})
        json.dumps(report)


# ---------------------------------------------------------------------------
# 4. Structural sanity (sections + types)
# ---------------------------------------------------------------------------

class TestStructure:
    _REQUIRED_SECTIONS = (
        "summary", "scores", "confidence_per_decision",
        "needs_human_review", "suggested_next_actions",
    )

    def test_all_five_sections_present(self, two_node_state):
        report = generate_handoff_report(two_node_state, {})
        for section in self._REQUIRED_SECTIONS:
            assert section in report, f"Missing section: {section}"

    def test_summary_has_required_fields(self, two_node_state):
        report = generate_handoff_report(two_node_state, {})
        s = report["summary"]
        for field in ("total_devices", "total_groups", "circuit_type",
                      "pdk_name", "drc_pass"):
            assert field in s
        assert isinstance(s["total_devices"], int)
        assert isinstance(s["total_groups"],  int)
        assert isinstance(s["drc_pass"],      bool)

    def test_summary_counts_correct(self, two_node_state):
        report = generate_handoff_report(two_node_state, {})
        assert report["summary"]["total_devices"] == 2
        assert report["summary"]["total_groups"]  == 1

    def test_scores_has_required_fields(self, two_node_state):
        report = generate_handoff_report(two_node_state, {})
        sc = report["scores"]
        for field in ("symmetry", "interdigitation", "area_utilization",
                      "signal_flow_cost", "drc_pass"):
            assert field in sc

    def test_decisions_have_required_keys(self, two_node_state):
        report = generate_handoff_report(two_node_state, {})
        for d in report["confidence_per_decision"]:
            assert set(d.keys()) >= {"decision", "confidence", "reason", "rule_source"}
            assert d["confidence"]  in ("high", "medium", "low")
            assert d["rule_source"] in ("confirmed_pdk", "literature_prior", "heuristic")

    def test_needs_review_is_list_of_strings(self, two_node_state):
        report = generate_handoff_report(two_node_state, {})
        assert isinstance(report["needs_human_review"], list)

    def test_suggested_actions_is_list_of_strings(self, two_node_state):
        report = generate_handoff_report(two_node_state, {})
        assert isinstance(report["suggested_next_actions"], list)
        for a in report["suggested_next_actions"]:
            assert isinstance(a, str) and len(a) > 0

    def test_check_overlaps_always_suggested(self, two_node_state):
        report = generate_handoff_report(two_node_state, {})
        assert any("check_overlaps" in a for a in report["suggested_next_actions"])

    def test_legalizer_suggested_only_when_drc_fails(self):
        ok = generate_handoff_report({"placement_nodes": [], "drc_pass": True}, {})
        bad = generate_handoff_report({"placement_nodes": [], "drc_pass": False,
                                       "drc_flags": [{"kind": "OVERLAP"}]}, {})
        assert not any("run_legalizer" in a for a in ok["suggested_next_actions"])
        assert     any("run_legalizer" in a for a in bad["suggested_next_actions"])


# ---------------------------------------------------------------------------
# 5. HTML rendering
# ---------------------------------------------------------------------------

class TestHtmlRendering:
    def test_returns_string(self, two_node_state):
        report = generate_handoff_report(two_node_state, {})
        html = render_handoff_report_html(report)
        assert isinstance(html, str)
        assert len(html) > 0

    def test_contains_section_titles(self, two_node_state):
        report = generate_handoff_report(two_node_state, {})
        html = render_handoff_report_html(report)
        assert "Handoff Report" in html
        assert "Scores"          in html
        assert "Decisions"       in html
        assert "Next Actions"    in html

    def test_uses_color_for_low_confidence(self, two_node_state):
        # Force at least one low-confidence decision
        report = generate_handoff_report(two_node_state, {})
        html = render_handoff_report_html(report)
        # Red for low confidence
        assert "#e25b5b" in html

    def test_uses_color_for_high_confidence(self, two_node_state, fully_confirmed_pdk):
        report = generate_handoff_report(two_node_state, fully_confirmed_pdk)
        html = render_handoff_report_html(report)
        # Green for high confidence (drc_pass + confirmed pdk)
        assert "#4ec98e" in html

    def test_review_section_visually_prominent(self, two_node_state):
        report = generate_handoff_report(two_node_state, {})
        html = render_handoff_report_html(report)
        # Prominence cues: coloured left border + tinted background
        assert "border-left:4px" in html
        assert "Needs Human Review" in html

    def test_html_escapes_dangerous_input(self):
        """User-provided strings (circuit type etc.) must be escaped."""
        state = {
            "placement_nodes": [],
            "constraint_text": "CIRCUIT_TYPE: <script>alert(1)</script>",
        }
        report = generate_handoff_report(state, {})
        html = render_handoff_report_html(report)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# 6. Empty / edge-case states
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_state_does_not_raise(self):
        report = generate_handoff_report({}, {})
        assert report["summary"]["total_devices"] == 0
        assert isinstance(report["scores"], dict)

    def test_none_state_does_not_raise(self):
        report = generate_handoff_report(None, None)
        assert report["summary"]["total_devices"] == 0

    def test_circuit_type_inferred_from_constraint_text(self):
        state = {
            "placement_nodes": [],
            "constraint_text": "CIRCUIT_TYPE: 5T-OTA\nGROUPS:\n  diff_pair: M1, M2",
        }
        report = generate_handoff_report(state, {})
        assert report["summary"]["circuit_type"] == "5T-OTA"

    def test_circuit_type_falls_back_to_device_mix(self):
        state = {
            "placement_nodes": [
                _node("M1", "nmos"),
                _node("M2", "pmos"),
            ],
        }
        report = generate_handoff_report(state, {})
        assert "PMOS" in report["summary"]["circuit_type"]
        assert "NMOS" in report["summary"]["circuit_type"]

    def test_uses_nodes_when_placement_nodes_absent(self):
        state = {"nodes": [_node("M1", "nmos")]}
        report = generate_handoff_report(state, {})
        assert report["summary"]["total_devices"] == 1

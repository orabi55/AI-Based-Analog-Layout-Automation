"""
Tests for ai_agent/core/layout_state.py

Coverage:
- Round-trip save/load produces identical nodes
- Missing file returns {} (no exception)
- Non-serializable node raises ValueError naming the field
- state_exists() returns False on missing file
- Various edge cases: malformed JSON, sorted nodes, float rounding,
  pdk_name default, handoff_report optional, placement_nodes fallback,
  drc_flags → violations rename, clear_layout_state.
"""

import os
import sys
import json
from datetime import datetime

_root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

import pytest
from ai_agent.core.layout_state import (
    save_layout_state,
    load_layout_state,
    state_exists,
    clear_layout_state,
    PIPELINE_VERSION,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def state_path(tmp_path):
    """Return a unique JSON path under tmp_path."""
    return str(tmp_path / "layout_state.json")


@pytest.fixture
def basic_state():
    return {
        "nodes": [
            {"id": "M1", "type": "nmos",
             "geometry": {"x": 0.0,   "y": 0.0,   "width": 0.294, "height": 0.568}},
            {"id": "M2", "type": "pmos",
             "geometry": {"x": 0.294, "y": 0.668, "width": 0.294, "height": 0.568}},
        ],
        "terminal_nets": {"M1": {"D": "out", "G": "vin"}},
        "drc_flags":     [],
        "drc_pass":      True,
        "groups":        [{"id": "G1", "type": "diff_pair",
                            "devices": ["M1", "M2"],
                            "matching": True, "placement_style": "ABBA"}],
    }


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_round_trip_identical_nodes(self, state_path, basic_state):
        save_layout_state(basic_state, state_path)
        loaded = load_layout_state(state_path)
        # Every node from the source must appear unchanged in the loaded file
        for orig in basic_state["nodes"]:
            assert orig in loaded["nodes"], f"Node {orig['id']} not preserved"

    def test_round_trip_preserves_terminal_nets(self, state_path, basic_state):
        save_layout_state(basic_state, state_path)
        loaded = load_layout_state(state_path)
        assert loaded["terminal_nets"] == basic_state["terminal_nets"]

    def test_round_trip_preserves_groups(self, state_path, basic_state):
        save_layout_state(basic_state, state_path)
        loaded = load_layout_state(state_path)
        assert loaded["groups"] == basic_state["groups"]

    def test_round_trip_preserves_drc_pass(self, state_path, basic_state):
        save_layout_state(basic_state, state_path)
        loaded = load_layout_state(state_path)
        assert loaded["drc_pass"] is True

    def test_pipeline_version_in_payload(self, state_path, basic_state):
        save_layout_state(basic_state, state_path)
        loaded = load_layout_state(state_path)
        assert loaded["pipeline_version"] == PIPELINE_VERSION

    def test_saved_at_is_iso_format(self, state_path, basic_state):
        save_layout_state(basic_state, state_path)
        loaded = load_layout_state(state_path)
        assert "saved_at" in loaded
        # Parsing must succeed for a valid ISO 8601 timestamp
        datetime.fromisoformat(loaded["saved_at"].replace("Z", "+00:00"))

    def test_pdk_name_default_saed14nm(self, state_path, basic_state):
        save_layout_state(basic_state, state_path)
        loaded = load_layout_state(state_path)
        assert loaded["pdk_name"] == "saed14nm"

    def test_pdk_name_explicit(self, state_path):
        save_layout_state({"nodes": [], "pdk_name": "tsmc28"}, state_path)
        loaded = load_layout_state(state_path)
        assert loaded["pdk_name"] == "tsmc28"


# ---------------------------------------------------------------------------
# Missing / malformed file behaviour
# ---------------------------------------------------------------------------

class TestLoadFailureModes:
    def test_missing_file_returns_empty_dict(self, tmp_path):
        path = str(tmp_path / "does_not_exist.json")
        assert load_layout_state(path) == {}

    def test_missing_file_does_not_raise(self, tmp_path):
        path = str(tmp_path / "ghost.json")
        # Must not raise
        result = load_layout_state(path)
        assert isinstance(result, dict)

    def test_malformed_json_returns_empty(self, state_path):
        with open(state_path, "w", encoding="utf-8") as fh:
            fh.write("{{{ not valid json !!!")
        assert load_layout_state(state_path) == {}

    def test_top_level_list_returns_empty(self, state_path):
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(["not", "a", "dict"], fh)
        assert load_layout_state(state_path) == {}

    def test_empty_file_returns_empty(self, state_path):
        open(state_path, "w").close()
        assert load_layout_state(state_path) == {}


# ---------------------------------------------------------------------------
# Non-serializable nodes
# ---------------------------------------------------------------------------

class TestSerializationGuard:
    def test_non_serializable_field_raises_value_error(self, state_path):
        bad = {"nodes": [{"id": "M1", "type": "nmos", "garbage_field": object()}]}
        with pytest.raises(ValueError) as exc:
            save_layout_state(bad, state_path)
        assert "M1" in str(exc.value)
        assert "garbage_field" in str(exc.value)

    def test_error_names_correct_field(self, state_path):
        bad = {"nodes": [{"id": "MM5", "type": "nmos",
                          "geometry": {"x": 0.0}, "broken": set([1, 2])}]}
        with pytest.raises(ValueError) as exc:
            save_layout_state(bad, state_path)
        assert "MM5" in str(exc.value)
        assert "broken" in str(exc.value)

    def test_error_names_correct_node_id(self, state_path):
        # Two valid nodes followed by a broken one — the error must blame node 3
        bad = {"nodes": [
            {"id": "M1", "type": "nmos"},
            {"id": "M2", "type": "nmos"},
            {"id": "M3", "type": "nmos", "callback": lambda x: x},
        ]}
        with pytest.raises(ValueError) as exc:
            save_layout_state(bad, state_path)
        assert "M3" in str(exc.value)
        assert "callback" in str(exc.value)

    def test_non_dict_node_raises(self, state_path):
        with pytest.raises(ValueError):
            save_layout_state({"nodes": ["this is not a dict"]}, state_path)

    def test_no_file_written_on_failure(self, state_path):
        bad = {"nodes": [{"id": "M1", "junk": object()}]}
        with pytest.raises(ValueError):
            save_layout_state(bad, state_path)
        # The save should have failed before opening the file for write
        assert not os.path.exists(state_path)


# ---------------------------------------------------------------------------
# state_exists
# ---------------------------------------------------------------------------

class TestStateExists:
    def test_false_on_missing_file(self, tmp_path):
        assert state_exists(str(tmp_path / "no.json")) is False

    def test_false_on_empty_file(self, state_path):
        open(state_path, "w").close()
        assert state_exists(state_path) is False

    def test_true_after_save(self, state_path, basic_state):
        save_layout_state(basic_state, state_path)
        assert state_exists(state_path) is True


# ---------------------------------------------------------------------------
# clear_layout_state
# ---------------------------------------------------------------------------

class TestClear:
    def test_deletes_existing_file(self, state_path, basic_state):
        save_layout_state(basic_state, state_path)
        assert os.path.exists(state_path)
        clear_layout_state(state_path)
        assert not os.path.exists(state_path)

    def test_noop_on_missing_file(self, tmp_path):
        path = str(tmp_path / "ghost.json")
        # Must not raise
        clear_layout_state(path)
        assert not os.path.exists(path)


# ---------------------------------------------------------------------------
# Sorting & float rounding
# ---------------------------------------------------------------------------

class TestFormatting:
    def test_nodes_sorted_by_id(self, state_path):
        state = {
            "nodes": [
                {"id": "MM3", "type": "nmos"},
                {"id": "MM1", "type": "nmos"},
                {"id": "MM2", "type": "nmos"},
            ]
        }
        save_layout_state(state, state_path)
        loaded = load_layout_state(state_path)
        ids = [n["id"] for n in loaded["nodes"]]
        assert ids == ["MM1", "MM2", "MM3"]

    def test_floats_rounded_to_six_decimals(self, state_path):
        state = {
            "nodes": [
                {"id": "M1", "geometry": {"x": 0.123456789012345, "y": 0.987654321098}}
            ]
        }
        save_layout_state(state, state_path)
        loaded = load_layout_state(state_path)
        assert loaded["nodes"][0]["geometry"]["x"] == round(0.123456789012345, 6)
        assert loaded["nodes"][0]["geometry"]["y"] == round(0.987654321098,   6)

    def test_nested_floats_rounded(self, state_path):
        state = {"nodes": [], "terminal_nets": {"M1": {"x": 1.234567890123}}}
        save_layout_state(state, state_path)
        loaded = load_layout_state(state_path)
        assert loaded["terminal_nets"]["M1"]["x"] == round(1.234567890123, 6)

    def test_output_is_pretty_printed(self, state_path, basic_state):
        save_layout_state(basic_state, state_path)
        with open(state_path, encoding="utf-8") as fh:
            text = fh.read()
        # Pretty-printed JSON has newlines and indentation
        assert "\n" in text
        assert "  " in text  # at least one indent


# ---------------------------------------------------------------------------
# Optional handoff_report and field fallbacks
# ---------------------------------------------------------------------------

class TestOptionalFields:
    def test_handoff_report_included_when_present(self, state_path):
        state = {"nodes": [], "handoff_report": {"summary": "ok", "score": 0.95}}
        save_layout_state(state, state_path)
        loaded = load_layout_state(state_path)
        assert loaded["handoff_report"] == {"summary": "ok", "score": 0.95}

    def test_handoff_report_omitted_when_absent(self, state_path):
        save_layout_state({"nodes": []}, state_path)
        loaded = load_layout_state(state_path)
        assert "handoff_report" not in loaded

    def test_placement_nodes_used_when_nodes_absent(self, state_path):
        state = {"placement_nodes": [{"id": "M1", "type": "nmos"}]}
        save_layout_state(state, state_path)
        loaded = load_layout_state(state_path)
        assert len(loaded["nodes"]) == 1
        assert loaded["nodes"][0]["id"] == "M1"

    def test_drc_flags_renamed_to_violations(self, state_path):
        state = {"nodes": [], "drc_flags": [{"kind": "OVERLAP", "dev_a": "M1"}]}
        save_layout_state(state, state_path)
        loaded = load_layout_state(state_path)
        assert loaded["violations"] == [{"kind": "OVERLAP", "dev_a": "M1"}]

    def test_explicit_violations_takes_precedence_over_drc_flags(self, state_path):
        state = {
            "nodes": [],
            "violations": [{"explicit": True}],
            "drc_flags":   [{"explicit": False}],
        }
        save_layout_state(state, state_path)
        loaded = load_layout_state(state_path)
        assert loaded["violations"] == [{"explicit": True}]

    def test_drc_pass_default_false(self, state_path):
        save_layout_state({"nodes": []}, state_path)
        loaded = load_layout_state(state_path)
        assert loaded["drc_pass"] is False

    def test_groups_default_empty_list(self, state_path):
        save_layout_state({"nodes": []}, state_path)
        loaded = load_layout_state(state_path)
        assert loaded["groups"] == []

    def test_terminal_nets_default_empty_dict(self, state_path):
        save_layout_state({"nodes": []}, state_path)
        loaded = load_layout_state(state_path)
        assert loaded["terminal_nets"] == {}


# ---------------------------------------------------------------------------
# Default-path / no-arg overloads
# ---------------------------------------------------------------------------

class TestDefaultPath:
    def test_default_path_round_trip(self, tmp_path, monkeypatch):
        # cd into tmp_path so the default "layout_state.json" lands there
        monkeypatch.chdir(tmp_path)
        save_layout_state({"nodes": [{"id": "X1", "type": "nmos"}]})
        assert state_exists()
        loaded = load_layout_state()
        assert loaded["nodes"][0]["id"] == "X1"
        clear_layout_state()
        assert not state_exists()

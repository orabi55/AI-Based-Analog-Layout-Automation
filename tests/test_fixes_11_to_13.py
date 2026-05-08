"""
test_fixes_11_to_13.py
======================
Tests for:
  Fix 11 — Extended command validator (list-device, finger, row, matching).
  Fix 12 — answer_from_initial_trace device-specific answers.
  Fix 13 — Single source of truth for session route constants.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

# Direct imports bypassing ai_agent.nodes.__init__ (which pulls in langchain).
_PROJ = Path(__file__).resolve().parents[1]

def _import_module(dotted_name: str, rel_path: str):
    """Import a module directly from its file path."""
    full = _PROJ / rel_path
    spec = importlib.util.spec_from_file_location(dotted_name, full)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(dotted_name, mod)
    spec.loader.exec_module(mod)
    return mod

_cv = _import_module(
    "ai_agent.nodes.command_validator",
    "ai_agent/nodes/command_validator.py",
)
node_command_validator = _cv.node_command_validator
_extract_device_ids   = _cv._extract_device_ids
_check_finger_integrity = _cv._check_finger_integrity
_check_row_legality     = _cv._check_row_legality
_validate_move_pair     = _cv._validate_move_pair
_detect_symmetry_warning = _cv._detect_symmetry_warning

from ai_agent.agents.session_chat_agent import (
    VALID_SESSION_ROUTES,
    SPECIALIST_BY_ROUTE,
    answer_from_initial_trace,
    run_session_chat_agent,
)


# ══════════════════════════════════════════════════════════════════
# Fix 11 — Extended command validator
# ══════════════════════════════════════════════════════════════════

class TestExtractDeviceIds:
    """_extract_device_ids must handle list-device keys."""

    def test_scalar_device_id(self):
        ids = _extract_device_ids({"device_id": "M1"})
        assert "M1" in ids

    def test_devices_list(self):
        ids = _extract_device_ids({"devices": ["M1", "M2"]})
        assert "M1" in ids
        assert "M2" in ids

    def test_devices_dict_list(self):
        ids = _extract_device_ids({"devices": [{"id": "M1"}, {"id": "M2"}]})
        assert "M1" in ids
        assert "M2" in ids

    def test_device_ids_key(self):
        ids = _extract_device_ids({"device_ids": ["M3", "M4"]})
        assert "M3" in ids
        assert "M4" in ids

    def test_targets_key(self):
        ids = _extract_device_ids({"targets": ["M5"]})
        assert "M5" in ids

    def test_devices_string(self):
        ids = _extract_device_ids({"devices": "M7"})
        assert "M7" in ids

    def test_mixed_scalar_and_list(self):
        ids = _extract_device_ids({"device_id": "M1", "devices": ["M2", "M3"]})
        assert set(ids) == {"M1", "M2", "M3"}


class TestValidatorRejectsUnknownDeviceInList:
    """Unknown devices in list fields must be rejected."""

    def test_unknown_device_in_devices_list(self):
        state = {
            "pending_cmds": [
                {"action": "move_pair", "devices": ["M1", "M9"], "dx": 1, "dy": 0}
            ],
            "placement_nodes": [{"id": "M1"}],
        }
        result = node_command_validator(state)
        assert result["pending_cmds"] == []
        assert "M9" in str(result["validation_errors"])

    def test_accepts_known_devices_in_list(self):
        state = {
            "pending_cmds": [
                {"action": "move_pair", "devices": ["M1", "M2"], "dx": 1, "dy": 0}
            ],
            "placement_nodes": [{"id": "M1"}, {"id": "M2"}],
        }
        result = node_command_validator(state)
        assert len(result["pending_cmds"]) == 1


class TestValidateMovePair:
    """move_pair must have ≥2 devices and dx/dy or x/y."""

    def test_move_pair_too_few_devices(self):
        err = _validate_move_pair({"action": "move_pair", "device_id": "M1", "dx": 1})
        assert err is not None
        assert "two devices" in err.lower()

    def test_move_pair_missing_delta(self):
        err = _validate_move_pair({"action": "move_pair", "devices": ["M1", "M2"]})
        assert err is not None
        assert "dx/dy" in err

    def test_move_pair_valid(self):
        err = _validate_move_pair(
            {"action": "move_pair", "devices": ["M1", "M2"], "dx": 1, "dy": 0}
        )
        assert err is None

    def test_non_move_pair_skipped(self):
        err = _validate_move_pair({"action": "move", "device_id": "M1"})
        assert err is None


class TestFingerIntegrity:
    """Partial finger edits must be blocked."""

    def test_blocks_finger_by_pattern(self):
        err = _check_finger_integrity(
            {"device_id": "M1_f0"},
            [{"id": "M1_f0"}],
        )
        assert err is not None
        assert "finger" in err.lower()

    def test_blocks_finger_by_bracket_pattern(self):
        err = _check_finger_integrity(
            {"device_id": "M1[0]"},
            [{"id": "M1[0]"}],
        )
        assert err is not None

    def test_blocks_finger_by_metadata(self):
        err = _check_finger_integrity(
            {"device_id": "M1_a"},
            [{"id": "M1_a", "parent_id": "M1", "finger_index": 0}],
        )
        assert err is not None
        assert "parent" in err.lower()

    def test_allows_with_opt_in(self):
        err = _check_finger_integrity(
            {"device_id": "M1_f0", "allow_finger_edit": True},
            [{"id": "M1_f0"}],
        )
        assert err is None

    def test_normal_device_passes(self):
        err = _check_finger_integrity(
            {"device_id": "M1"},
            [{"id": "M1"}],
        )
        assert err is None

    def test_validator_blocks_partial_finger_edit(self):
        state = {
            "pending_cmds": [
                {"action": "move", "device_id": "M1_f0", "dx": 1, "dy": 0}
            ],
            "placement_nodes": [
                {"id": "M1_f0", "parent_id": "M1", "finger_index": 0}
            ],
        }
        result = node_command_validator(state)
        assert result["pending_cmds"] == []


class TestRowLegality:
    """Absolute-y moves crossing row boundary must warn."""

    def test_warns_on_row_crossing(self):
        warn = _check_row_legality(
            {"action": "move", "device_id": "M1", "y": -5},
            [{"id": "M1", "type": "pmos", "y": 10}],
        )
        assert warn is not None
        assert "row boundary" in warn.lower()

    def test_no_warn_relative_move(self):
        warn = _check_row_legality(
            {"action": "move", "device_id": "M1", "dy": -1},
            [{"id": "M1", "type": "pmos", "y": 10}],
        )
        assert warn is None

    def test_no_warn_with_force_y(self):
        warn = _check_row_legality(
            {"action": "move", "device_id": "M1", "y": -5, "force_y": True},
            [{"id": "M1", "type": "pmos", "y": 10}],
        )
        assert warn is None

    def test_row_warning_is_non_blocking(self):
        """Row legality generates a warning, not an error."""
        state = {
            "pending_cmds": [
                {"action": "move", "device_id": "M1", "y": -5}
            ],
            "placement_nodes": [{"id": "M1", "type": "pmos", "y": 10}],
        }
        result = node_command_validator(state)
        # Command should still pass (warning, not error)
        assert len(result["pending_cmds"]) == 1
        assert result["validation_warnings"]


class TestSymmetryWarningStructured:
    """Structured matching group detection (Fix 11)."""

    def test_warns_single_device_in_matched_group(self):
        state = {
            "pending_cmds": [
                {"action": "move", "device_id": "M1", "dx": 1, "dy": 0}
            ],
            "placement_nodes": [{"id": "M1"}, {"id": "M2"}],
            "initial_agent_trace": {
                "strategy": {"matching_groups": [["M1", "M2"]]}
            },
        }
        result = node_command_validator(state)
        assert result["validation_warnings"]
        assert "M2" in str(result["validation_warnings"])

    def test_no_warn_when_both_devices_edited(self):
        warn = _detect_symmetry_warning(
            {"devices": ["M1", "M2"], "action": "move_pair"},
            {"strategy": {"matching_groups": [["M1", "M2"]]}},
        )
        assert warn is None

    def test_supports_matched_pairs_key(self):
        warn = _detect_symmetry_warning(
            {"device_id": "M1"},
            {"strategy": {"matched_pairs": [["M1", "M2"]]}},
        )
        assert warn is not None


# ══════════════════════════════════════════════════════════════════
# Fix 12 — answer_from_initial_trace
# ══════════════════════════════════════════════════════════════════

class TestAnswerFromTrace:
    """answer_from_initial_trace must provide useful device-specific answers."""

    def test_device_specific_with_matching(self):
        trace = {
            "strategy": {
                "matching_groups": [["M1", "M2"]],
                "symmetry_axis": "vertical",
            },
            "drc": {"pass": True, "flags": []},
        }
        text = answer_from_initial_trace("why did you place M1 here?", trace, [])
        assert "M1" in text
        assert "M2" in text
        assert "symmetr" in text.lower() or "matching" in text.lower() or "matched" in text.lower()

    def test_empty_trace_returns_fallback(self):
        text = answer_from_initial_trace("explain placement", {}, [])
        assert text
        assert "trace" in text.lower() or "current layout" in text.lower()

    def test_general_summary_when_no_device_mentioned(self):
        trace = {
            "strategy": {"matching_groups": [["M1", "M2"]]},
            "drc": {"pass": True, "flags": []},
        }
        text = answer_from_initial_trace("explain initial placement", trace, [])
        assert "placement" in text.lower() or "agents" in text.lower()
        assert "DRC" in text or "PASS" in text

    def test_drc_fail_mentioned(self):
        trace = {
            "drc": {"pass": False, "flags": [{"type": "overlap"}]},
        }
        text = answer_from_initial_trace("why did you place M1 here?", trace, [])
        assert "FAIL" in text or "violation" in text.lower()

    def test_with_placement_coordinates(self):
        trace = {
            "strategy": {"matching_groups": [["M1", "M2"]]},
            "drc": {"pass": True, "flags": []},
            "placement": {
                "placement_nodes": [
                    {"id": "M1", "x": 5.0, "y": 10.0},
                ]
            },
        }
        text = answer_from_initial_trace(
            "why did you place M1 here?", trace,
            [{"id": "M1"}, {"id": "M2"}],
        )
        assert "5.0" in text or "(5" in text


# ══════════════════════════════════════════════════════════════════
# Fix 13 — Single source of truth for route constants
# ══════════════════════════════════════════════════════════════════

class TestSingleSourceOfTruth:
    """Route constants must be consistent across all modules."""

    def test_session_route_labels_cover_all_routes(self):
        """SESSION_ROUTE_LABELS must have an entry for every route."""
        from ai_agent.llm.workers import SESSION_ROUTE_LABELS
        missing = set(VALID_SESSION_ROUTES) - set(SESSION_ROUTE_LABELS)
        assert not missing, f"Routes missing from SESSION_ROUTE_LABELS: {missing}"

    def test_specialist_by_route_keys_are_valid(self):
        """Every key in SPECIALIST_BY_ROUTE must be a valid route."""
        invalid = set(SPECIALIST_BY_ROUTE) - set(VALID_SESSION_ROUTES)
        assert not invalid, f"Invalid routes in SPECIALIST_BY_ROUTE: {invalid}"

    def test_session_route_map_covers_all_routes(self):
        """_SESSION_ROUTE_MAP in edges.py must cover every valid route."""
        import importlib, importlib.util
        from pathlib import Path

        mod_path = Path(__file__).resolve().parents[1] / "ai_agent" / "graph" / "edges.py"
        spec = importlib.util.spec_from_file_location("edges", mod_path)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:
            pytest.skip("edges.py requires langgraph")

        route_map = getattr(mod, "_SESSION_ROUTE_MAP", {})
        missing = set(VALID_SESSION_ROUTES) - set(route_map)
        assert not missing, f"Routes missing from _SESSION_ROUTE_MAP: {missing}"

    def test_valid_session_routes_is_frozenset(self):
        assert isinstance(VALID_SESSION_ROUTES, frozenset)

    def test_specialist_by_route_does_not_include_non_specialist_routes(self):
        """answer_only, clarify, command_edit should NOT be in SPECIALIST_BY_ROUTE."""
        for non_specialist in ("answer_only", "clarify", "command_edit"):
            assert non_specialist not in SPECIALIST_BY_ROUTE

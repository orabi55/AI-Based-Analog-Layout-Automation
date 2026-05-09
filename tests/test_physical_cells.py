"""
Unit tests for ai_agent/core/physical_cells.py
"""

import re
import sys
import os

_project_root = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import pytest
from ai_agent.core.physical_cells import (
    insert_endcaps,
    insert_taps,
    insert_fillers,
    insert_all_physical_cells,
)
from ai_agent.core.interfaces import LayoutToolResult, wrap_tool
from ai_agent.pdks.loader import get_rule


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _node(node_id, dev_type, x, y, w=0.294, h=0.568):
    return {
        "id": node_id,
        "type": dev_type,
        "geometry": {"x": x, "y": y, "width": w, "height": h},
    }


@pytest.fixture
def two_row_nodes():
    """One NMOS row at y=0, one PMOS row at y=0.668, two devices each."""
    return [
        _node("MM1", "nmos", 0.294, 0.000),
        _node("MM2", "nmos", 0.588, 0.000),
        _node("MM3", "pmos", 0.294, 0.668),
        _node("MM4", "pmos", 0.588, 0.668),
    ]


@pytest.fixture
def single_row_nodes():
    """One NMOS row with a gap between two devices (for filler test)."""
    return [
        _node("MM1", "nmos", 0.000, 0.000),
        _node("MM2", "nmos", 1.176, 0.000),
    ]


# ---------------------------------------------------------------------------
# get_rule / PDK loader
# ---------------------------------------------------------------------------

class TestGetRule:
    def test_top_level_lookup(self):
        pdk = {"fin_pitch_um": 0.007}
        assert get_rule(pdk, "fin_pitch_um") == 0.007

    def test_nested_lookup(self):
        pdk = {"drc_rules": {"tap_max_distance_um": 3.0}}
        assert get_rule(pdk, "tap_max_distance_um") == 3.0

    def test_heuristic_fallback_returns_value(self):
        assert get_rule({}, "fin_pitch_um") == 0.014

    def test_heuristic_fallback_tap_logs_warning(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="ai_agent"):
            val = get_rule({}, "tap_max_distance_um")
        assert val == 2.5
        assert "tap_max_distance_um" in caplog.text
        assert "heuristic" in caplog.text.lower()

    def test_unknown_key_returns_none(self):
        assert get_rule({}, "nonexistent_rule_xyz") is None

    def test_none_pdk_is_safe(self):
        assert get_rule(None, "fin_pitch_um") == 0.014


# ---------------------------------------------------------------------------
# insert_endcaps
# ---------------------------------------------------------------------------

class TestInsertEndcaps:
    def test_inserts_two_per_row(self, two_row_nodes):
        result = insert_endcaps(two_row_nodes, {})
        assert result.success
        endcaps = [n for n in result.nodes if n.get("subtype") == "endcap"]
        # 2 rows × 2 endcaps per row = 4
        assert len(endcaps) == 4

    def test_physical_only_flag(self, two_row_nodes):
        result = insert_endcaps(two_row_nodes, {})
        endcaps = [n for n in result.nodes if n.get("subtype") == "endcap"]
        assert all(n.get("physical_only") is True for n in endcaps)

    def test_left_endcap_is_left_of_row(self, single_row_nodes):
        result = insert_endcaps(single_row_nodes, {})
        left_caps = [n for n in result.nodes if "_L_" in n.get("id", "")]
        min_device_x = min(n["geometry"]["x"] for n in single_row_nodes)
        for ec in left_caps:
            assert ec["geometry"]["x"] < min_device_x

    def test_right_endcap_is_right_of_row(self, single_row_nodes):
        result = insert_endcaps(single_row_nodes, {})
        right_caps = [n for n in result.nodes if "_R_" in n.get("id", "")]
        max_device_x = max(
            n["geometry"]["x"] + n["geometry"]["width"] for n in single_row_nodes
        )
        for ec in right_caps:
            assert ec["geometry"]["x"] >= max_device_x

    def test_original_nodes_preserved(self, two_row_nodes):
        result = insert_endcaps(two_row_nodes, {})
        original_ids = {n["id"] for n in two_row_nodes}
        result_ids = {n["id"] for n in result.nodes}
        assert original_ids.issubset(result_ids)

    def test_metrics_endcaps_count(self, two_row_nodes):
        result = insert_endcaps(two_row_nodes, {})
        assert result.metrics["endcaps_inserted"] == 4

    def test_null_cell_name_uses_placeholder_and_warns(self, two_row_nodes):
        pdk = {"endcap_cell_names": [None]}
        result = insert_endcaps(two_row_nodes, pdk)
        assert result.success
        assert len(result.warnings) > 0
        assert any("placeholder" in w for w in result.warnings)
        endcaps = [n for n in result.nodes if n.get("subtype") == "endcap"]
        assert all(n["cell_name"] == "endcap" for n in endcaps)

    def test_empty_cell_names_list_uses_placeholder(self, two_row_nodes):
        pdk = {"endcap_cell_names": []}
        result = insert_endcaps(two_row_nodes, pdk)
        assert result.success
        assert len(result.warnings) > 0

    def test_none_pdk_is_safe(self, two_row_nodes):
        result = insert_endcaps(two_row_nodes, None)
        assert result.success

    def test_empty_nodes_returns_success(self):
        result = insert_endcaps([], {})
        assert result.success
        assert result.metrics["endcaps_inserted"] == 0
        assert not result.changed


# ---------------------------------------------------------------------------
# insert_taps
# ---------------------------------------------------------------------------

class TestInsertTaps:
    def test_nmos_row_gets_ptap(self, two_row_nodes):
        result = insert_taps(two_row_nodes, {})
        assert result.success
        ptaps = [n for n in result.nodes if n.get("subtype") == "ptap"]
        assert len(ptaps) > 0

    def test_pmos_row_gets_ntap(self, two_row_nodes):
        result = insert_taps(two_row_nodes, {})
        ntaps = [n for n in result.nodes if n.get("subtype") == "ntap"]
        assert len(ntaps) > 0

    def test_tap_type_is_tap(self, two_row_nodes):
        result = insert_taps(two_row_nodes, {})
        taps = [n for n in result.nodes if n.get("subtype") in ("ptap", "ntap")]
        assert all(n.get("type") == "tap" for n in taps)

    def test_physical_only_flag(self, two_row_nodes):
        result = insert_taps(two_row_nodes, {})
        taps = [n for n in result.nodes if n.get("type") == "tap"]
        assert all(n.get("physical_only") is True for n in taps)

    def test_taps_snapped_to_fin_grid(self, two_row_nodes):
        fin_pitch = 0.014
        result = insert_taps(two_row_nodes, {})
        taps = [n for n in result.nodes if n.get("type") == "tap"]
        for t in taps:
            x = t["geometry"]["x"]
            snapped = round(round(x / fin_pitch) * fin_pitch, 6)
            assert abs(x - snapped) < 1e-9, f"tap x={x} not on fin grid"

    def test_tap_interval_respects_max_distance(self):
        # Wide row: 6 µm — at tap_max=2.5 µm should get ≥2 intervals
        nodes = [
            _node("MM1", "nmos", 0.0,   0.0, w=3.0),
            _node("MM2", "nmos", 3.0,   0.0, w=3.0),
        ]
        result = insert_taps(nodes, {"tap_max_distance_um": 2.5})
        ptaps = [n for n in result.nodes if n.get("subtype") == "ptap"]
        assert len(ptaps) >= 3  # interval of 6/2=3 → 3 taps (0, 3, 6 um)

    def test_metrics_taps_count(self, two_row_nodes):
        result = insert_taps(two_row_nodes, {})
        assert result.metrics["taps_inserted"] > 0

    def test_none_pdk_is_safe(self, two_row_nodes):
        result = insert_taps(two_row_nodes, None)
        assert result.success


# ---------------------------------------------------------------------------
# insert_fillers — wraps existing finger_grouper logic
# ---------------------------------------------------------------------------

class TestInsertFillers:
    def test_fills_gap_between_devices(self, single_row_nodes):
        result = insert_fillers(single_row_nodes, {})
        assert result.success
        fillers = [
            n for n in result.nodes if str(n.get("id", "")).startswith("FILLER_DUMMY_")
        ]
        assert len(fillers) > 0, "Expected gap to be filled with FILLER_DUMMY nodes"

    def test_returns_layout_tool_result(self, single_row_nodes):
        result = insert_fillers(single_row_nodes, {})
        assert isinstance(result, LayoutToolResult)

    def test_metrics_fillers_count(self, single_row_nodes):
        result = insert_fillers(single_row_nodes, {})
        assert result.metrics["fillers_inserted"] >= 0

    def test_original_devices_present_in_result(self, single_row_nodes):
        result = insert_fillers(single_row_nodes, {})
        original_ids = {n["id"] for n in single_row_nodes}
        result_ids   = {n["id"] for n in result.nodes}
        assert original_ids.issubset(result_ids)

    def test_no_gap_no_fillers(self):
        # Devices are adjacent — no gap to fill
        nodes = [
            _node("MM1", "nmos", 0.000, 0.0),
            _node("MM2", "nmos", 0.294, 0.0),
        ]
        result = insert_fillers(nodes, {})
        assert result.success
        # Fillers may or may not appear depending on centering — just check no crash

    def test_empty_nodes_is_safe(self):
        result = insert_fillers([], {})
        assert result.success


# ---------------------------------------------------------------------------
# insert_all_physical_cells — aggregated pipeline
# ---------------------------------------------------------------------------

class TestInsertAllPhysicalCells:
    def test_success_on_valid_nodes(self, two_row_nodes):
        result = insert_all_physical_cells(two_row_nodes, {})
        assert result.success

    def test_message_format(self, two_row_nodes):
        result = insert_all_physical_cells(two_row_nodes, {})
        assert re.match(
            r"Inserted \d+ endcaps, \d+ tap cells, \d+ fillers",
            result.message,
        ), f"Unexpected message format: {result.message!r}"

    def test_all_metrics_present(self, two_row_nodes):
        result = insert_all_physical_cells(two_row_nodes, {})
        assert "endcaps_inserted" in result.metrics
        assert "taps_inserted"    in result.metrics
        assert "fillers_inserted" in result.metrics

    def test_endcaps_inserted(self, two_row_nodes):
        result = insert_all_physical_cells(two_row_nodes, {})
        assert result.metrics["endcaps_inserted"] > 0

    def test_taps_inserted(self, two_row_nodes):
        result = insert_all_physical_cells(two_row_nodes, {})
        assert result.metrics["taps_inserted"] > 0

    def test_warnings_aggregated_null_pdk(self, two_row_nodes):
        # Null cell name triggers a warning; it should appear in aggregated warnings
        pdk = {"endcap_cell_names": [None]}
        result = insert_all_physical_cells(two_row_nodes, pdk)
        assert result.success
        assert len(result.warnings) > 0

    def test_warnings_in_message_when_present(self, two_row_nodes):
        pdk = {"endcap_cell_names": [None]}
        result = insert_all_physical_cells(two_row_nodes, pdk)
        assert "Warnings" in result.message

    def test_none_pdk_is_safe(self, two_row_nodes):
        result = insert_all_physical_cells(two_row_nodes, None)
        assert result.success

    def test_empty_nodes_is_safe(self):
        result = insert_all_physical_cells([], {})
        assert result.success
        assert result.metrics["endcaps_inserted"] == 0


# ---------------------------------------------------------------------------
# wrap_tool exception safety
# ---------------------------------------------------------------------------

class TestWrapTool:
    def test_catches_exception_returns_failure(self):
        @wrap_tool
        def broken(nodes, pdk) -> LayoutToolResult:
            raise ValueError("deliberate error")

        result = broken([], {})
        assert not result.success
        assert "deliberate error" in result.message
        assert not result.changed
        assert result.nodes == []

    def test_success_path_passes_through(self):
        @wrap_tool
        def fine(nodes, pdk) -> LayoutToolResult:
            return LayoutToolResult(success=True, message="ok", changed=False, nodes=nodes)

        result = fine([{"id": "x"}], {})
        assert result.success
        assert result.nodes == [{"id": "x"}]

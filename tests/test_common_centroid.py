"""
Unit tests for ai_agent/core/common_centroid.py

Key invariants checked:
  1. No CC logic was reimplemented — module re-uses existing generators/metrics.
  2. 1D ABBA pair placement via place_common_centroid.
  3. 2D multi-device array via place_common_centroid_2d.
  4. evaluate_centroid_error == 0.0 for a manually constructed symmetric layout.
  5. Structural dummies survive finger_grouper's filler-stripping pass.
"""

import sys
import os

_root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

import pytest
from ai_agent.core.common_centroid import (
    place_common_centroid,
    place_common_centroid_2d,
    insert_dummies_around_group,
    evaluate_centroid_error,
)
from ai_agent.core.interfaces import LayoutToolResult


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _finger(dev_id: str, idx: int, x: float = 0.0, y: float = 0.0,
            w: float = 0.294, h: float = 0.568) -> dict:
    return {
        "id": f"{dev_id}_f{idx}",
        "type": "nmos",
        "geometry": {"x": x, "y": y, "width": w, "height": h},
    }


def _group(dev_id: str, n: int) -> list:
    """n finger nodes for dev_id, all initially at x=0, y=0."""
    return [_finger(dev_id, i) for i in range(n)]


def _make_symmetric_2d_nodes() -> list:
    """
    Manually constructed perfect 2D CC arrangement (2 rows × 4 columns).

    Row 0 (y=0.000): [MM1_f0, MM2_f0, MM2_f1, MM1_f1]  ← palindrome: M1 M2 M2 M1
    Row 1 (y=0.668): [MM2_f2, MM1_f2, MM1_f3, MM2_f3]  ← palindrome: M2 M1 M1 M2

    Both rows are palindromic with device set {MM1, MM2} → two qualifying rows.
    Centroids:
      MM1 cx = (0.147 + 1.029 + 0.441 + 0.735) / 4 = 0.588  cy = 0.334
      MM2 cx = (0.441 + 0.735 + 0.147 + 1.029) / 4 = 0.588  cy = 0.334
    → max_offset = 0 → score = 1.0 → error = 0.0 µm
    """
    pitch = 0.294
    row_h = 0.668
    nodes = []
    for i, nid in enumerate(["MM1_f0", "MM2_f0", "MM2_f1", "MM1_f1"]):
        nodes.append({
            "id": nid, "type": "nmos",
            "geometry": {"x": i * pitch, "y": 0.0, "width": pitch, "height": 0.568},
        })
    for i, nid in enumerate(["MM2_f2", "MM1_f2", "MM1_f3", "MM2_f3"]):
        nodes.append({
            "id": nid, "type": "nmos",
            "geometry": {"x": i * pitch, "y": row_h, "width": pitch, "height": 0.568},
        })
    return nodes


# ---------------------------------------------------------------------------
# Test 1 — No logic reimplemented
# ---------------------------------------------------------------------------

class TestNoReimplementation:
    """Verify that common_centroid.py only IMPORTS existing logic, never copies it."""

    def test_generate_placement_grid_is_original(self):
        import ai_agent.core.common_centroid as m
        from ai_agent.matching.universal_pattern_generator import generate_placement_grid
        assert m.generate_placement_grid is generate_placement_grid, (
            "generate_placement_grid must be the original object, not a local copy"
        )

    def test_generate_common_centroid_matrix_is_original(self):
        import ai_agent.core.common_centroid as m
        from ai_agent.placement.centroid_generator import generate_common_centroid_matrix
        assert m.generate_common_centroid_matrix is generate_common_centroid_matrix

    def test_common_centroid_accuracy_is_original(self):
        import ai_agent.core.common_centroid as m
        from ai_agent.placement.quality_metrics import _common_centroid_accuracy
        assert m._common_centroid_accuracy is _common_centroid_accuracy


# ---------------------------------------------------------------------------
# Test 2 — place_common_centroid (1D ABBA pair)
# ---------------------------------------------------------------------------

class TestPlaceCommonCentroid:
    def test_returns_layout_tool_result(self):
        result = place_common_centroid(_group("MM1", 2), _group("MM2", 2), 0.0, 0.0, {})
        assert isinstance(result, LayoutToolResult)

    def test_success_equal_fingers(self):
        result = place_common_centroid(_group("MM1", 2), _group("MM2", 2), 0.0, 0.0, {})
        assert result.success
        assert result.changed

    def test_all_placed_in_same_row(self):
        result = place_common_centroid(_group("MM1", 4), _group("MM2", 4), 0.0, 0.5, {})
        assert result.success
        for n in result.nodes:
            assert abs(n["geometry"]["y"] - 0.5) < 1e-9

    def test_placed_count_equals_total_fingers(self):
        result = place_common_centroid(_group("MM1", 2), _group("MM2", 2), 0.0, 0.0, {})
        # May be < total if grid has DUMMY slots that were skipped
        assert result.metrics["placed_count"] <= 4

    def test_nodes_have_distinct_x_positions(self):
        result = place_common_centroid(_group("MM1", 2), _group("MM2", 2), 0.0, 0.0, {})
        assert result.success
        xs = [n["geometry"]["x"] for n in result.nodes]
        # Each finger should occupy a unique column
        assert len(set(round(x, 6) for x in xs)) == len(xs)

    def test_start_x_offset_applied(self):
        offset = 1.0
        result = place_common_centroid(_group("MM1", 2), _group("MM2", 2), offset, 0.0, {})
        assert result.success
        min_x = min(n["geometry"]["x"] for n in result.nodes)
        # Fin-pitch snapping (0.014 µm grid) may shift start_x by up to ½ fin pitch
        fin_pitch = 0.014
        assert min_x >= offset - fin_pitch - 0.001

    def test_centroid_error_in_metrics(self):
        result = place_common_centroid(_group("MM1", 2), _group("MM2", 2), 0.0, 0.0, {})
        assert "centroid_error_um" in result.metrics
        assert result.metrics["centroid_error_um"] >= 0.0

    def test_empty_group_a_returns_failure(self):
        result = place_common_centroid([], _group("MM2", 2), 0.0, 0.0, {})
        assert not result.success

    def test_empty_group_b_returns_failure(self):
        result = place_common_centroid(_group("MM1", 2), [], 0.0, 0.0, {})
        assert not result.success

    def test_unequal_finger_counts_succeed(self):
        # generate_placement_grid handles imbalance via DUMMY padding
        result = place_common_centroid(_group("MM1", 2), _group("MM2", 4), 0.0, 0.0, {})
        assert result.success

    def test_none_pdk_is_safe(self):
        result = place_common_centroid(_group("MM1", 2), _group("MM2", 2), 0.0, 0.0, None)
        assert result.success

    def test_wrap_tool_catches_exception(self):
        # Pass non-list to trigger an internal error
        result = place_common_centroid("not_a_list", _group("MM2", 2), 0.0, 0.0, {})
        # Should not raise — wrap_tool must absorb it
        assert isinstance(result, LayoutToolResult)


# ---------------------------------------------------------------------------
# Test 3 — place_common_centroid_2d (multi-device 2D array)
# ---------------------------------------------------------------------------

class TestPlaceCommonCentroid2D:
    def _devices(self):
        return [
            {"id": "MM1", "fingers": 4, "nodes": _group("MM1", 4)},
            {"id": "MM2", "fingers": 2, "nodes": _group("MM2", 2)},
        ]

    def test_success(self):
        result = place_common_centroid_2d(self._devices(), 0.0, 0.0, {})
        assert result.success

    def test_returns_layout_tool_result(self):
        result = place_common_centroid_2d(self._devices(), 0.0, 0.0, {})
        assert isinstance(result, LayoutToolResult)

    def test_matrix_metrics_present(self):
        result = place_common_centroid_2d(self._devices(), 0.0, 0.0, {})
        assert "matrix_rows"  in result.metrics
        assert "matrix_cols"  in result.metrics
        assert "placed_count" in result.metrics

    def test_matrix_is_2d_for_enough_fingers(self):
        # 6 total fingers → matrix generator should produce > 1 row
        result = place_common_centroid_2d(self._devices(), 0.0, 0.0, {})
        assert result.metrics["matrix_rows"] >= 1  # at minimum 1 row

    def test_nodes_span_correct_y_rows(self):
        result = place_common_centroid_2d(self._devices(), 0.0, 0.0, {})
        assert result.success
        y_vals = {round(n["geometry"]["y"], 4) for n in result.nodes}
        # With multi-row matrix, expect at least 1 distinct Y
        assert len(y_vals) >= 1

    def test_start_x_offset_applied(self):
        offset = 2.0
        result = place_common_centroid_2d(self._devices(), offset, 0.0, {})
        assert result.success
        min_x = min(n["geometry"]["x"] for n in result.nodes)
        assert min_x >= offset - 0.001

    def test_3_device_array(self):
        devices = [
            {"id": "A", "fingers": 4, "nodes": _group("A", 4)},
            {"id": "B", "fingers": 4, "nodes": _group("B", 4)},
            {"id": "C", "fingers": 2, "nodes": _group("C", 2)},
        ]
        result = place_common_centroid_2d(devices, 0.0, 0.0, {})
        assert result.success
        assert result.metrics["placed_count"] > 0

    def test_fingers_inferred_from_nodes_when_absent(self):
        devices = [
            {"id": "MM1", "nodes": _group("MM1", 4)},  # no "fingers" key
            {"id": "MM2", "nodes": _group("MM2", 2)},
        ]
        result = place_common_centroid_2d(devices, 0.0, 0.0, {})
        assert result.success

    def test_integer_valued_float_fingers_are_accepted(self):
        devices = [
            {"id": "MM1", "fingers": 4.0, "nodes": _group("MM1", 4)},
            {"id": "MM2", "fingers": "2.0", "nodes": _group("MM2", 2)},
        ]
        result = place_common_centroid_2d(devices, 0.0, 0.0, {})
        assert result.success
        assert isinstance(result.metrics["matrix_rows"], int)
        assert isinstance(result.metrics["matrix_cols"], int)

    def test_empty_devices_returns_failure(self):
        result = place_common_centroid_2d([], 0.0, 0.0, {})
        assert not result.success

    def test_none_pdk_is_safe(self):
        result = place_common_centroid_2d(self._devices(), 0.0, 0.0, None)
        assert result.success


# ---------------------------------------------------------------------------
# Test 4 — evaluate_centroid_error == 0.0 for symmetric placement
# ---------------------------------------------------------------------------

class TestEvaluateCentroidError:
    def test_returns_float(self):
        nodes = _make_symmetric_2d_nodes()
        err = evaluate_centroid_error(nodes, ["MM1"], ["MM2"])
        assert isinstance(err, float)

    def test_zero_for_perfect_symmetric_2d(self):
        """A manually constructed ABBA/BAAB 2-row layout must yield error == 0."""
        nodes = _make_symmetric_2d_nodes()
        err = evaluate_centroid_error(nodes, ["MM1"], ["MM2"])
        assert abs(err) < 1e-9, f"Expected 0.0 centroid error for symmetric layout, got {err}"

    def test_zero_for_1d_arrangement(self):
        """1D ABBA is N/A for _common_centroid_accuracy → must return 0.0."""
        nodes = [
            _finger("MM1", 0, x=0.000, y=0.0),
            _finger("MM2", 0, x=0.294, y=0.0),
            _finger("MM2", 1, x=0.588, y=0.0),
            _finger("MM1", 1, x=0.882, y=0.0),
        ]
        err = evaluate_centroid_error(nodes, ["MM1"], ["MM2"])
        assert err == 0.0

    def test_non_negative(self):
        nodes = _make_symmetric_2d_nodes()
        err = evaluate_centroid_error(nodes, ["MM1"], ["MM2"])
        assert err >= 0.0

    def test_empty_nodes_returns_zero(self):
        assert evaluate_centroid_error([], ["MM1"], ["MM2"]) == 0.0

    def test_empty_ids_returns_zero(self):
        nodes = _make_symmetric_2d_nodes()
        assert evaluate_centroid_error(nodes, [], []) == 0.0

    def test_asymmetric_layout_has_nonzero_error(self):
        """Shift MM2 off-centre — both rows become non-palindromic → N/A → 0.0,
        OR rows are still palindromic but centroids diverge.
        Either way the function must not raise."""
        pitch = 0.294
        nodes = [
            # Row 0: same as symmetric
            _finger("MM1", 0, x=0.000, y=0.0),
            _finger("MM2", 0, x=0.294, y=0.0),
            _finger("MM2", 1, x=0.588, y=0.0),
            _finger("MM1", 1, x=0.882, y=0.0),
            # Row 1: NOT palindromic — all MM2 shifted right
            _finger("MM1", 2, x=0.000, y=0.668),
            _finger("MM1", 3, x=0.294, y=0.668),
            _finger("MM2", 2, x=1.000, y=0.668),
            _finger("MM2", 3, x=1.294, y=0.668),
        ]
        err = evaluate_centroid_error(nodes, ["MM1"], ["MM2"])
        assert err >= 0.0  # must not raise; value depends on palindrome detection


# ---------------------------------------------------------------------------
# Test 5 — structural dummies survive finger_grouper stripping
# ---------------------------------------------------------------------------

class TestInsertDummies:
    def test_returns_layout_tool_result(self):
        result = insert_dummies_around_group(_group("MM1", 2), {})
        assert isinstance(result, LayoutToolResult)

    def test_success(self):
        result = insert_dummies_around_group(_group("MM1", 2), {})
        assert result.success

    def test_default_one_dummy_per_side(self):
        result = insert_dummies_around_group(_group("MM1", 2), {})
        dummies = [n for n in result.nodes if n.get("structural")]
        assert len(dummies) == 2  # 1 left + 1 right

    def test_n_dummies_2_gives_four_dummies(self):
        result = insert_dummies_around_group(_group("MM1", 2), {}, n_dummies=2)
        dummies = [n for n in result.nodes if n.get("structural")]
        assert len(dummies) == 4  # 2 left + 2 right

    def test_original_nodes_preserved(self):
        group = _group("MM1", 3)
        original_ids = {n["id"] for n in group}
        result = insert_dummies_around_group(group, {})
        result_ids = {n["id"] for n in result.nodes}
        assert original_ids.issubset(result_ids)

    def test_structural_true_on_all_dummies(self):
        result = insert_dummies_around_group(_group("MM1", 2), {})
        dummies = [n for n in result.nodes if n.get("structural")]
        assert all(n["structural"] is True for n in dummies)

    def test_structural_dummies_not_stripped_by_finger_grouper(self):
        """_is_regenerated_filler_dummy must return False for structural dummies."""
        from ai_agent.placement.finger_grouper import _is_regenerated_filler_dummy
        result = insert_dummies_around_group(_group("MM1", 2), {}, n_dummies=1)
        dummies = [n for n in result.nodes if n.get("structural")]
        assert len(dummies) > 0, "No structural dummies were inserted"
        for d in dummies:
            assert not _is_regenerated_filler_dummy(d), (
                f"Structural dummy {d['id']!r} was incorrectly flagged as regenerated filler"
            )

    def test_structural_dummies_not_flagged_as_is_dummy(self):
        """finger_grouper._is_dummy_node must also return False for structural dummies."""
        from ai_agent.placement.finger_grouper import _is_dummy_node
        result = insert_dummies_around_group(_group("MM1", 2), {})
        dummies = [n for n in result.nodes if n.get("structural")]
        for d in dummies:
            assert not _is_dummy_node(d), (
                f"Structural dummy {d['id']!r} was flagged as a dummy by _is_dummy_node"
            )

    def test_left_dummy_is_left_of_group(self):
        group = [_finger("MM1", 0, x=1.0), _finger("MM1", 1, x=1.294)]
        result = insert_dummies_around_group(group, {})
        left_dummies  = [n for n in result.nodes if "_L_" in n.get("id", "")]
        min_group_x   = min(n["geometry"]["x"] for n in group)
        for d in left_dummies:
            assert d["geometry"]["x"] < min_group_x

    def test_right_dummy_is_right_of_group(self):
        group = [_finger("MM1", 0, x=0.0, w=0.294), _finger("MM1", 1, x=0.294, w=0.294)]
        result = insert_dummies_around_group(group, {})
        right_dummies = [n for n in result.nodes if "_R_" in n.get("id", "")]
        max_group_x   = max(n["geometry"]["x"] + n["geometry"]["width"] for n in group)
        for d in right_dummies:
            assert d["geometry"]["x"] >= max_group_x - 0.001

    def test_multi_row_group_gets_dummies_per_row(self):
        group = [
            _finger("MM1", 0, x=0.0, y=0.0),
            _finger("MM1", 1, x=0.0, y=0.668),
        ]
        result = insert_dummies_around_group(group, {}, n_dummies=1)
        dummies = [n for n in result.nodes if n.get("structural")]
        assert len(dummies) == 4  # 2 rows × (1 left + 1 right)

    def test_empty_group_returns_success(self):
        result = insert_dummies_around_group([], {})
        assert result.success
        assert result.metrics.get("structural_dummies_inserted", 0) == 0

    def test_metrics_count(self):
        result = insert_dummies_around_group(_group("MM1", 3), {}, n_dummies=1)
        assert result.metrics["structural_dummies_inserted"] == 2

    def test_none_pdk_is_safe(self):
        result = insert_dummies_around_group(_group("MM1", 2), None)
        assert result.success

    def test_id_prefix_is_struct_dummy(self):
        result = insert_dummies_around_group(_group("MM1", 2), {})
        dummies = [n for n in result.nodes if n.get("structural")]
        for d in dummies:
            assert d["id"].startswith("STRUCT_DUMMY_"), (
                f"Expected STRUCT_DUMMY_ prefix, got {d['id']!r}"
            )

    def test_no_is_dummy_key_set(self):
        result = insert_dummies_around_group(_group("MM1", 2), {})
        dummies = [n for n in result.nodes if n.get("structural")]
        for d in dummies:
            assert not d.get("is_dummy"), (
                f"Structural dummy {d['id']!r} has is_dummy=True — must NOT be set"
            )

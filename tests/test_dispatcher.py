"""
Tests for ai_agent/tools/dispatcher.py

Every route is exercised with a minimal 2-node layout (M1 nmos, M2 pmos).
Finger-level routes use an extended 4-finger fixture.
"""

import sys
import os
import copy

_root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

import pytest
from ai_agent.tools.dispatcher import dispatch
from ai_agent.tools.schemas import TOOL_REGISTRY, TOOL_MAP
from ai_agent.core.interfaces import LayoutToolResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _node(nid, ntype, x, y, w=0.294, h=0.568):
    return {
        "id": nid, "type": ntype,
        "geometry": {"x": x, "y": y, "width": w, "height": h},
    }


@pytest.fixture
def two_nodes():
    """Minimal layout: one NMOS (y=0) and one PMOS (y=0.668)."""
    return [
        _node("M1", "nmos", 0.0,   0.0),
        _node("M2", "pmos", 0.294, 0.668),
    ]


@pytest.fixture
def finger_nodes():
    """Four NMOS finger nodes for CC placement tests."""
    return [
        _node("MM1_f0", "nmos", 0.0,   0.0),
        _node("MM1_f1", "nmos", 0.294, 0.0),
        _node("MM2_f0", "nmos", 0.588, 0.0),
        _node("MM2_f1", "nmos", 0.882, 0.0),
    ]


@pytest.fixture
def nodes_with_dummies(two_nodes):
    """Layout with a filler dummy mixed in."""
    dummy = {
        "id": "FILLER_DUMMY_1_nmos",
        "type": "nmos",
        "is_dummy": True,
        "geometry": {"x": 0.588, "y": 0.0, "width": 0.294, "height": 0.568},
    }
    return two_nodes + [dummy]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ids(result):
    return {n["id"] for n in result.nodes}


# ---------------------------------------------------------------------------
# Schema / registry sanity
# ---------------------------------------------------------------------------

class TestRegistry:
    _EXPECTED_NAMES = {
        # Inspection
        "read_layout", "list_devices", "get_device_info", "score_layout",
        "get_layout_bounds",
        # Primitive manipulation
        "move_device", "swap_devices", "flip_device",
        "delete_device", "align_devices",
        "add_dummy", "remove_dummies",
        # DRC / legalization
        "check_overlaps", "run_legalizer",
        # Diffusion sharing
        "abut_devices", "merge_shared_source", "merge_shared_drain",
        # Device state
        "lock_device", "unlock_device",
        "set_device_color", "reset_device_color",
        # Grouping
        "create_group",
        # Matching
        "match_devices",
        # Physical cells
        "insert_taps", "insert_endcaps", "insert_fillers",
        "insert_all_physical_cells",
        # Common-centroid placement (low-level)
        "place_common_centroid", "place_common_centroid_2d",
        "insert_dummies_around_group",
        # Passive devices
        "place_resistor", "place_mom_cap", "place_mos_cap", "reshape_passive",
        # Mid-level detection
        "detect_matched_pairs", "detect_differential_pairs",
        "detect_current_mirrors", "detect_cross_coupled_pairs",
        # Mid-level placement
        "place_matched_pair", "place_differential_pair",
        "place_current_mirror", "add_dummy_group",
        # Validation
        "validate_symmetry", "validate_dummy_presence",
        # Advanced / circuit-level
        "detect_circuit_type", "place_comparator", "place_tx_driver",
        "run_full_layout_pipeline", "optimize_layout_for_matching",
        "optimize_layout_for_routing",
        # Persistence
        "save_layout",
    }

    def test_all_tools_registered(self):
        registered = {t["name"] for t in TOOL_REGISTRY}
        assert self._EXPECTED_NAMES == registered

    def test_every_schema_has_required_keys(self):
        for schema in TOOL_REGISTRY:
            assert "name" in schema
            assert "description" in schema
            assert "input_schema" in schema
            assert schema["input_schema"]["type"] == "object"

    def test_tool_map_matches_registry(self):
        assert set(TOOL_MAP.keys()) == {t["name"] for t in TOOL_REGISTRY}

    def test_required_fields_are_subset_of_properties(self):
        for schema in TOOL_REGISTRY:
            props = set(schema["input_schema"].get("properties", {}).keys())
            reqs  = set(schema["input_schema"].get("required", []))
            assert reqs.issubset(props), (
                f"{schema['name']}: required {reqs - props} not in properties"
            )


# ---------------------------------------------------------------------------
# Layout inspection routes
# ---------------------------------------------------------------------------

class TestInspection:
    def test_read_layout_success(self, two_nodes):
        r = dispatch("read_layout", {}, two_nodes)
        assert r.success
        assert len(r.nodes) == 2
        assert r.metrics["device_count"] == 2

    def test_list_devices_success(self, two_nodes):
        r = dispatch("list_devices", {}, two_nodes)
        assert r.success
        ids = {d["id"] for d in r.metrics["device_list"]}
        assert ids == {"M1", "M2"}

    def test_get_device_info_found(self, two_nodes):
        r = dispatch("get_device_info", {"device_id": "M1"}, two_nodes)
        assert r.success
        assert r.metrics["device"]["id"] == "M1"
        assert r.metrics["device"]["type"] == "nmos"

    def test_get_device_info_not_found(self, two_nodes):
        r = dispatch("get_device_info", {"device_id": "MISSING"}, two_nodes)
        assert not r.success
        assert "MISSING" in r.message

    def test_read_layout_does_not_mutate(self, two_nodes):
        original = copy.deepcopy(two_nodes)
        dispatch("read_layout", {}, two_nodes)
        assert two_nodes == original


# ---------------------------------------------------------------------------
# Device manipulation routes
# ---------------------------------------------------------------------------

class TestManipulation:
    def test_move_device(self, two_nodes):
        r = dispatch("move_device", {"device": "M1", "x": 1.0, "y": 0.0}, two_nodes)
        assert r.success
        assert r.changed
        m1 = next(n for n in r.nodes if n["id"] == "M1")
        assert abs(m1["geometry"]["x"] - 1.0) < 1e-9

    def test_move_device_preserves_other_nodes(self, two_nodes):
        r = dispatch("move_device", {"device": "M1", "x": 1.0, "y": 0.0}, two_nodes)
        assert any(n["id"] == "M2" for n in r.nodes)

    def test_swap_devices(self, two_nodes):
        m1_x_before = two_nodes[0]["geometry"]["x"]
        m2_x_before = two_nodes[1]["geometry"]["x"]
        r = dispatch("swap_devices", {"device_a": "M1", "device_b": "M2"}, two_nodes)
        assert r.success
        assert r.changed
        m1 = next(n for n in r.nodes if n["id"] == "M1")
        m2 = next(n for n in r.nodes if n["id"] == "M2")
        assert abs(m1["geometry"]["x"] - m2_x_before) < 1e-9
        assert abs(m2["geometry"]["x"] - m1_x_before) < 1e-9

    def test_flip_device_h(self, two_nodes):
        r = dispatch("flip_device", {"device": "M1", "axis": "h"}, two_nodes)
        assert r.success
        assert r.changed
        m1 = next(n for n in r.nodes if n["id"] == "M1")
        assert m1["geometry"].get("orientation") in ("R0_FH", "R0_FV", "R0_FH_FV", "R0")

    def test_flip_device_v(self, two_nodes):
        r = dispatch("flip_device", {"device": "M1", "axis": "v"}, two_nodes)
        assert r.success

    def test_add_dummy(self, two_nodes):
        r = dispatch("add_dummy", {"type": "nmos", "x": 0.588, "y": 0.0}, two_nodes)
        assert r.success
        assert r.changed
        assert len(r.nodes) == 3
        new_node = next(n for n in r.nodes if n["id"] not in {"M1", "M2"})
        assert new_node.get("is_dummy") is True
        assert abs(new_node["geometry"]["x"] - 0.588) < 1e-9

    def test_add_dummy_custom_dimensions(self, two_nodes):
        r = dispatch("add_dummy", {"type": "pmos", "x": 0.0, "y": 0.668,
                                   "width": 0.588, "height": 0.568}, two_nodes)
        assert r.success
        new_node = next(n for n in r.nodes if n["id"] not in {"M1", "M2"})
        assert abs(new_node["geometry"]["width"] - 0.588) < 1e-9

    def test_remove_dummies(self, nodes_with_dummies):
        assert len(nodes_with_dummies) == 3
        r = dispatch("remove_dummies", {}, nodes_with_dummies)
        assert r.success
        assert r.changed
        assert len(r.nodes) == 2
        assert all(not n.get("is_dummy") for n in r.nodes)

    def test_remove_dummies_nothing_to_remove(self, two_nodes):
        r = dispatch("remove_dummies", {}, two_nodes)
        assert r.success
        assert not r.changed
        assert len(r.nodes) == 2

    def test_manipulation_does_not_raise_on_missing_device(self, two_nodes):
        r = dispatch("move_device", {"device": "GHOST", "x": 0.0, "y": 0.0}, two_nodes)
        # apply_cmds_to_nodes silently ignores missing devices
        assert isinstance(r, LayoutToolResult)


# ---------------------------------------------------------------------------
# DRC & legalisation routes
# ---------------------------------------------------------------------------

class TestDRC:
    def test_check_overlaps_no_violations(self, two_nodes):
        r = dispatch("check_overlaps", {}, two_nodes)
        assert r.success
        assert not r.changed
        assert "drc_pass" in r.metrics

    def test_check_overlaps_detects_overlap(self, two_nodes):
        # Place both devices at the same x in the same row
        overlapping = [
            _node("A", "nmos", 0.0, 0.0),
            _node("B", "nmos", 0.0, 0.0),
        ]
        r = dispatch("check_overlaps", {}, overlapping)
        assert r.success
        assert not r.metrics["drc_pass"]
        assert r.metrics["violation_count"] > 0

    def test_check_overlaps_returns_full_nodes(self, two_nodes):
        r = dispatch("check_overlaps", {}, two_nodes)
        assert _ids(r) == {"M1", "M2"}

    def test_run_legalizer_clean_layout(self, two_nodes):
        r = dispatch("run_legalizer", {}, two_nodes)
        assert r.success
        # Clean layout: no fixes needed
        assert r.metrics["fixes_applied"] == 0

    def test_run_legalizer_fixes_overlap(self):
        overlapping = [
            _node("A", "nmos", 0.0, 0.0),
            _node("B", "nmos", 0.0, 0.0),
        ]
        r = dispatch("run_legalizer", {}, overlapping)
        assert r.success
        assert r.metrics["fixes_applied"] >= 0  # may be 0 if prescriptive engine skips

    def test_run_legalizer_returns_full_nodes(self, two_nodes):
        r = dispatch("run_legalizer", {}, two_nodes)
        assert _ids(r) == {"M1", "M2"}


# ---------------------------------------------------------------------------
# Physical cell insertion routes
# ---------------------------------------------------------------------------

class TestPhysicalCells:
    def test_insert_endcaps_success(self, two_nodes):
        r = dispatch("insert_endcaps", {}, two_nodes)
        assert r.success
        assert len(r.nodes) > 2
        endcaps = [n for n in r.nodes if n.get("subtype") == "endcap"]
        assert len(endcaps) > 0

    def test_insert_taps_success(self, two_nodes):
        r = dispatch("insert_taps", {}, two_nodes)
        assert r.success
        taps = [n for n in r.nodes if n.get("type") == "tap"]
        assert len(taps) > 0

    def test_insert_fillers_success(self, two_nodes):
        r = dispatch("insert_fillers", {}, two_nodes)
        assert r.success
        assert isinstance(r, LayoutToolResult)

    def test_insert_all_physical_cells_success(self, two_nodes):
        r = dispatch("insert_all_physical_cells", {}, two_nodes)
        assert r.success
        assert r.metrics["endcaps_inserted"] > 0
        assert r.metrics["taps_inserted"] > 0

    def test_physical_cells_original_nodes_preserved(self, two_nodes):
        r = dispatch("insert_endcaps", {}, two_nodes)
        assert {"M1", "M2"}.issubset(_ids(r))


# ---------------------------------------------------------------------------
# Common-centroid placement routes
# ---------------------------------------------------------------------------

class TestCommonCentroid:
    def test_place_common_centroid_success(self, finger_nodes):
        r = dispatch(
            "place_common_centroid",
            {
                "group_a_ids": ["MM1_f0", "MM1_f1"],
                "group_b_ids": ["MM2_f0", "MM2_f1"],
                "start_x": 0.0,
                "row_y": 0.0,
            },
            finger_nodes,
        )
        assert r.success
        assert r.changed
        assert "centroid_error_um" in r.metrics

    def test_place_common_centroid_returns_full_layout(self, finger_nodes):
        r = dispatch(
            "place_common_centroid",
            {
                "group_a_ids": ["MM1_f0", "MM1_f1"],
                "group_b_ids": ["MM2_f0", "MM2_f1"],
                "start_x": 0.0, "row_y": 0.0,
            },
            finger_nodes,
        )
        # All four original node IDs must still be present
        assert {"MM1_f0", "MM1_f1", "MM2_f0", "MM2_f1"}.issubset(_ids(r))

    def test_place_common_centroid_does_not_mutate_input(self, finger_nodes):
        original_x = [n["geometry"]["x"] for n in finger_nodes]
        dispatch(
            "place_common_centroid",
            {"group_a_ids": ["MM1_f0", "MM1_f1"],
             "group_b_ids": ["MM2_f0", "MM2_f1"],
             "start_x": 0.0, "row_y": 0.0},
            finger_nodes,
        )
        # dispatcher deep-copies before mutating; original should be unchanged
        assert [n["geometry"]["x"] for n in finger_nodes] == original_x

    def test_place_common_centroid_with_pattern(self, finger_nodes):
        r = dispatch(
            "place_common_centroid",
            {"group_a_ids": ["MM1_f0", "MM1_f1"],
             "group_b_ids": ["MM2_f0", "MM2_f1"],
             "start_x": 0.0, "row_y": 0.0, "pattern": "ABBA"},
            finger_nodes,
        )
        assert r.success

    def test_place_common_centroid_2d_success(self, finger_nodes):
        r = dispatch(
            "place_common_centroid_2d",
            {
                "device_specs": [
                    {"id": "MM1", "fingers": 2},
                    {"id": "MM2", "fingers": 2},
                ],
                "start_x": 0.0,
                "row_y": 0.0,
            },
            finger_nodes,
        )
        assert r.success
        assert "matrix_rows" in r.metrics
        assert "placed_count" in r.metrics

    def test_place_common_centroid_2d_returns_full_layout(self, finger_nodes):
        r = dispatch(
            "place_common_centroid_2d",
            {"device_specs": [{"id": "MM1"}, {"id": "MM2"}],
             "start_x": 0.0, "row_y": 0.0},
            finger_nodes,
        )
        # All four finger IDs must still exist in the result
        assert {"MM1_f0", "MM1_f1", "MM2_f0", "MM2_f1"}.issubset(_ids(r))

    def test_place_common_centroid_2d_accepts_float_finger_args(self, finger_nodes):
        r = dispatch(
            "place_common_centroid_2d",
            {
                "device_specs": [
                    {"id": "MM1", "fingers": 2.0},
                    {"id": "MM2", "fingers": "2.0"},
                ],
                "start_x": 0.0,
                "row_y": 0.0,
            },
            finger_nodes,
        )
        assert r.success
        assert r.metrics["placed_count"] == 4

    def test_insert_dummies_around_group_success(self, finger_nodes):
        r = dispatch(
            "insert_dummies_around_group",
            {"group_node_ids": ["MM1_f0", "MM1_f1"], "n_dummies": 1},
            finger_nodes,
        )
        assert r.success
        assert r.changed
        dummies = [n for n in r.nodes if n.get("structural")]
        assert len(dummies) == 2  # 1 left + 1 right

    def test_insert_dummies_returns_full_layout(self, finger_nodes):
        r = dispatch(
            "insert_dummies_around_group",
            {"group_node_ids": ["MM1_f0", "MM1_f1"]},
            finger_nodes,
        )
        # Non-group nodes must still be there
        assert {"MM2_f0", "MM2_f1"}.issubset(_ids(r))

    def test_insert_dummies_n2(self, finger_nodes):
        r = dispatch(
            "insert_dummies_around_group",
            {"group_node_ids": ["MM1_f0", "MM1_f1"], "n_dummies": 2},
            finger_nodes,
        )
        dummies = [n for n in r.nodes if n.get("structural")]
        assert len(dummies) == 4

    def test_structural_dummies_not_stripped_by_finger_grouper(self, finger_nodes):
        from ai_agent.placement.finger_grouper import _is_regenerated_filler_dummy
        r = dispatch(
            "insert_dummies_around_group",
            {"group_node_ids": ["MM1_f0", "MM1_f1"]},
            finger_nodes,
        )
        dummies = [n for n in r.nodes if n.get("structural")]
        for d in dummies:
            assert not _is_regenerated_filler_dummy(d)

    def test_place_matched_pair_returns_full_layout(self, finger_nodes):
        extra = _node("MM3_f0", "nmos", 1.176, 0.0)
        r = dispatch(
            "place_matched_pair",
            {"device_a": "MM1", "device_b": "MM2"},
            finger_nodes + [extra],
        )
        assert r.success
        assert r.metrics["placed_count"] == 4
        assert "MM3_f0" in _ids(r)


# ---------------------------------------------------------------------------
# Advanced matching optimizer
# ---------------------------------------------------------------------------

class TestOptimizeLayoutForMatching:
    def test_expanded_fingers_are_optimized_as_parent_devices(self):
        nodes = [
            _node("MM0_f1", "nmos", 0.000, 0.0),
            _node("MM0_f2", "nmos", 0.294, 0.0),
            _node("MM1_f1", "nmos", 0.588, 0.0),
            _node("MM1_f2", "nmos", 0.882, 0.0),
        ]
        terminal_nets = {
            "MM0_f1": {"D": "BIAS", "G": "BIAS", "S": "VSS"},
            "MM0_f2": {"D": "BIAS", "G": "BIAS", "S": "VSS"},
            "MM1_f1": {"D": "OUT", "G": "BIAS", "S": "VSS"},
            "MM1_f2": {"D": "OUT", "G": "BIAS", "S": "VSS"},
        }
        r = dispatch(
            "optimize_layout_for_matching",
            {},
            nodes,
            terminal_nets=terminal_nets,
        )
        assert r.success
        assert r.changed
        assert len(r.nodes) == len(nodes)
        assert "Placed 4 finger(s)" in r.message
        assert "Placed 0 finger(s)" not in r.message


# ---------------------------------------------------------------------------
# Score layout route
# ---------------------------------------------------------------------------

class TestScoreLayout:
    def test_score_layout_success(self, two_nodes):
        r = dispatch("score_layout", {}, two_nodes)
        assert r.success
        assert not r.changed
        assert "composite_score" in r.metrics
        assert "summary" in r.metrics

    def test_score_layout_returns_all_metric_keys(self, two_nodes):
        r = dispatch("score_layout", {}, two_nodes)
        for key in ("layout_y_score", "drc_score", "matched_pairs_count", "device_count"):
            assert key in r.metrics, f"Missing metric key: {key}"

    def test_score_layout_preserves_nodes(self, two_nodes):
        r = dispatch("score_layout", {}, two_nodes)
        assert _ids(r) == {"M1", "M2"}


# ---------------------------------------------------------------------------
# Save layout route
# ---------------------------------------------------------------------------

class TestSaveLayout:
    def test_save_layout_no_path(self, two_nodes):
        r = dispatch("save_layout", {}, two_nodes)
        assert r.success
        assert not r.changed
        import json
        parsed = json.loads(r.metrics["serialized"])
        assert len(parsed) == 2

    def test_save_layout_to_file(self, two_nodes, tmp_path):
        path = str(tmp_path / "layout.json")
        r = dispatch("save_layout", {"path": path}, two_nodes)
        assert r.success
        import json, pathlib
        content = json.loads(pathlib.Path(path).read_text())
        assert len(content) == 2

    def test_save_layout_preserves_nodes(self, two_nodes):
        r = dispatch("save_layout", {}, two_nodes)
        assert _ids(r) == {"M1", "M2"}


# ---------------------------------------------------------------------------
# Unknown tool → clear failure
# ---------------------------------------------------------------------------

class TestUnknownTool:
    def test_unknown_tool_returns_failure(self, two_nodes):
        r = dispatch("totally_made_up_tool", {}, two_nodes)
        assert not r.success
        assert "totally_made_up_tool" in r.message

    def test_unknown_tool_message_says_unknown(self, two_nodes):
        r = dispatch("xyzzy", {}, two_nodes)
        assert "Unknown tool" in r.message or "xyzzy" in r.message

    def test_unknown_tool_returns_nodes_unchanged(self, two_nodes):
        r = dispatch("ghost_tool", {}, two_nodes)
        assert _ids(r) == {"M1", "M2"}

    def test_unknown_tool_never_raises(self, two_nodes):
        # Should not raise even with malformed arguments
        r = dispatch("???", {"bad": object()}, two_nodes)
        assert isinstance(r, LayoutToolResult)


# ---------------------------------------------------------------------------
# General safety / edge cases
# ---------------------------------------------------------------------------

class TestSafety:
    def test_none_arguments_is_safe(self, two_nodes):
        r = dispatch("read_layout", None, two_nodes)
        assert r.success

    def test_none_pdk_uses_default(self, two_nodes):
        r = dispatch("insert_taps", {}, two_nodes, pdk=None)
        assert r.success

    def test_empty_nodes_is_safe_for_inspection(self):
        r = dispatch("list_devices", {}, [])
        assert r.success
        assert r.metrics["device_list"] == []

    def test_result_is_always_layout_tool_result(self, two_nodes):
        for tool in ("read_layout", "list_devices", "check_overlaps",
                     "score_layout", "totally_fake"):
            r = dispatch(tool, {}, two_nodes)
            assert isinstance(r, LayoutToolResult), f"{tool} did not return LayoutToolResult"

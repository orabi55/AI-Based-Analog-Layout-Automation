from types import SimpleNamespace

import pytest

from ai_agent.tools.cmd_parser import apply_cmds_to_nodes
from ai_agent.placement.finger_grouper import (
    _resolve_row_overlaps,
    expand_to_fingers,
    legalize_vertical_rows,
)
from symbolic_editor.layout_tab import LayoutEditorTab


def test_command_deduplication_resolves_same_type_row_overlaps():
    nodes = [
        {
            "id": "MN1",
            "type": "nmos",
            "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.668},
        },
        {
            "id": "MN2",
            "type": "nmos",
            "geometry": {"x": 0.588, "y": 0.0, "width": 0.294, "height": 0.668},
        },
    ]

    placed = apply_cmds_to_nodes(nodes, [{"action": "move", "device": "MN2", "x": 0.0, "y": 0.0}])
    by_id = {node["id"]: node for node in placed}

    assert by_id["MN2"]["geometry"]["x"] >= by_id["MN1"]["geometry"]["x"] + by_id["MN1"]["geometry"]["width"]


def test_deterministic_fallback_legalizer_shifts_same_row_overlaps():
    nodes = [
        {
            "id": "MP1",
            "type": "pmos",
            "geometry": {"x": 0.0, "y": 0.668, "width": 0.294, "height": 0.668},
        },
        {
            "id": "MP2",
            "type": "pmos",
            "geometry": {"x": 0.1, "y": 0.668, "width": 0.294, "height": 0.668},
        },
    ]

    LayoutEditorTab._resolve_deterministic_node_overlaps(nodes)

    assert nodes[1]["geometry"]["x"] == 0.294


def test_dummy_builder_converts_scene_y_back_to_layout_y():
    tab = LayoutEditorTab.__new__(LayoutEditorTab)
    tab.editor = SimpleNamespace(scale_factor=80)
    tab.nodes = [
        {
            "id": "MP1",
            "type": "pmos",
            "electrical": {"l": 1.4e-8, "nf": 1, "nfin": 2},
            "geometry": {"x": 0.0, "y": 0.668, "width": 0.294, "height": 0.668},
        }
    ]

    dummy = LayoutEditorTab._build_dummy_node(
        tab,
        {"type": "pmos", "x": 160.0, "y": -53.44, "width": 23.52, "height": 53.44},
    )

    assert dummy["id"] == "DUMMYP1"
    assert dummy["geometry"]["x"] == 2.0
    assert dummy["geometry"]["y"] == pytest.approx(0.668)


def test_vertical_legalizer_stacks_rows_by_actual_height():
    nodes = [
        {
            "id": "MP1",
            "type": "pmos",
            "geometry": {"x": 0.0, "y": 0.818, "width": 0.294, "height": 0.818},
        },
        {
            "id": "MP2",
            "type": "pmos",
            "geometry": {"x": 0.0, "y": 1.486, "width": 0.294, "height": 0.818},
        },
    ]

    legalize_vertical_rows(nodes)

    assert nodes[1]["geometry"]["y"] == pytest.approx(1.636)


def test_row_resolver_preserves_structural_matrix_dummies():
    nodes = [
        {
            "id": "DUMMY_matrix_BLOCK_0",
            "type": "nmos",
            "is_dummy": True,
            "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.668},
            "electrical": {},
        },
        {
            "id": "MN1",
            "type": "nmos",
            "geometry": {"x": 0.294, "y": 0.0, "width": 0.294, "height": 0.668},
            "electrical": {},
        },
    ]

    resolved = _resolve_row_overlaps(nodes)

    assert any(node["id"] == "DUMMY_matrix_BLOCK_0" for node in resolved)


def test_common_centroid_matrix_expansion_keeps_side_dummies():
    group_id = "M1_centroid"
    member = {
        "id": "M1_f1",
        "type": "nmos",
        "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.668},
        "electrical": {"l": 1.4e-8, "nfin": 2},
    }
    group_placement = [
        {
            "id": group_id,
            "type": "nmos",
            "geometry": {"x": 1.0, "y": 0.0, "width": 0.21, "height": 0.668},
        }
    ]
    original_group_nodes = {
        group_id: {
            "_matched_block": True,
            "_technique": "common_centroid_mirror",
            "_block_pitch": 0.070,
            "_matrix_data": {
                "matrix": [["dummy", "M1", "dummy"]],
                "rows": 1,
                "cols": 3,
            },
        }
    }

    expanded = expand_to_fingers(
        group_placement,
        {group_id: [member]},
        original_group_nodes=original_group_nodes,
    )

    dummy_ids = [node["id"] for node in expanded if node.get("is_dummy")]
    assert len(dummy_ids) == 2
    assert all(dummy_id.startswith(f"DUMMY_matrix_{group_id}_") for dummy_id in dummy_ids)

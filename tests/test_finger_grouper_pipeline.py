"""Regression tests for the finger grouping → placement → expansion pipeline."""

import pytest
from ai_agent.placement.finger_grouper import (
    group_fingers,
    expand_groups,
    FINGER_PITCH,
    STD_PITCH,
)
from ai_agent.placement.validators import _validate_placement


def _make_finger_node(dev_id: str, dev_type: str, nfin: int = 2) -> dict:
    """Build a minimal finger-level node dict."""
    return {
        "id": dev_id,
        "type": dev_type,
        "electrical": {"nfin": nfin, "l": 1.4e-8, "nf": 1, "w": 1e-6},
        "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.668},
    }


def test_group_fingers_collapse_multi_finger_transistor():
    """8 finger nodes of the same transistor must collapse to 1 group."""
    nodes = [_make_finger_node(f"MM0_f{i}", "nmos") for i in range(1, 9)]
    edges = []
    group_nodes, group_edges, finger_map = group_fingers(nodes, edges)

    assert len(group_nodes) == 1
    assert group_nodes[0]["id"] == "MM0"
    assert group_nodes[0]["electrical"]["total_fingers"] == 8
    assert len(finger_map["MM0"]) == 8


def test_expand_groups_restores_abutment_spacing():
    """Expanding a placed group must place fingers exactly FINGER_PITCH apart after healing."""
    # Two transistors: MM0 (4 fingers), MM1 (2 fingers)
    nodes = (
        [_make_finger_node(f"MM0_f{i}", "nmos") for i in range(1, 5)]
        + [_make_finger_node(f"MM1_f{i}", "nmos") for i in range(1, 3)]
    )
    edges = []
    group_nodes, group_edges, finger_map = group_fingers(nodes, edges)

    # Place them sufficiently far apart to avoid adjacent bridge dummy insertion
    placed_groups = [
        {"id": "MM0", "type": "nmos", "geometry": {"x": 0.0, "y": 0.0}},
        {"id": "MM1", "type": "nmos", "geometry": {"x": 10.0, "y": 0.0}},
    ]

    expanded = expand_groups(placed_groups, finger_map, no_abutment=False)
    
    # Under Symbolic Slot System, fingers in editor are at STD_PITCH (0.294um).
    # We run heal_abutment_positions to compress them to physical FINGER_PITCH (0.07um) spacing.
    from ai_agent.placement.abutment import heal_abutment_positions
    expanded = heal_abutment_positions(expanded, candidates=[])
    
    assert len(expanded) == 7  # 4 + 2 fingers + 1 bridge dummy (since they don't share a net and are packed adjacent)

    # Check abutment spacing within each group
    mm0_fingers = [n for n in expanded if n["id"].startswith("MM0_f")]
    mm0_fingers.sort(key=lambda n: n["geometry"]["x"])
    for i in range(len(mm0_fingers) - 1):
        dx = mm0_fingers[i + 1]["geometry"]["x"] - mm0_fingers[i]["geometry"]["x"]
        assert abs(dx - FINGER_PITCH) < 1e-9, f"MM0 finger spacing wrong: {dx}"

    mm1_fingers = [n for n in expanded if n["id"].startswith("MM1_f")]
    mm1_fingers.sort(key=lambda n: n["geometry"]["x"])
    for i in range(len(mm1_fingers) - 1):
        dx = mm1_fingers[i + 1]["geometry"]["x"] - mm1_fingers[i]["geometry"]["x"]
        assert abs(dx - FINGER_PITCH) < 1e-9, f"MM1 finger spacing wrong: {dx}"


def test_expand_groups_no_abutment_uses_std_pitch():
    """With no_abutment=True, fingers must use STD_PITCH, not FINGER_PITCH."""
    nodes = [_make_finger_node(f"MM0_f{i}", "nmos") for i in range(1, 4)]
    edges = []
    group_nodes, group_edges, finger_map = group_fingers(nodes, edges)

    placed_groups = [
        {"id": "MM0", "type": "nmos", "geometry": {"x": 0.0, "y": 0.0}},
    ]

    expanded = expand_groups(placed_groups, finger_map, no_abutment=True)
    expanded.sort(key=lambda n: n["geometry"]["x"])
    for i in range(len(expanded) - 1):
        dx = expanded[i + 1]["geometry"]["x"] - expanded[i]["geometry"]["x"]
        assert abs(dx - STD_PITCH) < 1e-9, f"no_abutment spacing wrong: {dx}"


def test_expand_groups_preserves_pmos_nmos_separation():
    """PMOS fingers must end up on a Y strictly above all NMOS fingers."""
    nmos_nodes = [_make_finger_node(f"MM0_f{i}", "nmos", nfin=4) for i in range(1, 3)]
    pmos_nodes = [_make_finger_node(f"MM1_f{i}", "pmos", nfin=4) for i in range(1, 3)]
    group_nodes, group_edges, finger_map = group_fingers(nmos_nodes + pmos_nodes, [])

    # LLM mistakenly puts both at y=0.0
    placed_groups = [
        {"id": "MM0", "type": "nmos", "geometry": {"x": 0.0, "y": 0.0}},
        {"id": "MM1", "type": "pmos", "geometry": {"x": 0.0, "y": 0.0}},
    ]

    expanded = expand_groups(placed_groups, finger_map)
    nmos_ys = [n["geometry"]["y"] for n in expanded if n["type"] == "nmos"]
    pmos_ys = [n["geometry"]["y"] for n in expanded if n["type"] == "pmos"]

    assert nmos_ys
    assert pmos_ys
    assert min(pmos_ys) > max(nmos_ys), (
        f"PMOS/NMOS separation violated: PMOS min={min(pmos_ys)} vs NMOS max={max(nmos_ys)}"
    )


def test_expand_groups_sets_explicit_width():
    """Every expanded finger must have geometry.width set to the pitch used in editor (STD_PITCH)."""
    nodes = [_make_finger_node(f"MM0_f{i}", "nmos") for i in range(1, 3)]
    group_nodes, group_edges, finger_map = group_fingers(nodes, [])
    placed_groups = [
        {"id": "MM0", "type": "nmos", "geometry": {"x": 0.0, "y": 0.0}},
    ]
    expanded = expand_groups(placed_groups, finger_map, no_abutment=False)
    for n in expanded:
        assert "width" in n.get("geometry", {}), f"{n['id']} missing geometry.width"
        assert n["geometry"]["width"] == STD_PITCH


def test_full_pipeline_validate_no_errors():
    """Run the complete group → place → expand pipeline and assert _validate_placement passes after healing."""
    nodes = (
        [_make_finger_node(f"MM0_f{i}", "nmos") for i in range(1, 5)]
        + [_make_finger_node(f"MM1_f{i}", "nmos") for i in range(1, 3)]
        + [_make_finger_node(f"MM2_f{i}", "pmos") for i in range(1, 3)]
    )
    edges = []
    group_nodes, group_edges, finger_map = group_fingers(nodes, edges)

    placed_groups = [
        {"id": "MM0", "type": "nmos", "geometry": {"x": 0.0, "y": 0.0}},
        {"id": "MM1", "type": "nmos", "geometry": {"x": 10.0, "y": 0.0}},  # Far apart to avoid bridge dummy
        {"id": "MM2", "type": "pmos", "geometry": {"x": 0.0, "y": 2.0}},   # Far apart to avoid bridge/filler dummy
    ]

    expanded = expand_groups(placed_groups, finger_map)
    
    # Heal to compress to physical abutment spacing (0.07um)
    from ai_agent.placement.abutment import heal_abutment_positions
    expanded = heal_abutment_positions(expanded, candidates=[])
    
    # Filter out generated filler/symmetry dummies so EXTRA devices assertion does not fail
    expanded_no_dummies = [n for n in expanded if not n.get("is_dummy") and not n.get("id", "").startswith("FILLER_DUMMY")]
    
    errors = _validate_placement(nodes, expanded_no_dummies)
    assert not errors, f"Validation errors: {errors}"


def test_group_fingers_preserves_multi_finger_multiplier_hierarchy():
    """nf=4, m=2 → 8 finger nodes that collapse into 1 group with multiplier=2, nf=4."""
    nodes = []
    for m in range(1, 3):
        for f in range(1, 5):
            nodes.append(_make_finger_node(f"MM0_m{m}_f{f}", "nmos"))
    edges = []
    group_nodes, group_edges, finger_map = group_fingers(nodes, edges)

    assert len(group_nodes) == 1
    elec = group_nodes[0]["electrical"]
    assert elec["multiplier"] == 2
    assert elec["nf_per_device"] == 4
    assert elec["total_fingers"] == 8
    assert len(finger_map["MM0"]) == 8


def test_expand_groups_abutment_flags_first_and_last():
    """First finger in a group must have abut_left=False, last must have abut_right=False."""
    nodes = [_make_finger_node(f"MM0_f{i}", "nmos") for i in range(1, 5)]
    group_nodes, group_edges, finger_map = group_fingers(nodes, [])
    placed_groups = [
        {"id": "MM0", "type": "nmos", "geometry": {"x": 0.0, "y": 0.0}},
    ]
    expanded = expand_groups(placed_groups, finger_map)
    expanded.sort(key=lambda n: n["geometry"]["x"])
    assert expanded[0]["abutment"]["abut_left"] is False
    assert expanded[0]["abutment"]["abut_right"] is True
    assert expanded[-1]["abutment"]["abut_left"] is True
    assert expanded[-1]["abutment"]["abut_right"] is False
    for mid in expanded[1:-1]:
        assert mid["abutment"]["abut_left"] is True
        assert mid["abutment"]["abut_right"] is True


def test_huge_transistor_autofolds_to_2d_matrix():
    """Any transistor with total_fingers >= 12 must automatically fold into a 2D matrix."""
    # 24 fingers of the same transistor
    nodes = [_make_finger_node(f"MM0_f{i}", "nmos") for i in range(1, 25)]
    edges = []
    group_nodes, group_edges, finger_map = group_fingers(nodes, edges)

    assert len(group_nodes) == 1
    g = group_nodes[0]
    assert g["id"] == "MM0"
    assert g["electrical"]["total_fingers"] == 24
    
    # It must have been auto-folded to 2D
    assert g.get("_n_rows", 1) >= 2
    assert "_matrix_data" in g
    matrix_data = g["_matrix_data"]
    assert matrix_data["rows"] >= 2
    assert matrix_data["cols"] >= 2
    
    # Expanding it must result in 2D coordinates (multiple rows used)
    placed_groups = [
        {"id": "MM0", "type": "nmos", "geometry": {"x": 0.0, "y": 0.0, "width": g["geometry"]["width"]}},
    ]
    orig_lookup = {g["id"]: g for g in group_nodes}
    expanded = expand_groups(placed_groups, finger_map, original_group_nodes=orig_lookup)
    ys = {n["geometry"]["y"] for n in expanded if not n.get("is_dummy")}
    assert len(ys) == matrix_data["rows"]

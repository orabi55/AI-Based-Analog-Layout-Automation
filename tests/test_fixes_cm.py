"""
Test script for all C/M fixes against the current_mirror example.
Run from project root: python tests/test_fixes_cm.py
"""
import sys
import os
import json
import copy

# ---- Path setup ----
ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

EXAMPLE_DIR = os.path.join(ROOT, "examples", "current_mirror")
GRAPH_JSON   = os.path.join(EXAMPLE_DIR, "Current_Mirror_CM_graph.json")
SPICE_FILE   = os.path.join(EXAMPLE_DIR, "Current_Mirror_CM.sp")

# ─────────────────────────────────────────────────────────────────────────────
# 1. Load test data
# ─────────────────────────────────────────────────────────────────────────────
with open(GRAPH_JSON) as f:
    graph = json.load(f)

nodes = graph["nodes"]
edges = graph["edges"]

print(f"[TEST] Loaded {len(nodes)} physical nodes, {len(edges)} edges")

# Build minimal terminal_nets from graph (CM: MM0 diode, MM1+MM2 copies)
# Source of truth comes from the SPICE file, but we test without relying on LLM
terminal_nets = {
    "MM0": {"D": "C", "G": "C", "S": "gnd", "B": "gnd"},
    "MM1": {"D": "B", "G": "C", "S": "gnd", "B": "gnd"},
    "MM2": {"D": "A", "G": "C", "S": "gnd", "B": "gnd"},
}


# ─────────────────────────────────────────────────────────────────────────────
# TEST C5 — Bus-notation devices not dropped
# ─────────────────────────────────────────────────────────────────────────────
from ai_agent.finger_grouping import aggregate_to_logical_devices, group_fingers

def test_C5_bus_notation():
    """Synthetic test: inject a bus-notation device and verify it survives."""
    bus_node = {
        "id": "MM8<0>",
        "type": "nmos",
        "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.668},
        "electrical": {"nf": 1},
        "is_dummy": False,
    }
    test_nodes = nodes[:4] + [bus_node]
    logical = aggregate_to_logical_devices(test_nodes)
    logical_ids = {n["id"] for n in logical}
    assert "MM8<0>" in logical_ids, f"C5 FAIL: bus device lost. Got {logical_ids}"
    print("[PASS] C5 — Bus-notation device preserved in aggregate_to_logical_devices")

test_C5_bus_notation()


# ─────────────────────────────────────────────────────────────────────────────
# TEST C2 — Multi-row DRC ROW check
# ─────────────────────────────────────────────────────────────────────────────
from ai_agent.drc_critic import run_drc_check

def test_C2_multi_row():
    """NMOS devices at y=0.0 and y=0.668 (common-centroid) must NOT be flagged."""
    multi_row_nodes = [
        {
            "id": "MM0_f1",
            "type": "nmos",
            "geometry": {"x": 0.0, "y": 0.0,   "width": 0.294, "height": 0.668},
        },
        {
            "id": "MM0_f2",
            "type": "nmos",
            "geometry": {"x": 0.294, "y": 0.0,  "width": 0.294, "height": 0.668},
        },
        {
            "id": "MM1_f1",
            "type": "nmos",
            "geometry": {"x": 0.0, "y": 0.668,  "width": 0.294, "height": 0.668},
        },
        {
            "id": "MM1_f2",
            "type": "nmos",
            "geometry": {"x": 0.294, "y": 0.668, "width": 0.294, "height": 0.668},
        },
    ]
    result = run_drc_check(multi_row_nodes, gap_px=0.0)
    row_errors = [v for v in result["violations"] if "ROW ERROR" in v]
    assert len(row_errors) == 0, f"C2 FAIL: Multi-row devices got false ROW_ERRORs:\n  {row_errors}"
    print("[PASS] C2 — Multi-row NMOS layout produces no false ROW_ERROR violations")

test_C2_multi_row()


# ─────────────────────────────────────────────────────────────────────────────
# TEST C2b — Genuinely wrong row IS flagged
# ─────────────────────────────────────────────────────────────────────────────
def test_C2_real_error():
    """PMOS device placed in NMOS row MUST be flagged."""
    mixed_nodes = [
        {
            "id": "MP1",
            "type": "pmos",
            "geometry": {"x": 0.0, "y": -0.668, "width": 0.294, "height": 0.668},
        },
        {
            "id": "MN1",
            "type": "nmos",
            "geometry": {"x": 0.0, "y":  0.0,   "width": 0.294, "height": 0.668},
        },
        {
            "id": "MP2_wrong_row",
            "type": "pmos",
            "geometry": {"x": 0.294, "y": 0.0,  "width": 0.294, "height": 0.668},  # PMOS in NMOS row!
        },
    ]
    result = run_drc_check(mixed_nodes, gap_px=0.0)
    row_errors = [v for v in result["violations"] if "ROW ERROR" in v]
    assert len(row_errors) > 0, "C2b FAIL: Misplaced PMOS in NMOS row not flagged"
    assert "MP2_wrong_row" in row_errors[0], f"C2b FAIL: wrong device flagged: {row_errors}"
    print("[PASS] C2b — Genuinely misplaced PMOS in NMOS row is correctly flagged")

test_C2_real_error()


# ─────────────────────────────────────────────────────────────────────────────
# TEST C1 — gap_px unit: overlap check works with gap_um=0.0 (no gap enforcement)
# ─────────────────────────────────────────────────────────────────────────────
def test_C1_gap_zero():
    """With gap_um=0.0, adjacent-touching devices must NOT trigger gap violations."""
    adjacent_nodes = [
        {"id": "A", "type": "nmos",
         "geometry": {"x": 0.0,   "y": 0.0, "width": 0.294, "height": 0.668}},
        {"id": "B", "type": "nmos",
         "geometry": {"x": 0.294, "y": 0.0, "width": 0.294, "height": 0.668}},
    ]
    result = run_drc_check(adjacent_nodes, gap_px=0.0)
    gap_violations = [v for v in result["violations"] if "GAP" in v]
    assert len(gap_violations) == 0, f"C1 FAIL: gap violations with gap_um=0: {gap_violations}"
    print("[PASS] C1 — Adjacent devices with gap_px=0 produce no gap violations")

test_C1_gap_zero()


# ─────────────────────────────────────────────────────────────────────────────
# TEST C4 — Odd-nf common-centroid pattern length
# ─────────────────────────────────────────────────────────────────────────────
from ai_agent.placement_specialist import _build_symmetric_interdig_pattern

def test_C4_odd_nf():
    """Odd total nf: pattern length must equal total fingers, no fallback."""
    dev_nf_map = {"MM0": 3, "MM1": 3}   # total = 6 (even)
    pattern = _build_symmetric_interdig_pattern(dev_nf_map, ref_id="MM0")
    assert len(pattern) == 6, f"C4 FAIL (even): pattern length {len(pattern)} != 6"

    dev_nf_map2 = {"MM0": 4, "MM1": 3}  # total = 7 (odd)
    pattern2 = _build_symmetric_interdig_pattern(dev_nf_map2, ref_id="MM0")
    assert len(pattern2) == 7, f"C4 FAIL (odd): pattern length {len(pattern2)} != 7"
    print("[PASS] C4 — Odd-nf interdigitation pattern has correct length")

test_C4_odd_nf()


# ─────────────────────────────────────────────────────────────────────────────
# TEST C3 + M6 — Routing cost normalized & wire length includes cross-row component
# ─────────────────────────────────────────────────────────────────────────────
from ai_agent.routing_previewer import score_routing

def test_C3_M6_routing():
    """Cross-row net wire_length must be > span alone. placement_cost must be finite."""
    pmos_node = {
        "id": "MP1",
        "type": "pmos",
        "geometry": {"x": 0.0, "y": -0.668},
    }
    nmos_node = {
        "id": "MN1",
        "type": "nmos",
        "geometry": {"x": 0.0, "y": 0.0},
    }
    cross_edge = [{"source": "MP1", "target": "MN1", "net": "OUT"}]
    cross_term = {
        "MP1": {"D": "OUT"},
        "MN1": {"D": "OUT"},
    }
    result = score_routing([pmos_node, nmos_node], cross_edge, cross_term)

    out_detail = result["net_details"].get("OUT", {})
    wire_len    = out_detail.get("wire_length", 0.0)
    span        = out_detail.get("span", 0.0)
    cost        = result.get("placement_cost", 0.0)

    # Cross-row net: wire_length must include 0.668 µm vertical component
    assert wire_len > span, (
        f"M6 FAIL: cross-row wire_length ({wire_len}) should exceed span ({span})"
    )
    assert wire_len >= 0.668 - 0.001, (
        f"M6 FAIL: cross-row wire_length ({wire_len}) < row_height (0.668)"
    )
    # Normalized cost must be finite and reasonable
    assert 0 <= cost < 1e6, f"C3 FAIL: placement_cost unreasonable: {cost}"
    print(
        f"[PASS] C3+M6 — Cross-row wire_length={wire_len:.3f}µm "
        f"(span={span:.3f}µm), normalized cost={cost:.4f}"
    )

test_C3_M6_routing()


# ─────────────────────────────────────────────────────────────────────────────
# INTEGRATION TEST — Full DRC on current_mirror initial placement
# ─────────────────────────────────────────────────────────────────────────────
def test_integration_drc():
    """Run DRC on the current_mirror example's initial placement."""
    result = run_drc_check(nodes, gap_px=0.0)
    print(f"[INFO] CM DRC: pass={result['pass']}, "
          f"violations={len(result['violations'])}")
    row_errors = [v for v in result["violations"] if "ROW ERROR" in v]
    print(f"[INFO]   ROW_ERRORs: {len(row_errors)}")
    if row_errors:
        for e in row_errors[:3]:
            print(f"       {e[:100]}")
    # The initial placement is all NMOS at y=0.0 → should have NO row errors
    assert len(row_errors) == 0, (
        f"INTEGRATION FAIL: {len(row_errors)} false ROW_ERRORs on CM initial placement"
    )
    print("[PASS] Integration DRC — No false ROW_ERRORs on current_mirror placement")

test_integration_drc()


# ─────────────────────────────────────────────────────────────────────────────
# INTEGRATION TEST — Routing score on current_mirror (grouped placement = bad)
# ─────────────────────────────────────────────────────────────────────────────
def test_integration_routing_before_after():
    """Compare routing cost: grouped (initial) vs interdigitated (optimized)."""
    from ai_agent.placement_specialist import (
        compute_interdigitated_placement, placements_to_cmd_blocks
    )
    from ai_agent.topology_analyst import _parse_spice_directly
    from ai_agent.finger_grouping import aggregate_to_logical_devices

    spice_nets = _parse_spice_directly(SPICE_FILE)
    logical_nodes = aggregate_to_logical_devices(nodes)

    # score_routing on initial (grouped) layout
    initial_score = score_routing(nodes, edges, {})
    initial_cost   = initial_score["placement_cost"]
    initial_wire   = initial_score["total_wire_length"]

    # Build interdigitated placement for MM0(8f) + MM1(4f) + MM2(4f)
    mirror_devs = [n for n in logical_nodes if n["id"] in ("MM0", "MM1", "MM2")]
    placements  = compute_interdigitated_placement(mirror_devs, spice_nets)
    cmds        = placements_to_cmd_blocks(placements)

    # Apply cmds to a copy of nodes
    from ai_agent.orchestrator import _apply_cmds_to_nodes
    from ai_agent.orchestrator import _cmds_to_text

    optimized_nodes = _apply_cmds_to_nodes(copy.deepcopy(nodes), cmds)

    optimized_score = score_routing(optimized_nodes, edges, {})
    optimized_cost   = optimized_score["placement_cost"]
    optimized_wire   = optimized_score["total_wire_length"]

    print(
        f"\n[ROUTING COMPARISON]\n"
        f"  Initial   (grouped):      cost={initial_cost:.4f},  wire={initial_wire:.3f}µm\n"
        f"  Optimized (interdigited): cost={optimized_cost:.4f}, wire={optimized_wire:.3f}µm"
    )

    drc_after = run_drc_check(optimized_nodes, gap_px=0.0)
    print(f"  DRC after interdigitation: pass={drc_after['pass']}, "
          f"violations={len(drc_after['violations'])}")
    assert drc_after["pass"], (
        f"INTEGRATION FAIL: DRC violations after interdigitation:\n"
        + "\n".join(f"  {v}" for v in drc_after["violations"][:5])
    )
    print("[PASS] Integration routing — DRC clean after interdigitation")

test_integration_routing_before_after()

print("\n" + "="*60)
print("ALL TESTS PASSED")
print("="*60)

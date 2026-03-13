"""
tests/test_agent_recovery.py
============================
Chaos Test Suite — Stress-tests the multi-agent Orchestrator's ability
to self-correct from a deliberately broken starting placement.

Test scenarios:
  1. Forced Collision: two devices at identical x/y coordinates.
  2. Cascaded Collisions: three devices all piled on the same spot.
  3. GAP violation: adjacent devices placed too close together.
  4. Mixed: overlaps + gap violation together.
  5. Already-clean layout: confirm pipeline does not introduce new errors.
"""

import sys
import os
import json
import re

# Ensure project root is importable
PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from ai_agent.drc_critic import (
    run_drc_check, compute_prescriptive_fixes, format_drc_violations_for_llm,
)
from ai_agent.routing_previewer import score_routing
from ai_agent.topology_analyst import analyze_topology
from ai_agent.orchestrator import Orchestrator, _apply_cmds_to_nodes, _extract_cmd_blocks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_node(dev_id, x, y, width=1.0, height=1.0, dev_type="nmos", is_dummy=False):
    return {
        "id": dev_id,
        "type": dev_type,
        "is_dummy": is_dummy,
        "geometry": {"x": x, "y": y, "width": width, "height": height},
        "electrical": {"nf": 1, "w": 1.0, "l": 0.1},
    }

def _deterministic_llm(chat_messages, full_prompt):
    """Stubbed LLM that extracts the prescriptive fix from the DRC prompt."""
    cmds = []
    for line in full_prompt.splitlines():
        match = re.search(r'MOVE (\S+) to x=([\d.]+),\s*y=([\d.]+)', line)
        if match:
            dev, x, y = match.group(1), float(match.group(2)), float(match.group(3))
            cmds.append({"action": "move", "device": dev, "x": x, "y": y})
    if cmds:
        cmd_text = "\n".join(f"[CMD]{json.dumps(c)}[/CMD]" for c in cmds)
        return cmd_text + "\n\nFixed."
    return "The placement looks good. No [CMD] blocks needed."

# ---------------------------------------------------------------------------
# ═══ Test 1: Forced Collision (identical coordinates) ═══
# ---------------------------------------------------------------------------
def test_forced_collision_two_devices():
    print("\n─── Test 1: Forced Collision (identical coords) ───")
    nodes = [
        _make_node("MM28", x=0.0, y=0.0, width=2.0),
        _make_node("MM5",  x=0.0, y=0.0, width=2.0),
        _make_node("MM3",  x=5.0, y=0.0, width=1.5),
    ]
    layout_context = {"nodes": nodes, "edges": [], "terminal_nets": {}, "gap_px": 0.0}

    baseline = run_drc_check(nodes)
    assert not baseline["pass"], "Expected DRC violation"
    
    orch = Orchestrator(run_llm_fn=_deterministic_llm, max_drc_retries=3, gap_px=0.0)
    _, constraint_text = orch.run_topology_analysis("Fix layout", layout_context)
    final_response = orch.continue_placement("Yes", layout_context, constraint_text)
    
    result_nodes = _apply_cmds_to_nodes(nodes, _extract_cmd_blocks(final_response))
    final_drc = run_drc_check(result_nodes)
    assert final_drc["pass"], f"FAILED: {final_drc['violations']}"
    print("  ✅ Test 1 PASSED")

# ---------------------------------------------------------------------------
# ═══ Test 2: Cascaded Collisions (3 devices at same spot) ═══
# ---------------------------------------------------------------------------
def test_cascaded_collisions():
    print("\n─── Test 2: Cascaded Collisions (3-way pile-up) ───")
    nodes = [
        _make_node("MA", x=0.0, y=0.0, width=1.5),
        _make_node("MB", x=0.0, y=0.0, width=1.5),
        _make_node("MC", x=0.0, y=0.0, width=1.5),
        _make_node("MD", x=6.0, y=0.0, width=1.0),
    ]
    layout_context = {"nodes": nodes, "edges": [], "terminal_nets": {}, "gap_px": 0.0}

    orch = Orchestrator(_deterministic_llm, max_drc_retries=4, gap_px=0.0)
    _, constraint_text = orch.run_topology_analysis("Fix", layout_context)
    final_response = orch.continue_placement("Yes", layout_context, constraint_text)
    
    result_nodes = _apply_cmds_to_nodes(nodes, _extract_cmd_blocks(final_response))
    final_drc = run_drc_check(result_nodes)
    assert final_drc["pass"], f"FAILED: {final_drc['violations']}"
    print("  ✅ Test 2 PASSED")

# ---------------------------------------------------------------------------
# ═══ Test 3: GAP violation ═══
# ---------------------------------------------------------------------------
def test_gap_violation():
    print("\n─── Test 3: GAP Violation ───")
    GAP_PX = 0.5
    nodes = [
        _make_node("P1", x=0.0, y=0.0, width=2.0),
        _make_node("P2", x=2.1, y=0.0, width=2.0),
    ]
    layout_context = {"nodes": nodes, "edges": [], "terminal_nets": {}, "gap_px": GAP_PX}

    orch = Orchestrator(_deterministic_llm, max_drc_retries=3, gap_px=GAP_PX)
    _, constraint_text = orch.run_topology_analysis("Fix", layout_context)
    final_response = orch.continue_placement("Yes", layout_context, constraint_text)
    
    result_nodes = _apply_cmds_to_nodes(nodes, _extract_cmd_blocks(final_response))
    final_drc = run_drc_check(result_nodes, gap_px=GAP_PX)
    assert final_drc["pass"], f"FAILED: {final_drc['violations']}"
    print("  ✅ Test 3 PASSED")

# ---------------------------------------------------------------------------
# ═══ Test 4: Mixed overlap + gap ═══
# ---------------------------------------------------------------------------
def test_mixed_violations():
    print("\n─── Test 4: Mixed Overlap + GAP ───")
    GAP_PX = 0.5
    nodes = [
        _make_node("X1", x=0.0, y=0.0, width=2.0),
        _make_node("X2", x=1.0, y=0.0, width=2.0),
        _make_node("X3", x=8.0, y=0.0, width=2.0),
        _make_node("X4", x=10.1, y=0.0, width=2.0),
    ]
    layout_context = {"nodes": nodes, "edges": [], "terminal_nets": {}, "gap_px": GAP_PX}

    orch = Orchestrator(_deterministic_llm, max_drc_retries=4, gap_px=GAP_PX)
    _, constraint_text = orch.run_topology_analysis("Fix", layout_context)
    final_response = orch.continue_placement("Yes", layout_context, constraint_text)
    
    result_nodes = _apply_cmds_to_nodes(nodes, _extract_cmd_blocks(final_response))
    final_drc = run_drc_check(result_nodes, gap_px=GAP_PX)
    assert final_drc["pass"], f"FAILED: {final_drc['violations']}"
    print("  ✅ Test 4 PASSED")

# ---------------------------------------------------------------------------
# ═══ Test 5: Already-clean layout ═══
# ---------------------------------------------------------------------------
def test_clean_layout_no_regression():
    print("\n─── Test 5: Already-clean layout — no regression ───")
    nodes = [
        _make_node("Q1", x=0.0, y=0.0, width=2.0),
        _make_node("Q2", x=2.0, y=0.0, width=2.0),
        _make_node("Q3", x=4.0, y=0.0, width=2.0),
    ]
    layout_context = {"nodes": nodes, "edges": [], "terminal_nets": {}, "gap_px": 0.0}

    orch = Orchestrator(_deterministic_llm, max_drc_retries=2, gap_px=0.0)
    _, constraint_text = orch.run_topology_analysis("Optimize", layout_context)
    final_response = orch.continue_placement("Yes", layout_context, constraint_text)
    
    result_nodes = _apply_cmds_to_nodes(nodes, _extract_cmd_blocks(final_response))
    final_drc = run_drc_check(result_nodes)
    assert final_drc["pass"], f"FAILED: {final_drc['violations']}"
    print("  ✅ Test 5 PASSED")

# ---------------------------------------------------------------------------
# ═══ Test 6: compute_prescriptive_fixes unit test ═══
# ---------------------------------------------------------------------------
def test_prescriptive_fixes_unit():
    print("\n─── Test 6: compute_prescriptive_fixes unit test ───")
    nodes = [
        _make_node("A", x=0.0, y=0.0, width=3.0),
        _make_node("B", x=1.0, y=0.0, width=3.0),
    ]
    drc = run_drc_check(nodes, gap_px=0.0)
    fixes = compute_prescriptive_fixes(drc, gap_px=0.0)
    
    fixed_nodes = _apply_cmds_to_nodes(nodes, fixes)
    drc2 = run_drc_check(fixed_nodes)
    assert drc2["pass"], f"Prescriptive fix didn't resolve: {drc2['violations']}"
    print("  ✅ Test 6 PASSED")

if __name__ == "__main__":
    tests = [
        test_prescriptive_fixes_unit,
        test_clean_layout_no_regression,
        test_gap_violation,
        test_forced_collision_two_devices,
        test_cascaded_collisions,
        test_mixed_violations,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"  ❌ FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"  💥 ERROR in {t.__name__}: {e}")
            import traceback; traceback.print_exc()
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed / {len(tests)} total")
    print(f"{'='*50}")
    sys.exit(0 if failed == 0 else 1)

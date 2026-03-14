"""
test_all_fixes.py
==================
Validates all bug fixes:
  - Bug #CRITICAL : Wrong .sp file selected (comp_fortest vs CM)
  - Bug #BUS      : MM8<0> bus notation not treated as finger
  - Bug #SPICE    : Direct SPICE parser reads CM netlist correctly
  - Bug #MIRROR   : 3-transistor mirror detected from CM netlist
  - Bug #DIODE    : MM0 correctly identified as diode-connected reference
  - Bug #RATIO    : output:ref ratio = 1:2 for nf=8 vs nf=16
  - Bug #NUMERIC  : Single numeric suffix not false-split
"""

import sys
import tempfile
import traceback
from pathlib import Path

# Project root on path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))


# ===========================================================================
# Shared SPICE content
# ===========================================================================
CM_SP_CONTENT = """\
*Custom Compiler Version V-2023.12-6
*Thu Mar 12 04:12:20 2026

*.SCALE METER
*.LDD

.subckt CM A B C gnd
*.PININFO A:I B:I C:I gnd:I
MM2 A C gnd gnd n08 l=0.014u nf=4 nfin=2
MM1 B C gnd gnd n08 l=0.014u nf=4 nfin=2
MM0 C C gnd gnd n08 l=0.014u nf=8 nfin=2
.ends CM
"""


def make_cm_sp(tmp_path: Path) -> Path:
    """Write CM netlist to a temp file and return its path."""
    sp_file = tmp_path / "Current_Mirror_CM.sp"
    sp_file.write_text(CM_SP_CONTENT)
    return sp_file


# ===========================================================================
# Test 1: _resolve_sp_file picks correct file by cell_name
# ===========================================================================
def test_resolve_sp_file_by_cell_name(tmp_path: Path):
    """_resolve_sp_file should prefer file matching cell_name=CM."""
    from ai_agent.llm_worker import _resolve_sp_file

    (tmp_path / "comp_fortest_comparator.sp").write_text("* comparator")
    (tmp_path / "Current_Mirror_CM.sp").write_text("* cm")

    result = _resolve_sp_file({"cell_name": "CM"}, tmp_path)

    assert result is not None, "Should find a .sp file"
    assert "Current_Mirror_CM" in result, (
        f"Expected Current_Mirror_CM.sp, got: {result}"
    )
    print(f"  PASS: matched to {Path(result).name}")


# ===========================================================================
# Test 2: _resolve_sp_file honours explicit path
# ===========================================================================
def test_resolve_sp_file_explicit_path(tmp_path: Path):
    """_resolve_sp_file should honour explicit sp_file_path."""
    from ai_agent.llm_worker import _resolve_sp_file

    sp = tmp_path / "my_custom.sp"
    sp.write_text("* custom")

    result = _resolve_sp_file({"sp_file_path": str(sp)}, tmp_path)

    assert result == str(sp), f"Expected {sp}, got {result}"
    print("  PASS: explicit path honoured")


# ===========================================================================
# Test 3: _resolve_sp_file falls back to most recently modified
# ===========================================================================
def test_resolve_sp_file_most_recent(tmp_path: Path):
    """_resolve_sp_file falls back to most recently modified file."""
    import time
    from ai_agent.llm_worker import _resolve_sp_file

    old_file = tmp_path / "comp_fortest_comparator.sp"
    old_file.write_text("* old")

    time.sleep(0.05)

    new_file = tmp_path / "Current_Mirror_CM.sp"
    new_file.write_text("* new")

    result = _resolve_sp_file({}, tmp_path)

    assert result is not None
    assert "Current_Mirror_CM" in result, (
        f"Should pick most recent file, got: {result}"
    )
    print(f"  PASS: most recent = {Path(result).name}")


# ===========================================================================
# Test 4: Bus notation not treated as fingers
# ===========================================================================
def test_bus_notation_not_finger():
    """MM8<0> and MM8<21> must NOT be grouped as fingers of MM8."""
    from ai_agent.finger_grouping import (
        extract_base_and_finger,
        group_fingers,
        is_finger_device,
    )

    base, num = extract_base_and_finger("MM8<0>")
    assert base == "MM8<0>", f"Expected MM8<0>, got {base!r}"
    assert num  == 0,        f"Expected finger=0, got {num}"

    base, num = extract_base_and_finger("MM8<21>")
    assert base == "MM8<21>", f"Expected MM8<21>, got {base!r}"
    assert num  == 0

    assert not is_finger_device("MM8<0>"),  "MM8<0> must not be a finger device"
    assert not is_finger_device("MM8<21>"), "MM8<21> must not be a finger device"

    nodes = [
        {"id": "MM8<0>",  "geometry": {"x": 0.0, "y": 0.0, "width": 0.294}},
        {"id": "MM8<1>",  "geometry": {"x": 0.3, "y": 0.0, "width": 0.294}},
        {"id": "MM8<21>", "geometry": {"x": 0.6, "y": 0.0, "width": 0.294}},
    ]
    groups = group_fingers(nodes)

    assert len(groups) == 3, (
        f"Expected 3 separate groups for bus devices, "
        f"got {len(groups)}: {list(groups.keys())}"
    )
    print("  PASS: bus notation kept as separate devices")


# ===========================================================================
# Test 5: Explicit finger patterns still work
# ===========================================================================
def test_explicit_finger_grouping():
    """MM2_f1..MM2_f4 must be grouped as MM2 with nf=4."""
    from ai_agent.finger_grouping import (
        group_fingers,
        aggregate_to_logical_devices,
    )

    nodes = [
        {
            "id":   f"MM2_f{i}",
            "type": "nmos",
            "geometry": {
                "x": (i - 1) * 0.294,
                "y": 0.0,
                "width": 0.294,
                "height": 1.0,
                "orientation": "R0",
            },
            "electrical": {"nf": "4", "l": "0.014u"},
        }
        for i in range(1, 5)
    ]

    groups = group_fingers(nodes)
    assert "MM2" in groups, (
        f"Expected MM2 group, got: {list(groups.keys())}"
    )
    assert len(groups["MM2"]) == 4, (
        f"Expected 4 fingers, got {len(groups['MM2'])}"
    )

    logical = aggregate_to_logical_devices(nodes)
    mm2     = next((n for n in logical if n["id"] == "MM2"), None)
    assert mm2 is not None, "MM2 logical device not found"
    assert mm2["electrical"]["nf"] == 4, (
        f"Expected nf=4, got {mm2['electrical']['nf']}"
    )
    print("  PASS: MM2_f1..f4 -> MM2 (nf=4)")


# ===========================================================================
# Test 6: Numeric suffix false-split prevention
# ===========================================================================
def test_numeric_suffix_false_split():
    """MM5_1 alone must NOT be split into base=MM5, finger=1."""
    from ai_agent.finger_grouping import group_fingers

    nodes = [
        {
            "id":   "MM5_1",
            "type": "nmos",
            "geometry": {"x": 0.0, "y": 0.0, "width": 0.294},
            "electrical": {},
        }
    ]

    groups = group_fingers(nodes)

    assert "MM5_1" in groups, (
        f"Expected MM5_1 kept as-is, "
        f"got keys: {list(groups.keys())}"
    )
    assert "MM5" not in groups, (
        f"MM5 should not exist as group key for single numeric device"
    )
    print("  PASS: MM5_1 not false-split")


# ===========================================================================
# Test 7: Direct SPICE parser reads CM netlist correctly
# ===========================================================================
def test_parse_spice_cm_netlist(tmp_path: Path):
    """_parse_spice_directly must correctly parse the CM netlist."""
    from ai_agent.topology_analyst import _parse_spice_directly

    sp_file = make_cm_sp(tmp_path)
    nets    = _parse_spice_directly(str(sp_file))

    assert "MM2" in nets, f"MM2 not found. Found: {list(nets.keys())}"
    assert "MM1" in nets, f"MM1 not found. Found: {list(nets.keys())}"
    assert "MM0" in nets, f"MM0 not found. Found: {list(nets.keys())}"

    assert nets["MM2"]["D"] == "A",   f"MM2 drain: expected A, got {nets['MM2']['D']}"
    assert nets["MM2"]["G"] == "C",   f"MM2 gate:  expected C, got {nets['MM2']['G']}"
    assert nets["MM2"]["S"] == "gnd", f"MM2 source: expected gnd"
    assert nets["MM1"]["D"] == "B",   f"MM1 drain: expected B"
    assert nets["MM1"]["G"] == "C",   f"MM1 gate:  expected C"
    assert nets["MM0"]["D"] == "C",   f"MM0 drain: expected C (diode)"
    assert nets["MM0"]["G"] == "C",   f"MM0 gate:  expected C (diode)"

    # Effective nf = nf * nfin
    # MM1, MM2: nf=4 * nfin=2 = 8
    # MM0:      nf=8 * nfin=2 = 16
    assert nets["MM2"]["nf"] == "8",  (
        f"MM2 effective nf: expected 8 (4*2), got {nets['MM2']['nf']}"
    )
    assert nets["MM1"]["nf"] == "8",  (
        f"MM1 effective nf: expected 8 (4*2), got {nets['MM1']['nf']}"
    )
    assert nets["MM0"]["nf"] == "16", (
        f"MM0 effective nf: expected 16 (8*2), got {nets['MM0']['nf']}"
    )

    print("  PASS: SPICE parser - all 3 devices with correct nets and nf")
    for dev in ("MM0", "MM1", "MM2"):
        n = nets[dev]
        print(f"    {dev}: D={n['D']} G={n['G']} S={n['S']} nf={n['nf']}")


# ===========================================================================
# Test 8: Mirror detection from CM netlist
# ===========================================================================
def test_mirror_detection_cm(tmp_path: Path):
    """analyze_topology must detect the 3-transistor NMOS mirror."""
    from ai_agent.topology_analyst import analyze_topology

    sp_file = make_cm_sp(tmp_path)
    result  = analyze_topology([], {}, str(sp_file))

    print("\n  --- analyze_topology output ---")
    for line in result.splitlines():
        print(f"  {line}")
    print("  --- end ---\n")

    result_upper = result.upper()

    assert "MIRROR" in result_upper, (
        "Expected MIRROR in topology output"
    )
    assert "MM0" in result, "MM0 not in topology output"
    assert "MM1" in result, "MM1 not in topology output"
    assert "MM2" in result, "MM2 not in topology output"

    assert any(
        marker in result
        for marker in ("gate=C", "gate-net=C", "G=C", "(C)")
    ), "Expected gate net C in topology output"

    print("  PASS: mirror detected with all 3 devices")


# ===========================================================================
# Test 9: MM0 identified as diode-connected reference
# ===========================================================================
def test_diode_reference_detection(tmp_path: Path):
    """MM0 (D=C, G=C) must be identified as diode-connected reference."""
    from ai_agent.topology_analyst import (
        _parse_spice_directly,
        _infer_mirrors_from_spice,
    )

    sp_file     = make_cm_sp(tmp_path)
    nets        = _parse_spice_directly(str(sp_file))
    constraints = _infer_mirrors_from_spice(nets, nodes=[])
    full_text   = "\n".join(constraints)

    print("\n  --- Mirror constraints ---")
    print(full_text)
    print("  ---\n")

    assert "MM0" in full_text, "MM0 must appear in mirror constraints"
    assert "diode" in full_text.lower(), (
        "Expected diode or diode-connected in constraints"
    )
    assert "[REF]" in full_text, (
        "Expected [REF] tag on diode-connected device MM0"
    )
    print("  PASS: MM0 correctly marked as [REF] diode-connected")


# ===========================================================================
# Test 10: Ratio reporting
# ===========================================================================
def test_ratio_mirror_reporting(tmp_path: Path):
    """
    Mirror output must report correct ratio.

    Circuit:
      MM0 ref  nf=16  (diode-connected, drain=C gate=C)
      MM1 out  nf=8   (drain=B gate=C)
      MM2 out  nf=8   (drain=A gate=C)

    Expected ratio (output:reference):
      MM1:MM0 = 8:16 = 1:2  (output carries half the reference current)
      MM2:MM0 = 8:16 = 1:2  (output carries half the reference current)

    The ratio string 1:2 must appear in the constraints.
    The string 1:1 must NOT appear (that was the old broken behaviour).
    """
    from ai_agent.topology_analyst import (
        _parse_spice_directly,
        _infer_mirrors_from_spice,
    )

    sp_file     = make_cm_sp(tmp_path)
    nets        = _parse_spice_directly(str(sp_file))
    constraints = _infer_mirrors_from_spice(nets, nodes=[])
    full_text   = "\n".join(constraints)

    print("\n  --- Ratio constraints ---")
    print(full_text)
    print("  ---\n")

    # Verify effective nf values first
    assert nets["MM0"]["nf"] == "16", (
        f"Pre-condition: MM0 nf must be 16, got {nets['MM0']['nf']}"
    )
    assert nets["MM1"]["nf"] == "8", (
        f"Pre-condition: MM1 nf must be 8, got {nets['MM1']['nf']}"
    )
    assert nets["MM2"]["nf"] == "8", (
        f"Pre-condition: MM2 nf must be 8, got {nets['MM2']['nf']}"
    )

    # Ratio 1:2 must appear
    assert "1:2" in full_text, (
        f"Expected ratio 1:2 in constraints (output nf=8 vs ref nf=16).\n"
        f"Got:\n{full_text}"
    )

    # Old broken value must NOT appear
    # (1:1 would mean gcd simplified both sides to 1 incorrectly)
    ratio_lines = [
        line for line in full_text.splitlines()
        if "Ratio" in line
    ]
    for line in ratio_lines:
        assert "1:1" not in line, (
            f"Ratio line shows 1:1 (broken) instead of 1:2:\n  {line}"
        )

    print(
        "  PASS: 1:2 ratio correctly reported "
        "for output(nf=8) vs reference(nf=16)"
    )


# ===========================================================================
# Test 11: No hallucinated devices from other circuits
# ===========================================================================
def test_no_hallucinated_devices(tmp_path: Path):
    """
    Topology output for CM netlist must NOT contain devices or nets
    from other circuits (MM5, MM6, MM8, MM9, VOUTN, VOUTP, CLK).
    """
    from ai_agent.topology_analyst import analyze_topology

    sp_file = make_cm_sp(tmp_path)
    result  = analyze_topology([], {}, str(sp_file))

    hallucinated = [
        "MM5", "MM6", "MM8", "MM9",
        "VOUTN", "VOUTP", "CLK"
    ]
    found = [h for h in hallucinated if h in result]

    assert len(found) == 0, (
        f"Hallucinated devices/nets in output: {found}\n"
        f"Full output:\n{result}"
    )
    print("  PASS: no hallucinated devices from other circuits")


# ===========================================================================
# Test Runner
# ===========================================================================
def run_all_tests():
    """Run all tests and report results."""

    standalone_tests = [
        test_bus_notation_not_finger,
        test_explicit_finger_grouping,
        test_numeric_suffix_false_split,
    ]

    tmp_tests = [
        test_resolve_sp_file_by_cell_name,
        test_resolve_sp_file_explicit_path,
        test_resolve_sp_file_most_recent,
        test_parse_spice_cm_netlist,
        test_mirror_detection_cm,
        test_diode_reference_detection,
        test_ratio_mirror_reporting,
        test_no_hallucinated_devices,
    ]

    passed = 0
    failed = 0

    print("=" * 60)
    print("Running all bug-fix tests")
    print("=" * 60)

    for test_fn in standalone_tests:
        name = test_fn.__name__
        print(f"\n[TEST] {name}")
        try:
            test_fn()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            traceback.print_exc()
            failed += 1

    for test_fn in tmp_tests:
        name = test_fn.__name__
        print(f"\n[TEST] {name}")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                test_fn(Path(tmp))
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 60)
    print(
        f"Results: {passed} passed, {failed} failed "
        f"out of {passed + failed} tests"
    )
    print("=" * 60)

    return failed


if __name__ == "__main__":
    failed = run_all_tests()
    sys.exit(1 if failed > 0 else 0)
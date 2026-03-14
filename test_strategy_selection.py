"""
test_strategy_selection.py
===========================
Tests for the new strategy-based placement mode selection.
"""
import os
import sys

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


def build_nodes():
    """Build 16 physical finger nodes for CM netlist."""
    nodes = []
    for dev, nf in [("MM2", 4), ("MM1", 4), ("MM0", 8)]:
        for i in range(1, nf + 1):
            nodes.append({
                "id": f"{dev}_f{i}",
                "type": "nmos",
                "is_dummy": False,
                "electrical": {"l": 1.4e-08, "nf": 1, "nfin": 2.0},
                "geometry": {
                    "x": 0.0, "y": 0.0,
                    "width": 0.294, "height": 0.668,
                    "orientation": "R0",
                },
            })
    return nodes


def get_pattern(nodes):
    """Get device pattern sorted by (y, x)."""
    s = sorted(nodes, key=lambda n: (n["geometry"]["y"], n["geometry"]["x"]))
    return [n["id"].rsplit("_f", 1)[0] for n in s]


def get_num_rows(nodes):
    """Count distinct y-values."""
    ys = set(round(float(n["geometry"]["y"]), 3) for n in nodes)
    return len(ys)


def test_parse_placement_mode():
    """Test 1: parse_placement_mode correctly maps user input."""
    print("=" * 60)
    print("TEST 1: parse_placement_mode")
    print("=" * 60)

    from ai_agent.strategy_selector import parse_placement_mode

    constraint = "MIRROR (NMOS, gate=C): MM2(nf=4) <-> MM1(nf=4) <-> MM0[REF](nf=8)"

    tests = [
        ("1", "interdigitated"),
        ("2", "common_centroid"),
        ("3", "auto"),
        ("yes", "auto"),
        ("all", "auto"),
        ("auto", "auto"),
        ("common centroid", "common_centroid"),
        ("common_centroid", "common_centroid"),
        ("common-centroid", "common_centroid"),
        ("interdigitated", "interdigitated"),
        ("Use interdigitation", "interdigitated"),
        ("optimize", "auto"),  # no keyword → auto
    ]

    for user_msg, expected in tests:
        result = parse_placement_mode(user_msg, constraint)
        status = "PASS" if result == expected else "FAIL"
        print(f"  {status}: '{user_msg}' → {result} (expected {expected})")
        assert result == expected, f"FAIL: got {result}"

    # No mirror → always auto
    result = parse_placement_mode("1", "NMOS row: MM0, MM1")
    assert result == "auto", f"FAIL: no mirror should return auto, got {result}"
    print("  PASS: No mirror → always 'auto'")

    print("  All parse tests passed\n")


def test_forced_interdigitated():
    """Test 2: force_mode='interdigitated' produces single-row."""
    print("=" * 60)
    print("TEST 2: Forced Interdigitated (single-row)")
    print("=" * 60)

    from ai_agent.pipeline_optimizer import apply_deterministic_optimizations
    from ai_agent.topology_analyst import _parse_spice_directly, analyze_topology

    sp_file = os.path.join(project_root, "netlists", "cm.sp")
    spice_nets = _parse_spice_directly(sp_file)
    devices = []
    for name, desc in spice_nets.items():
        d = desc.copy()
        d["id"] = name
        d["type"] = "NMOS" if desc["model"].lower().startswith("n") else "PMOS"
        devices.append(d)
    constraint_text = analyze_topology(devices, spice_nets, sp_file)

    nodes = build_nodes()
    result = apply_deterministic_optimizations(
        nodes, constraint_text, spice_nets, [],
        placement_mode="interdigitated",
    )

    num_rows = get_num_rows(result)
    pattern = get_pattern(result)
    transitions = sum(1 for i in range(1, len(pattern)) if pattern[i] != pattern[i - 1])

    print(f"  Rows: {num_rows} (should be 1)")
    print(f"  Transitions: {transitions} (should be > 2)")
    print(f"  Pattern: {' '.join(pattern)}")

    assert num_rows == 1, f"FAIL: Expected 1 row, got {num_rows}"
    assert transitions > 2, f"FAIL: Not interdigitated ({transitions} transitions)"
    print("  PASS: Forced interdigitated produces single-row interdigitated\n")


def test_forced_common_centroid():
    """Test 3: force_mode='common_centroid' produces multi-row."""
    print("=" * 60)
    print("TEST 3: Forced Common Centroid (multi-row)")
    print("=" * 60)

    from ai_agent.pipeline_optimizer import apply_deterministic_optimizations
    from ai_agent.topology_analyst import _parse_spice_directly, analyze_topology

    sp_file = os.path.join(project_root, "netlists", "cm.sp")
    spice_nets = _parse_spice_directly(sp_file)
    devices = []
    for name, desc in spice_nets.items():
        d = desc.copy()
        d["id"] = name
        d["type"] = "NMOS" if desc["model"].lower().startswith("n") else "PMOS"
        devices.append(d)
    constraint_text = analyze_topology(devices, spice_nets, sp_file)

    nodes = build_nodes()
    result = apply_deterministic_optimizations(
        nodes, constraint_text, spice_nets, [],
        placement_mode="common_centroid",
    )

    num_rows = get_num_rows(result)
    pattern = get_pattern(result)
    transitions = sum(1 for i in range(1, len(pattern)) if pattern[i] != pattern[i - 1])

    print(f"  Rows: {num_rows} (should be > 1)")
    print(f"  Transitions: {transitions} (should be > 2)")
    print(f"  Pattern: {' '.join(pattern)}")

    assert num_rows > 1, f"FAIL: Expected multiple rows, got {num_rows}"
    assert transitions > 2, f"FAIL: Not interdigitated ({transitions} transitions)"
    print("  PASS: Forced common centroid produces multi-row placement\n")


def main():
    print("\n" + "=" * 60)
    print("STRATEGY SELECTION TEST SUITE")
    print("=" * 60 + "\n")

    passed = 0
    failed = 0

    for test_fn in [test_parse_placement_mode, test_forced_interdigitated, test_forced_common_centroid]:
        try:
            test_fn()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed, {passed + failed} total")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

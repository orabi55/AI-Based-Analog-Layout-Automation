"""
test_common_centroid.py
========================
Self-contained test for common-centroid / interdigitated placement
of the CM netlist:

  MM2 A C gnd gnd n08 l=0.014u nf=4 nfin=2
  MM1 B C gnd gnd n08 l=0.014u nf=4 nfin=2
  MM0 C C gnd gnd n08 l=0.014u nf=8 nfin=2

Expected:
  - nf values: MM2=4, MM1=4, MM0=8  (nfin=2 does NOT multiply)
  - Total: 16 fingers
  - needs_interdigitation -> True (ratio mirror, 4 vs 8)
  - Placement: 16 finger positions, symmetric interdigitation
  - Centroid of MM1 == Centroid of MM2 (matched)
  - All 16 fingers placed exactly once
"""

import json
import os
import sys

# Ensure project root is on path
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


def test_nf_parsing():
    """Test 1: nf is parsed correctly (not multiplied by nfin)."""
    print("=" * 60)
    print("TEST 1: nf Parsing (nfin must NOT multiply)")
    print("=" * 60)

    from ai_agent.topology_analyst import _parse_spice_directly

    sp_file = os.path.join(project_root, "netlists", "cm.sp")
    spice_nets = _parse_spice_directly(sp_file)

    assert spice_nets, "FAIL: _parse_spice_directly returned empty dict"

    # Check each device
    for dev_id in ["MM0", "MM1", "MM2"]:
        assert dev_id in spice_nets, f"FAIL: {dev_id} not in spice_nets"

    mm0_nf = int(spice_nets["MM0"]["nf"])
    mm1_nf = int(spice_nets["MM1"]["nf"])
    mm2_nf = int(spice_nets["MM2"]["nf"])

    print(f"  MM0: nf={mm0_nf}  (expected 8)")
    print(f"  MM1: nf={mm1_nf}  (expected 4)")
    print(f"  MM2: nf={mm2_nf}  (expected 4)")

    assert mm0_nf == 8, f"FAIL: MM0 nf={mm0_nf}, expected 8"
    assert mm1_nf == 4, f"FAIL: MM1 nf={mm1_nf}, expected 4"
    assert mm2_nf == 4, f"FAIL: MM2 nf={mm2_nf}, expected 4"

    # Verify nfin is stored but NOT multiplied into nf
    assert spice_nets["MM0"].get("nfin") == "2", \
        f"FAIL: MM0 nfin={spice_nets['MM0'].get('nfin')}, expected '2'"

    print("  PASS: nf is layout finger count only (nfin not multiplied)")
    print()
    return spice_nets


def test_logical_device_aggregation():
    """Test 2: Physical finger nodes aggregate correctly."""
    print("=" * 60)
    print("TEST 2: Logical Device Aggregation")
    print("=" * 60)

    from ai_agent.finger_grouping import aggregate_to_logical_devices

    # Simulate the JSON nodes (physical fingers)
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
                    "orientation": "R0"
                },
            })

    logical = aggregate_to_logical_devices(nodes)
    logical_map = {ld["id"]: ld for ld in logical}

    print(f"  Input: {len(nodes)} physical finger nodes")
    print(f"  Output: {len(logical)} logical devices")

    for dev_id in ["MM0", "MM1", "MM2"]:
        assert dev_id in logical_map, f"FAIL: {dev_id} not aggregated"
        ld = logical_map[dev_id]
        fingers = ld.get("_fingers", [ld["id"]])
        print(f"  {dev_id}: {len(fingers)} fingers -> {fingers}")

    assert len(logical_map["MM0"].get("_fingers", [])) == 8, \
        f"FAIL: MM0 expected 8 fingers"
    assert len(logical_map["MM1"].get("_fingers", [])) == 4, \
        f"FAIL: MM1 expected 4 fingers"
    assert len(logical_map["MM2"].get("_fingers", [])) == 4, \
        f"FAIL: MM2 expected 4 fingers"

    print("  PASS: Logical devices have correct _fingers lists")
    print()
    return logical


def test_needs_interdigitation(logical_devices):
    """Test 3: needs_interdigitation returns True for this mirror."""
    print("=" * 60)
    print("TEST 3: needs_interdigitation Decision")
    print("=" * 60)

    from ai_agent.placement_specialist import needs_interdigitation

    result = needs_interdigitation(logical_devices, {})
    print(f"  needs_interdigitation = {result}")

    assert result is True, \
        "FAIL: needs_interdigitation should return True for ratio mirror"

    print("  PASS: Correctly identifies need for interdigitation")
    print()


def test_interdigitated_placement(logical_devices, spice_nets):
    """Test 4: Interdigitated placement produces correct output."""
    print("=" * 60)
    print("TEST 4: Interdigitated Placement Output")
    print("=" * 60)

    from ai_agent.placement_specialist import compute_mirror_placement

    placements = compute_mirror_placement(
        mirror_logical_devices=logical_devices,
        spice_nets=spice_nets,
    )

    print(f"  Total placements: {len(placements)}")
    assert len(placements) == 16, \
        f"FAIL: Expected 16 placements, got {len(placements)}"

    # Check all fingers placed exactly once
    placed_ids = [p["finger_id"] for p in placements]
    expected_ids = set()
    for dev, nf in [("MM0", 8), ("MM1", 4), ("MM2", 4)]:
        for i in range(1, nf + 1):
            expected_ids.add(f"{dev}_f{i}")

    placed_set = set(placed_ids)
    missing = expected_ids - placed_set
    extra = placed_set - expected_ids
    duplicates = len(placed_ids) - len(placed_set)

    assert not missing, f"FAIL: Missing fingers: {missing}"
    assert not extra, f"FAIL: Extra fingers: {extra}"
    assert duplicates == 0, f"FAIL: {duplicates} duplicate placements"

    print("  PASS: All 16 fingers placed exactly once, no duplicates")

    # Print the pattern
    pattern = [p["dev_id"] for p in placements]
    print(f"\n  Interdigitation pattern:")
    print(f"    {' '.join(pattern)}")

    # Verify pattern is NOT grouped
    # A grouped pattern would be: MM2 MM2 MM2 MM2 MM1 MM1 MM1 MM1 MM0 ...
    # Interdigitated should have alternating devices
    grouped = True
    last_dev = pattern[0]
    transitions = 0
    for dev in pattern[1:]:
        if dev != last_dev:
            transitions += 1
            last_dev = dev
    # Grouped would have exactly 2 transitions (MM2->MM1->MM0 or similar)
    # Interdigitated should have many more
    print(f"  Device transitions: {transitions} (grouped would be ~2)")
    assert transitions > 2, \
        f"FAIL: Pattern looks grouped ({transitions} transitions). Should be interdigitated."
    print("  PASS: Pattern is interdigitated (not grouped)")

    # Check centroid matching
    print("\n  Centroid verification:")
    dev_x_positions = {}
    for p in placements:
        dev_id = p["dev_id"]
        if dev_id not in dev_x_positions:
            dev_x_positions[dev_id] = []
        dev_x_positions[dev_id].append(p["x"])

    centroids = {}
    for dev_id, xs in dev_x_positions.items():
        centroid = sum(xs) / len(xs)
        centroids[dev_id] = centroid
        print(f"    {dev_id}: centroid_x = {centroid:.4f}  (from {len(xs)} fingers)")

    # MM1 and MM2 centroids should be equal (matched outputs)
    mm1_cx = centroids.get("MM1", 0)
    mm2_cx = centroids.get("MM2", 0)
    diff = abs(mm1_cx - mm2_cx)
    print(f"\n    |MM1_centroid - MM2_centroid| = {diff:.6f}")

    assert diff < 0.001, \
        f"FAIL: MM1 and MM2 centroids differ by {diff:.6f} (should be < 0.001)"
    print("  PASS: MM1 and MM2 centroids match (good matching)")

    # Print full placement table
    print("\n  Full placement table:")
    print(f"    {'finger_id':<12} {'dev_id':<6} {'x':>8} {'y':>8} {'row':>4}")
    print(f"    {'-'*12} {'-'*6} {'-'*8} {'-'*8} {'-'*4}")
    for p in placements:
        print(
            f"    {p['finger_id']:<12} {p['dev_id']:<6} "
            f"{p['x']:>8.3f} {p['y']:>8.3f} {p['row_idx']:>4}"
        )

    print()
    return placements


def test_pipeline_integration():
    """Test 5: Full pipeline integration test."""
    print("=" * 60)
    print("TEST 5: Pipeline Integration (pipeline_optimizer)")
    print("=" * 60)

    from ai_agent.topology_analyst import _parse_spice_directly, analyze_topology
    from ai_agent.finger_grouping import aggregate_to_logical_devices
    from ai_agent.pipeline_optimizer import apply_deterministic_optimizations

    sp_file = os.path.join(project_root, "netlists", "cm.sp")
    spice_nets = _parse_spice_directly(sp_file)

    # Build device list for topology analysis  
    devices = []
    for name, desc in spice_nets.items():
        d = desc.copy()
        d['id'] = name
        d['name'] = name
        if 'type' not in d:
            d['type'] = 'NMOS' if desc['model'].lower().startswith('n') else 'PMOS'
        devices.append(d)

    # Get topology constraints
    constraint_text = analyze_topology(devices, spice_nets, sp_file)
    print(f"  Topology constraints generated ({len(constraint_text)} chars)")
    
    # Check that "MIRROR" appears in constraints
    assert "MIRROR" in constraint_text.upper(), \
        "FAIL: No MIRROR found in topology constraints"
    print("  PASS: MIRROR detected in topology constraints")

    # Build physical nodes (as they would come from JSON)
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
                    "orientation": "R0"
                },
            })

    # Run the pipeline
    result_nodes = apply_deterministic_optimizations(
        working_nodes=nodes,
        constraint_text=constraint_text,
        terminal_nets=spice_nets,
        edges=[],
    )

    assert len(result_nodes) == 16, \
        f"FAIL: Expected 16 result nodes, got {len(result_nodes)}"

    # Check that positions are NOT all at x=0, y=0
    x_values = [n["geometry"]["x"] for n in result_nodes]
    unique_x = set(x_values)
    print(f"  Unique x-positions: {len(unique_x)}")
    assert len(unique_x) > 1, \
        "FAIL: All nodes at same x-position (placement not applied)"

    # Check interdigitation: nodes should not be grouped by device
    # Get pattern by sorting by x then checking device order
    sorted_nodes = sorted(result_nodes, key=lambda n: (n["geometry"]["y"], n["geometry"]["x"]))
    pattern = []
    for n in sorted_nodes:
        # Extract base device name from finger ID
        dev_id = n["id"].rsplit("_f", 1)[0] if "_f" in n["id"] else n["id"]
        pattern.append(dev_id)

    transitions = 0
    for i in range(1, len(pattern)):
        if pattern[i] != pattern[i-1]:
            transitions += 1

    print(f"  Device transitions in result: {transitions}")
    print(f"  Pattern: {' '.join(pattern)}")

    assert transitions > 2, \
        f"FAIL: Result still looks grouped ({transitions} transitions)"
    print("  PASS: Pipeline produces interdigitated result")
    print()


def main():
    print("\n" + "=" * 60)
    print("COMMON CENTROID / INTERDIGITATION TEST SUITE")
    print("=" * 60 + "\n")

    passed = 0
    failed = 0

    # Test 1: nf parsing
    try:
        spice_nets = test_nf_parsing()
        passed += 1
    except AssertionError as e:
        print(f"  FAIL: {e}")
        failed += 1
        spice_nets = None

    # Test 2: Logical device aggregation
    try:
        logical_devices = test_logical_device_aggregation()
        passed += 1
    except AssertionError as e:
        print(f"  FAIL: {e}")
        failed += 1
        logical_devices = None

    # Test 3: needs_interdigitation
    if logical_devices:
        try:
            test_needs_interdigitation(logical_devices)
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {e}")
            failed += 1

    # Test 4: Interdigitated placement
    if logical_devices and spice_nets:
        try:
            test_interdigitated_placement(logical_devices, spice_nets)
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {e}")
            failed += 1

    # Test 5: Pipeline integration
    try:
        test_pipeline_integration()
        passed += 1
    except AssertionError as e:
        print(f"  FAIL: {e}")
        failed += 1
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback
        traceback.print_exc()
        failed += 1

    # Summary
    print("=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed, {passed + failed} total")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

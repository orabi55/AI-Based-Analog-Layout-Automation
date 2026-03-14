"""
test_e2e_pipeline.py
====================
End-to-end test that simulates what orchestrator.continue_placement does.
This will tell us exactly where the interdigitation gets lost.
"""
import copy
import os
import sys

project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from ai_agent.topology_analyst import analyze_topology, _parse_spice_directly
from ai_agent.finger_grouping import aggregate_to_logical_devices
from ai_agent.pipeline_optimizer import apply_deterministic_optimizations


def build_physical_nodes():
    """Build physical finger nodes as they would come from JSON."""
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
    return nodes


def get_device_pattern(nodes):
    """Get the placement pattern sorted by x-position."""
    sorted_nodes = sorted(nodes, key=lambda n: (n["geometry"]["y"], n["geometry"]["x"]))
    return [n["id"].rsplit("_f", 1)[0] if "_f" in n["id"] else n["id"] for n in sorted_nodes]


def main():
    sp_file = os.path.join(project_root, "netlists", "cm.sp")
    
    # Step 1: Parse SPICE and get topology constraints
    print("=" * 60)
    print("STEP 1: Topology Analysis")
    print("=" * 60)
    spice_nets = _parse_spice_directly(sp_file)
    
    devices = []
    for name, desc in spice_nets.items():
        d = desc.copy()
        d['id'] = name
        d['name'] = name
        if 'type' not in d:
            d['type'] = 'NMOS' if desc['model'].lower().startswith('n') else 'PMOS'
        devices.append(d)
    
    constraint_text = analyze_topology(devices, spice_nets, sp_file)
    print(f"  Constraint text ({len(constraint_text)} chars):")
    for line in constraint_text.splitlines():
        print(f"    {line}")
    
    has_mirror = "MIRROR" in constraint_text.upper()
    print(f"\n  Contains 'MIRROR': {has_mirror}")
    assert has_mirror, "FAIL: No MIRROR in constraint_text!"

    # Step 2: Build physical nodes (simulating JSON load)
    print("\n" + "=" * 60)
    print("STEP 2: Build Physical Nodes (simulating JSON)")
    print("=" * 60)
    nodes = build_physical_nodes()
    print(f"  {len(nodes)} physical finger nodes")
    
    # Step 3: Simulate Stage 2 LLM output (grouped placement)
    print("\n" + "=" * 60)
    print("STEP 3: Simulate Stage 2 LLM Output (grouped)")
    print("=" * 60)
    # LLM would place them grouped: MM2_f1..f4, MM1_f1..f4, MM0_f1..f8
    pitch = 0.294
    x = 0.0
    for n in nodes:
        n["geometry"]["x"] = round(x, 3)
        n["geometry"]["y"] = 0.0
        x += pitch
    
    pattern = get_device_pattern(nodes)
    print(f"  After LLM Stage 2: {' '.join(pattern)}")
    
    # Step 4: Run deterministic optimizer (Stage 2.5)
    print("\n" + "=" * 60)
    print("STEP 4: Deterministic Optimizer (Stage 2.5)")
    print("=" * 60)
    
    pre_positions = {n["id"]: n["geometry"]["x"] for n in nodes}
    
    result_nodes = apply_deterministic_optimizations(
        working_nodes=nodes,
        constraint_text=constraint_text,
        terminal_nets=spice_nets,
        edges=[],
    )
    
    post_positions = {n["id"]: n["geometry"]["x"] for n in result_nodes}
    
    changed = {k: (pre_positions[k], post_positions[k]) 
               for k in pre_positions if pre_positions[k] != post_positions[k]}
    
    if changed:
        print(f"\n  CHANGED {len(changed)} positions:")
        for dev_id, (old, new) in list(changed.items())[:5]:
            print(f"    {dev_id}: x={old} -> x={new}")
    else:
        print("\n  *** NO POSITIONS CHANGED ***")
        print("  This means the deterministic optimizer is NOT applying interdigitation!")
    
    pattern = get_device_pattern(result_nodes)
    print(f"\n  Final pattern: {' '.join(pattern)}")
    
    # Check if interdigitated
    transitions = sum(1 for i in range(1, len(pattern)) if pattern[i] != pattern[i-1])
    print(f"  Transitions: {transitions} (grouped=2, interdigitated=many)")
    
    if transitions <= 2:
        print("\n  FAIL: Still grouped! Interdigitation not applied.")
        sys.exit(1)
    else:
        print(f"\n  PASS: Interdigitated ({transitions} transitions)")


if __name__ == "__main__":
    main()

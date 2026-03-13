import asyncio
import os
import sys

from ai_agent.topology_analyst import analyze_topology, _parse_spice_directly
from ai_agent.finger_grouping import aggregate_to_logical_devices, expand_logical_to_fingers
from ai_agent.pipeline_optimizer import _parse_mirror_groups

def main():
    sp_file = "netlists/cm.sp"
    print(f"Testing {sp_file}")
    
    # 1. Parsing SPICE Nets
    spice_nets = _parse_spice_directly(sp_file)
    print("\n--- SPICE Nets ---")
    for net, desc in spice_nets.items():
        print(desc)
    
    # 2. Topology Analysis
    devices = []
    for name, desc in spice_nets.items():
        if desc.get('model', '').lower().startswith(('n', 'p')):
            d = desc.copy()
            d['id'] = name
            d['name'] = name
            if 'type' not in d:
                d['type'] = 'NMOS' if desc['model'].lower().startswith('n') else 'PMOS'
            devices.append(d)
    topology_constraints = analyze_topology(devices, spice_nets, sp_file)
    print("\n--- Topology Constraints ---")
    print(topology_constraints)
    
    # logical grouping
    logical_devices = aggregate_to_logical_devices(devices)
    print("\n--- Logical Devices ---")
    
    # -------------------------------------------------------------
    # SIMULATE THE BEHAVIOR OF THE REAL SYSTEM WHERE nodes COME FROM JSON
    # If the JSON already has MM0_f1 .. MM0_f8, then `aggregate_to_logical_devices`
    # will populate `_fingers` with the array lengths matching `nf`.
    # -------------------------------------------------------------
    for ld in logical_devices:
        sp_net = spice_nets.get(ld['id'], {})
        nf = int(sp_net.get('nf', 1))
        if nf > 1:
            ld['_fingers'] = [f"{ld['id']}_f{i+1}" for i in range(nf)]
            ld['_is_logical'] = True
    # -------------------------------------------------------------

    for logical_device in logical_devices:
        fingers = logical_device.get('_fingers', [])
        print(f"Logical Device: {logical_device['id']}, Fingers: {fingers}")

    # 3. Placement Specialist (Mocking it lightly, or just calling optimizer)
    nodes = logical_devices
    # Apply common centroid extraction
    mirror_groups = _parse_mirror_groups(topology_constraints, nodes)
    print("\n--- Mirror Groups ---")
    print(mirror_groups)
    
    # Apply placement directly
    # To run _apply_common_centroid_if_needed, we need nodes and edges.
    nodes = []
    
    for dev in logical_devices:
        dev_copy = dev.copy()
        nodes.append(dev_copy)
        
    for gate_net, mgr in mirror_groups.items():
        dev_ids = mgr['dev_ids']
        dev_type = mgr['dev_type']
        mirror_devs = [n for n in nodes if n['id'] in dev_ids]
        print(f"Checking mirror logic for: gate={gate_net}, devs={dev_ids}, found_devs={[d['id'] for d in mirror_devs]}")
        # Test needs_common_centroid
        from ai_agent.placement_specialist import needs_common_centroid, compute_common_centroid_placement
        if needs_common_centroid(mirror_devs, spice_nets):
            print("NEEDS COMMON CENTROID: TRUE")
            placements = compute_common_centroid_placement(mirror_devs, spice_nets)
            print("PLACEMENTS:")
            for p in placements:
                print(p)
        else:
            print("NEEDS COMMON CENTROID: FALSE")
            
if __name__ == "__main__":
    main()

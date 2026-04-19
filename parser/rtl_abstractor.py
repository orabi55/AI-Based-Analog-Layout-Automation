"""
rtl_abstractor.py  --  Final Hierarchical Block Schema
======================================================

Produces a TRULY HIERARCHICAL JSON where:
  - "blocks" is the TOP-LEVEL interactive unit
  - Each block's "members" array nests device objects with relative_offsets
  - There are ZERO individual finger nodes in output
  - Unmatched devices appear in a separate "devices" dict (free-floating)

The AI/GUI sees blocks as rigid rectangles. Fingers are invisible.
"""
import json
import sys
from collections import defaultdict


def abstract_layout(input_file, output_file):
    with open(input_file, 'r') as f:
        data = json.load(f)

    # ── 1. SCAN NODES: aggregate into parent devices ─────────────────
    raw_devices = {}           # parent -> accumulator
    finger_positions = defaultdict(list)  # parent -> [{finger_index, x, y}]

    for node in data.get('nodes', []):
        parent = node['electrical'].get('parent')
        if not parent:
            continue

        if parent not in raw_devices:
            raw_devices[parent] = {
                "type": node['type'],
                "l": node['electrical'].get('l', 0.0),
                "nfin": node['electrical'].get('nfin', 0.0),
                "nf": 0,
                "min_x": float('inf'),
                "min_y": float('inf'),
                "max_x_w": float('-inf'),
                "max_h": float('-inf'),
                "orientation": node['geometry'].get('orientation', 'R0'),
                "finger_ids": [],
                "finger_width": node['geometry']['width']
            }

        rd = raw_devices[parent]
        rd["nf"] += 1
        rd["finger_ids"].append(node['id'])

        g = node['geometry']
        rd["min_x"] = min(rd["min_x"], g['x'])
        rd["min_y"] = min(rd["min_y"], g['y'])
        rd["max_x_w"] = max(rd["max_x_w"], g['x'] + g['width'])
        rd["max_h"] = max(rd["max_h"], g['height'])

        finger_positions[parent].append({
            "finger_index": node['electrical'].get('finger_index', 0),
            "x": g['x'],
            "y": g['y']
        })

    # ── 2. TERMINAL NET CONSOLIDATION ────────────────────────────────
    terminal_nets_raw = data.get('terminal_nets', {})
    device_nets = {}

    if terminal_nets_raw:
        for parent, rd in raw_devices.items():
            first = rd["finger_ids"][0]
            if first in terminal_nets_raw:
                device_nets[parent] = dict(terminal_nets_raw[first])
    else:
        # Infer from edges
        fid_to_parent = {}
        for parent, rd in raw_devices.items():
            for fid in rd["finger_ids"]:
                fid_to_parent[fid] = parent

        parent_net_set = defaultdict(set)
        for edge in data.get('edges', []):
            for end in ('source', 'target'):
                p = fid_to_parent.get(edge[end])
                if p:
                    parent_net_set[p].add(edge['net'])

        for parent in raw_devices:
            nets = sorted(parent_net_set.get(parent, set()))
            labels = ['G', 'D', 'S', 'B']
            device_nets[parent] = {
                labels[i] if i < len(labels) else f"T{i}": n
                for i, n in enumerate(nets)
            }

    # ── 3. BUILD DEVICE OBJECTS (with relative_offsets) ───────────────
    devices_built = {}
    for parent, rd in raw_devices.items():
        anchor_x = rd["min_x"]
        anchor_y = rd["min_y"]
        bbox_w = round(rd["max_x_w"] - anchor_x, 6)
        bbox_h = round(rd["max_h"], 6)

        fingers_sorted = sorted(finger_positions[parent],
                                key=lambda f: f["finger_index"])
        rel_offsets = [
            {
                "finger_index": f["finger_index"],
                "dx": round(f["x"] - anchor_x, 6),
                "dy": round(f["y"] - anchor_y, 6)
            }
            for f in fingers_sorted
        ]

        devices_built[parent] = {
            "device_id": parent,
            "type": rd["type"],
            "params": {
                "l": rd["l"],
                "nf": rd["nf"],
                "nfin": rd["nfin"]
            },
            "nets": device_nets.get(parent, {}),
            "geometry": {
                "x": round(anchor_x, 6),
                "y": round(anchor_y, 6),
                "w": bbox_w,
                "h": bbox_h,
                "orientation": rd["orientation"]
            },
            "fingers": rd["nf"],
            "relative_offsets": rel_offsets,
            "finger_width": rd["finger_width"]
        }

    # ── 4. TOPOLOGY INFERENCE → BLOCK GROUPING ───────────────────────
    # Detect current mirrors (shared G+S)
    gs_groups = defaultdict(list)
    for p, d in devices_built.items():
        nets = d["nets"]
        g = nets.get("G")
        s = nets.get("S")
        if g and s:
            gs_groups[(g, s)].append(p)

    topologies = []
    for (g_net, s_net), members in gs_groups.items():
        if len(members) > 1:
            topologies.append({
                "name": f"mirror_{g_net}_{s_net}",
                "type": f"current_mirror_{devices_built[members[0]]['type']}",
                "members": sorted(members)
            })

    # Build blocks for each topology group
    blocks = []
    assigned = set()
    block_idx = 1

    for topo in topologies:
        members = sorted(topo["members"])
        if len(members) < 2:
            continue

        # Global bounding box for the entire block
        bx_min = min(devices_built[m]["geometry"]["x"] for m in members)
        by_min = min(devices_built[m]["geometry"]["y"] for m in members)
        bx_max = max(devices_built[m]["geometry"]["x"] +
                     devices_built[m]["geometry"]["w"] for m in members)
        by_max = max(devices_built[m]["geometry"]["y"] +
                     devices_built[m]["geometry"]["h"] for m in members)

        block_id = f"Matched_Group_{block_idx:02d}"

        # Build member list with relative_offsets INSIDE the block
        block_members = []
        for m in members:
            d = devices_built[m]
            block_members.append({
                "device_id": m,
                "type": d["type"],
                "fingers": d["fingers"],
                "params": d["params"],
                "nets": d["nets"],
                "relative_offsets": d["relative_offsets"],
                "device_geometry": d["geometry"]
            })

        blocks.append({
            "id": block_id,
            "type": "common_centroid",
            "status": "LOCKED",
            "behavior": "rigid",
            "geometry": {
                "x": round(bx_min, 6),
                "y": round(by_min, 6),
                "w": round(bx_max - bx_min, 6),
                "h": round(by_max - by_min, 6)
            },
            "members": block_members,
            "constraints": {
                "keep_internal_routing": True,
                "allow_interdigitation_change": False,
                "allow_rotation": False
            }
        })

        for m in members:
            assigned.add(m)
        block_idx += 1

    # Unmatched devices stay as free-floating top-level entries
    free_devices = {}
    for p, d in devices_built.items():
        if p not in assigned:
            free_devices[p] = d

    # ── 5. BUILD OUTPUT ──────────────────────────────────────────────
    output = {
        "metadata": {
            "pdk": "generic",
            "abstraction_level": "block",
            "selection_rule": (
                "Individual fingers are read-only. "
                "Only Parent Blocks are interactive. "
                "Selection(finger) = Selection(parent_device) = "
                "Selection(parent_block)."
            ),
            "matching_protection": (
                "Any block with status LOCKED and behavior rigid "
                "is a pre-assembled matched group. Moving the block "
                "moves all contained devices and fingers as one unit. "
                "The AI is STRICTLY FORBIDDEN from moving individual "
                "devices or fingers within a LOCKED block."
            )
        },
        "blocks": blocks,
        "devices": free_devices,
        "topologies": topologies
    }

    with open(output_file, 'w') as f:
        json.dump(output, f, indent=2)

    # ── 6. SUMMARY ───────────────────────────────────────────────────
    print("--- Hierarchical Block Abstraction Complete ---")
    print(f"  Input  : {len(data.get('nodes', []))} finger nodes")
    print(f"  Output : {len(blocks)} LOCKED blocks, "
          f"{len(free_devices)} free devices, "
          f"{len(topologies)} topologies")
    print(f"  Zero individual finger nodes in output.")
    print()

    for b in blocks:
        mids = [m['device_id'] for m in b['members']]
        total_f = sum(m['fingers'] for m in b['members'])
        print(f"  {b['id']}: status={b['status']} behavior={b['behavior']}")
        print(f"    members: {mids} ({total_f} total fingers)")
        print(f"    bbox: ({b['geometry']['x']:.3f}, "
              f"{b['geometry']['y']:.3f}, "
              f"w={b['geometry']['w']:.3f}, "
              f"h={b['geometry']['h']:.3f})")

    if free_devices:
        print()
        for p, d in sorted(free_devices.items()):
            print(f"  Free: {p} ({d['type']}, nf={d['params']['nf']})")

    print(f"\n  Written to: {output_file}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python rtl_abstractor.py <input.json> <output.json>")
        sys.exit(1)
    abstract_layout(sys.argv[1], sys.argv[2])

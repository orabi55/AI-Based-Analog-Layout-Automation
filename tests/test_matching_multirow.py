"""Smoke test for matching adapter + multi-row geometry engine."""
from ai_agent.ai_chat_bot.agents.matching_adapter import apply_matching, parse_matching_requests
from ai_agent.ai_chat_bot.agents.geometry_engine import convert_multirow_to_geometry
from collections import defaultdict

print("=" * 60)
print("TEST 1: Common Centroid 1D matching")
print("=" * 60)

nodes = []
for parent, dev_type in [('MM0', 'nmos'), ('MM1', 'nmos')]:
    for f in range(1, 5):
        nodes.append({
            'id': f'{parent}_f{f}',
            'type': dev_type,
            'geometry': {'x': 0, 'y': 0, 'width': 0.294, 'height': 0.668},
            'electrical': {'nf': 4, 'parent': parent},
        })

dev_ids = [n['id'] for n in nodes]
placed, block = apply_matching(nodes, dev_ids, 'COMMON_CENTROID_1D', anchor_x=0.0, anchor_y=0.0)

for p in placed:
    bid = p.get("_matched_block", "?")
    print(f"  {p['id']:10s}  x={p['geometry']['x']:.3f}  y={p['geometry']['y']:.3f}  block={bid}")

# Verify centroids match
centroids = defaultdict(list)
for p in placed:
    parent = p['id'].rsplit('_f', 1)[0]
    centroids[parent].append(p['geometry']['x'])
for par, xs in centroids.items():
    cx = sum(xs) / len(xs)
    print(f"  {par} centroid_x = {cx:.4f}")

cx_mm0 = sum(centroids['MM0']) / len(centroids['MM0'])
cx_mm1 = sum(centroids['MM1']) / len(centroids['MM1'])
print(f"  Centroid match: {'PASS' if abs(cx_mm0 - cx_mm1) < 0.01 else 'FAIL'}")

print()
print("=" * 60)
print("TEST 2: Interdigitation")
print("=" * 60)

placed2, block2 = apply_matching(nodes, dev_ids, 'INTERDIGITATION', anchor_x=0.0, anchor_y=0.0)
for p in placed2:
    print(f"  {p['id']:10s}  x={p['geometry']['x']:.3f}")
print(f"  Block members: {len(block2.member_ids)}")

print()
print("=" * 60)
print("TEST 3: Parse matching requests from strategy")
print("=" * 60)

strat = '''Strategy: Use CC for diff pair.
```json
{"match_groups": [{"devices": ["MM0", "MM1"], "technique": "common_centroid_1d"}]}
```'''
reqs = parse_matching_requests(strat, nodes)
print(f"  Parsed {len(reqs)} request(s)")
for r in reqs:
    print(f"    parents={r['parent_ids']}, tech={r['technique']}, n_fingers={len(r['device_ids'])}")

print()
print("=" * 60)
print("TEST 4: Multi-row geometry (4 NMOS rows + 3 PMOS rows)")
print("=" * 60)

# Build 28 NMOS + 21 PMOS devices for a 7-row layout
multirow_nodes = []
for i in range(28):
    multirow_nodes.append({
        'id': f'MN{i}', 'type': 'nmos',
        'geometry': {'x': 0, 'y': 0, 'width': 0.294, 'height': 0.668},
        'electrical': {'nf': 1},
    })
for i in range(21):
    multirow_nodes.append({
        'id': f'MP{i}', 'type': 'pmos',
        'geometry': {'x': 0, 'y': 0, 'width': 0.294, 'height': 0.668},
        'electrical': {'nf': 1},
    })

multirow_data = {
    'nmos_rows': [
        {'label': 'nmos_input',   'devices': [f'MN{i}' for i in range(7)]},
        {'label': 'nmos_cascode', 'devices': [f'MN{i}' for i in range(7, 14)]},
        {'label': 'nmos_mirror',  'devices': [f'MN{i}' for i in range(14, 21)]},
        {'label': 'nmos_bias',    'devices': [f'MN{i}' for i in range(21, 28)]},
    ],
    'pmos_rows': [
        {'label': 'pmos_load',    'devices': [f'MP{i}' for i in range(7)]},
        {'label': 'pmos_cascode', 'devices': [f'MP{i}' for i in range(7, 14)]},
        {'label': 'pmos_mirror',  'devices': [f'MP{i}' for i in range(14, 21)]},
    ],
}

placed_multi = convert_multirow_to_geometry(multirow_data, multirow_nodes, [])
print(f"  Total placed: {len(placed_multi)}")

# Check unique Y values
ys = sorted(set(round(p['geometry']['y'], 3) for p in placed_multi))
print(f"  Unique Y values: {len(ys)}")
for y in ys:
    row_devs = [p['id'] for p in placed_multi if round(p['geometry']['y'], 3) == y]
    dev_type = placed_multi[[p['geometry']['y'] for p in placed_multi].index(y)]['type']
    print(f"    y={y:.3f}  ({dev_type.upper()})  {len(row_devs)} devices")

# Verify PMOS/NMOS separation
nmos_ys = [p['geometry']['y'] for p in placed_multi if p['type'] == 'nmos']
pmos_ys = [p['geometry']['y'] for p in placed_multi if p['type'] == 'pmos']
sep_ok = min(pmos_ys) > max(nmos_ys)
print(f"  PMOS/NMOS separation: {'PASS' if sep_ok else 'FAIL'}")
print(f"  Max NMOS y = {max(nmos_ys):.3f}, Min PMOS y = {min(pmos_ys):.3f}")
print(f"  Total rows: {len(ys)}  (expected 7)")
print(f"  Multi-row: {'PASS' if len(ys) == 7 else 'FAIL'}")

"""Full integration test: geometry engine + matching + left-align + dummy padding."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_agent.ai_chat_bot.agents.geometry_engine import convert_multirow_to_geometry
from ai_agent.ai_chat_bot.agents.matching_adapter import apply_matching
from ai_agent.ai_chat_bot.nodes import _pad_rows_with_dummies
from collections import defaultdict, Counter

# === Build comparator nodes (same as real circuit) ===
# PMOS: MM0(m=8), MM1(m=8), MM2(m=8), MM3(m=8), MM4(m=4), MM5(m=4) = 40 fingers
# NMOS: MM8(m=8), MM9(m=8), MM10(m=4), MM6(m=1), MM7(m=1) = 22 fingers
nodes = []
def add_fingers(parent, nf, dev_type):
    for i in range(nf):
        nodes.append({
            "id": f"{parent}_f{i+1}",
            "type": dev_type,
            "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.818, "orientation": "R0"},
            "electrical": {"nf": 1, "parent": parent},
        })

add_fingers("MM0", 8, "pmos")
add_fingers("MM1", 8, "pmos")
add_fingers("MM2", 8, "pmos")
add_fingers("MM3", 8, "pmos")
add_fingers("MM4", 4, "pmos")
add_fingers("MM5", 4, "pmos")
add_fingers("MM6", 1, "nmos")
add_fingers("MM7", 1, "nmos")
add_fingers("MM8", 8, "nmos")
add_fingers("MM9", 8, "nmos")
add_fingers("MM10", 4, "nmos")

print(f"Total nodes: {len(nodes)} ({sum(1 for n in nodes if n['type']=='pmos')} PMOS, {sum(1 for n in nodes if n['type']=='nmos')} NMOS)")

# === Step 1: Geometry engine (multi-row placement) ===
multirow = {
    "nmos_rows": [
        {"label": "nmos_input", "devices": [f"MM8_f{i+1}" for i in range(8)] + [f"MM9_f{i+1}" for i in range(8)]},
        {"label": "nmos_tail",  "devices": [f"MM10_f{i+1}" for i in range(4)] + ["MM6_f1", "MM7_f1"]},
    ],
    "pmos_rows": [
        {"label": "pmos_precharge_a", "devices": [f"MM0_f{i+1}" for i in range(8)] + [f"MM3_f{i+1}" for i in range(8)]},
        {"label": "pmos_precharge_b", "devices": [f"MM1_f{i+1}" for i in range(8)] + [f"MM2_f{i+1}" for i in range(8)]},
        {"label": "pmos_latch",       "devices": [f"MM4_f{i+1}" for i in range(4)] + [f"MM5_f{i+1}" for i in range(4)]},
    ],
}

physical = convert_multirow_to_geometry(multirow, nodes, [])
print(f"\nAfter geometry engine: {len(physical)} nodes")

# Show rows
def show_rows(nodes_list, label=""):
    rows = defaultdict(list)
    for n in nodes_list:
        ry = round(float(n["geometry"]["y"]), 3)
        rows[ry].append(n)
    for ry in sorted(rows.keys()):
        rn = rows[ry]
        xs = [float(n["geometry"]["x"]) for n in rn]
        types = Counter(n["type"] for n in rn)
        dummies = sum(1 for n in rn if n.get("is_dummy"))
        print(f"  y={ry:>7.3f}: {len(rn):>2} ({dummies}D) x=[{min(xs):.3f}..{max(xs)+0.294:.3f}] {dict(types)}")

show_rows(physical, "geometry")

# === Step 2: Matching (simulate what the pipeline does) ===
print("\n=== MATCHING ===")
node_map = {n['id']: n for n in physical}

match_groups = [
    {"device_ids": [f"MM0_f{i+1}" for i in range(8)] + [f"MM3_f{i+1}" for i in range(8)],
     "parent_ids": ["MM0", "MM3"], "technique": "COMMON_CENTROID_1D"},
    {"device_ids": [f"MM1_f{i+1}" for i in range(8)] + [f"MM2_f{i+1}" for i in range(8)],
     "parent_ids": ["MM1", "MM2"], "technique": "COMMON_CENTROID_1D"},
    {"device_ids": [f"MM4_f{i+1}" for i in range(4)] + [f"MM5_f{i+1}" for i in range(4)],
     "parent_ids": ["MM4", "MM5"], "technique": "COMMON_CENTROID_1D"},
    {"device_ids": [f"MM8_f{i+1}" for i in range(8)] + [f"MM9_f{i+1}" for i in range(8)],
     "parent_ids": ["MM8", "MM9"], "technique": "COMMON_CENTROID_1D"},
]

for mreq in match_groups:
    dev_ids = mreq["device_ids"]
    group_ys = []
    for d in dev_ids:
        if d in node_map:
            group_ys.append(round(float(node_map[d]["geometry"]["y"]), 4))
    ax = 0.0
    y_counts = Counter(group_ys)
    ay = y_counts.most_common(1)[0][0] if group_ys else 0.0
    print(f"  {mreq['parent_ids']}: anchor_y={ay:.3f} (mode of {dict(y_counts)})")
    
    matched_nodes, block = apply_matching(nodes, dev_ids, mreq["technique"], anchor_x=ax, anchor_y=ay)
    matched_map = {mn['id']: mn for mn in matched_nodes}
    for i, pn in enumerate(physical):
        if pn['id'] in matched_map:
            physical[i] = matched_map[pn['id']]

print("\nAfter matching:")
show_rows(physical, "matched")

# === Step 3: Left-align + dummy padding ===
print("\n=== NORMALISE + PAD ===")
result = _pad_rows_with_dummies(physical)

print(f"\nFinal layout:")
show_rows(result, "final")

# Verify
rows = defaultdict(list)
for n in result:
    ry = round(float(n["geometry"]["y"]), 3)
    rows[ry].append(n)
widths = set()
for ry in sorted(rows.keys()):
    rn = rows[ry]
    xs = [float(n["geometry"]["x"]) for n in rn]
    assert min(xs) == 0.0, f"Row y={ry} doesn't start at x=0! min_x={min(xs)}"
    widths.add(round(max(xs) + 0.294, 3))

if len(widths) == 1:
    print(f"\nPASS: All rows start at x=0, equal width = {widths.pop():.3f}um")
else:
    print(f"\nFAIL: Unequal widths: {widths}")

# Check no PMOS/NMOS overlap
pmos_ys = sorted(set(round(float(n["geometry"]["y"]), 3) for n in result if n["type"] == "pmos"))
nmos_ys = sorted(set(round(float(n["geometry"]["y"]), 3) for n in result if n["type"] == "nmos"))
if pmos_ys and nmos_ys:
    max_nmos_bottom = max(nmos_ys) + 0.818
    min_pmos_top = min(pmos_ys)
    gap = min_pmos_top - max_nmos_bottom
    print(f"PMOS/NMOS gap = {gap:.3f}um (NMOS top edge={max_nmos_bottom:.3f}, PMOS bottom={min_pmos_top:.3f})")
    if gap > 0:
        print("PASS: No vertical overlap")
    else:
        print("FAIL: Vertical overlap!")

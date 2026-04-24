"""Test: Staircase fix + dummy padding."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_agent.ai_chat_bot.nodes import _pad_rows_with_dummies
from collections import defaultdict

# Simulate a staircase layout (what matching produces):
# PMOS row 0 (y=0.941): 16 devices starting at x=0.0 (matching anchor=0)
# PMOS row 1 (y=1.800): 16 devices starting at x=1.5 (matching anchor=1.5!)
# PMOS row 2 (y=2.659):  8 devices starting at x=3.0 (matching anchor=3.0!)
# NMOS row 0 (y=0.000): 16 devices starting at x=0.5
# NMOS row 1 (y=-0.859):  6 devices starting at x=0.0

nodes = []
# PMOS row 0: 16 devices from x=0
for i in range(16):
    nodes.append({"id": f"P0_{i}", "type": "pmos", "geometry": {"x": i * 0.294, "y": 0.941, "width": 0.294, "height": 0.818, "orientation": "R0"}, "electrical": {"nf": 1}})

# PMOS row 1: 16 devices from x=1.5 (STAIRCASE!)
for i in range(16):
    nodes.append({"id": f"P1_{i}", "type": "pmos", "geometry": {"x": 1.5 + i * 0.294, "y": 1.800, "width": 0.294, "height": 0.818, "orientation": "R0"}, "electrical": {"nf": 1}})

# PMOS row 2: 8 devices from x=3.0 (STAIRCASE!)
for i in range(8):
    nodes.append({"id": f"P2_{i}", "type": "pmos", "geometry": {"x": 3.0 + i * 0.294, "y": 2.659, "width": 0.294, "height": 0.818, "orientation": "R0"}, "electrical": {"nf": 1}})

# NMOS row 0: 16 devices from x=0.5
for i in range(16):
    nodes.append({"id": f"N0_{i}", "type": "nmos", "geometry": {"x": 0.5 + i * 0.294, "y": 0.000, "width": 0.294, "height": 0.818, "orientation": "R0"}, "electrical": {"nf": 1}})

# NMOS row 1: 6 devices from x=0
for i in range(6):
    nodes.append({"id": f"N1_{i}", "type": "nmos", "geometry": {"x": i * 0.294, "y": -0.859, "width": 0.294, "height": 0.818, "orientation": "R0"}, "electrical": {"nf": 1}})

print("=== BEFORE (staircase) ===")
rows = defaultdict(list)
for n in nodes:
    ry = round(n["geometry"]["y"], 3)
    rows[ry].append(n)
for ry in sorted(rows.keys()):
    rn = rows[ry]
    xs = [n["geometry"]["x"] for n in rn]
    print(f"  y={ry:>7.3f}: {len(rn):>2} devices  x=[{min(xs):.3f} .. {max(xs)+0.294:.3f}]")

print("\n=== NORMALISING ===")
result = _pad_rows_with_dummies(nodes)
dummies = [n for n in result if n.get("is_dummy")]
active = [n for n in result if not n.get("is_dummy")]

print(f"\n=== AFTER ===")
print(f"Total={len(result)}, Active={len(active)}, Dummies={len(dummies)}")
rows2 = defaultdict(list)
for n in result:
    ry = round(n["geometry"]["y"], 3)
    rows2[ry].append(n)
for ry in sorted(rows2.keys()):
    rn = rows2[ry]
    xs = [n["geometry"]["x"] for n in rn]
    print(f"  y={ry:>7.3f}: {len(rn):>2} devices  x=[{min(xs):.3f} .. {max(xs)+0.294:.3f}]")

# Verify all rows start at x=0 and end at the same x
for ry in sorted(rows2.keys()):
    rn = rows2[ry]
    xs = [n["geometry"]["x"] for n in rn]
    assert min(xs) == 0.0, f"Row y={ry} doesn't start at x=0! min_x={min(xs)}"
    
widths = set()
for ry in sorted(rows2.keys()):
    rn = rows2[ry]
    xs = [n["geometry"]["x"] for n in rn]
    widths.add(round(max(xs) + 0.294, 3))
assert len(widths) == 1, f"Rows have different widths: {widths}"
print(f"\n✓ All rows start at x=0 and have equal width {widths.pop():.3f}µm")

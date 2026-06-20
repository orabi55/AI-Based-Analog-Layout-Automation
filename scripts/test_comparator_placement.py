"""
Test the full comparator placement pipeline with the fixes applied.
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_agent.placement.finger_grouper import _resolve_row_overlaps
from ai_agent.placement.abutment import optimize_all_rows_orientations

# Load the comparator placement data
placement_file = Path("examples/comparator/comparator_initial_placement.json")
with open(placement_file, 'r', encoding='utf-8') as f:
    data = json.load(f)

print("=== Comparator Placement Test ===")
print(f"Loaded {len(data['nodes'])} nodes from {placement_file}")

# The nodes are already expanded finger-level nodes
nodes = data['nodes']
print(f"Processing {len(nodes)} nodes")

# Step 1: Optimize orientations for abutment
print("\n[Step 1] Optimizing orientations for abutment...")
optimized = optimize_all_rows_orientations(nodes, data.get('terminal_nets', {}))
print(f"Optimized {len(optimized)} nodes")

# Step 2: Resolve overlaps and add fillers
print("\n[Step 2] Resolving overlaps and adding fillers...")
final = _resolve_row_overlaps(
    optimized,
    no_abutment=False,
    preserve_order=True,
    terminal_nets=data.get('terminal_nets', {})
)
print(f"Final layout: {len(final)} nodes")

# Analyze the results
from collections import defaultdict
rows = defaultdict(list)
for n in final:
    y = round(n.get('geometry', {}).get('y', 0.0), 6)
    rows[(y, n.get('type', 'unknown'))].append(n)

print("\n=== Row Analysis ===")
total_abutments = 0
total_gaps = 0
row_widths = []

for key in sorted(rows.keys()):
    y, typ = key
    row = sorted(rows[key], key=lambda n: n['geometry']['x'])
    
    # Calculate row width
    if row:
        min_x = min(n['geometry']['x'] for n in row)
        max_x = max(n['geometry']['x'] + n['geometry'].get('width', 0.294) for n in row)
        width = max_x - min_x
        row_widths.append(width)
        
        # Count abutments
        for i in range(len(row) - 1):
            curr = row[i]
            next_node = row[i + 1]
            curr_right = curr.get('abutment', {}).get('abut_right', False)
            next_left = next_node.get('abutment', {}).get('abut_left', False)
            
            if curr_right and next_left:
                total_abutments += 1
            else:
                total_gaps += 1
        
        # Count dummies
        dummies = sum(1 for n in row if n.get('is_dummy', False))
        active = len(row) - dummies
        
        print(f"Row y={y:.3f} ({typ}): {len(row)} devices ({active} active, {dummies} dummies), width={width:.3f}um")

print(f"\n=== Summary ===")
print(f"Total abutments: {total_abutments}")
print(f"Total gaps: {total_gaps}")

if row_widths:
    print(f"Row widths: min={min(row_widths):.3f}, max={max(row_widths):.3f}, diff={max(row_widths)-min(row_widths):.3f}")
    
    if max(row_widths) - min(row_widths) < 0.01:
        print("[OK] All rows have equal width!")
    else:
        print("[FAIL] Rows have different widths!")

if total_gaps == 0:
    print("[OK] 100% abutment achieved!")
else:
    print(f"[FAIL] {total_gaps} abutment gaps found!")

# Save the result for visualization
output_file = Path("scratch/comparator_test_output.json")
with open(output_file, 'w', encoding='utf-8') as f:
    json.dump(final, f, indent=2)
print(f"\nSaved result to {output_file}")

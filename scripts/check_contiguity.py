"""
Check if blocks are contiguous or interleaved
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Load the test output
output_file = Path("scratch/comparator_test_output.json")
with open(output_file, 'r', encoding='utf-8') as f:
    final = json.load(f)

# Focus on row y=2.454
row_nodes = [n for n in final if round(n.get('geometry', {}).get('y', 0.0), 6) == 2.454003]
row_nodes.sort(key=lambda n: n['geometry']['x'])

print("=== Row y=2.454 Layout Order ===\n")
for i, n in enumerate(row_nodes):
    is_dummy = n.get('is_dummy', False)
    parent = n['id'].split('_')[0] if not is_dummy else 'DUMMY'
    print(f"{i:2d}. x={n['geometry']['x']:.3f} {n['id']:20s} ({parent}) {'DUMMY' if is_dummy else ''}")

print("\n\n=== Block Contiguity Check ===")
# Check if MM4 and MM5 fingers are contiguous or interleaved
mm4_positions = [i for i, n in enumerate(row_nodes) if n['id'].startswith('MM4')]
mm5_positions = [i for i, n in enumerate(row_nodes) if n['id'].startswith('MM5')]

print(f"MM4 finger positions: {mm4_positions}")
print(f"MM5 finger positions: {mm5_positions}")

# Check if they're interleaved
mm4_set = set(mm4_positions)
mm5_set = set(mm5_positions)

# Find the range
all_positions = sorted(mm4_positions + mm5_positions)
min_pos = min(all_positions)
max_pos = max(all_positions)

print(f"\nPosition range: {min_pos} to {max_pos}")
print(f"Total positions: {len(all_positions)}")
print(f"MM4 fingers: {len(mm4_positions)}")
print(f"MM5 fingers: {len(mm5_positions)}")

# Check for interleaving
interleaved = False
for i in range(len(all_positions) - 1):
    pos1 = all_positions[i]
    pos2 = all_positions[i + 1]
    parent1 = row_nodes[pos1]['id'].split('_')[0]
    parent2 = row_nodes[pos2]['id'].split('_')[0]
    if parent1 != parent2:
        interleaved = True
        print(f"INTERLEAVED at positions {pos1} ({parent1}) and {pos2} ({parent2})")

if not interleaved:
    print("Blocks are CONTIGUOUS (not interleaved)")

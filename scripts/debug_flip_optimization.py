"""
Debug the flip optimization for row y=2.454.
"""
import sys
import json
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

# Load the test output
output_file = Path("scratch/no_optimize_test.json")
with open(output_file, 'r', encoding='utf-8') as f:
    data = json.load(f)

print("=== Analyzing Flip Optimization for Row y=2.454 ===\n")

# Get row y=2.454
row = [n for n in data if round(n.get('geometry', {}).get('y', 0.0), 6) == 2.454003]
row.sort(key=lambda n: n['geometry']['x'])

# Get active devices only
active = [n for n in row if not n.get('is_dummy', False)]

print(f"Active devices: {len(active)}\n")

# Group by parent
blocks = defaultdict(list)
for n in active:
    parent = n['id'].split('_')[0]
    blocks[parent].append(n)

print(f"Blocks: {list(blocks.keys())}\n")

# Check interleaved pattern
positions = [n['id'].split('_')[0] for n in active]
print(f"Pattern: {' '.join(positions)}\n")

# Try all 2^8 = 256 flip combinations
from itertools import product
from ai_agent.placement.finger_grouper import get_block_boundary_nets

best_abutments = 0
best_flips = None

for flip_combo in product([False, True], repeat=len(active)):
    # Apply flips
    for i, flip in enumerate(flip_combo):
        if flip:
            active[i]['swapped_sd'] = not active[i].get('swapped_sd', False)
            active[i]['net_s'], active[i]['net_d'] = active[i]['net_d'], active[i]['net_s']
    
    # Count abutments
    abutments = 0
    for i in range(len(active) - 1):
        curr = active[i]
        next_node = active[i + 1]
        _, curr_right = get_block_boundary_nets([curr], False)
        next_left, _ = get_block_boundary_nets([next_node], False)
        
        if curr_right and next_left and curr_right != 'NC' and next_left != 'NC' and curr_right == next_left:
            abutments += 1
    
    if abutments > best_abutments:
        best_abutments = abutments
        best_flips = list(flip_combo)
    
    # Revert flips
    for i, flip in enumerate(flip_combo):
        if flip:
            active[i]['swapped_sd'] = not active[i].get('swapped_sd', False)
            active[i]['net_s'], active[i]['net_d'] = active[i]['net_d'], active[i]['net_s']

print(f"Best abutments: {best_abutments} out of {len(active)-1} possible")
print(f"Best flip combination: {best_flips}\n")

# Apply best flips
for i, flip in enumerate(best_flips):
    if flip:
        active[i]['swapped_sd'] = not active[i].get('swapped_sd', False)
        active[i]['net_s'], active[i]['net_d'] = active[i]['net_d'], active[i]['net_s']

print("After applying best flips:")
for i, n in enumerate(active):
    _, right = get_block_boundary_nets([n], False)
    print(f"  {n['id']:20s} swapped={n.get('swapped_sd',False)} right_boundary={right}")

print(f"\nAbutment check:")
for i in range(len(active) - 1):
    curr = active[i]
    next_node = active[i + 1]
    _, curr_right = get_block_boundary_nets([curr], False)
    next_left, _ = get_block_boundary_nets([next_node], False)
    
    can_abut = curr_right == next_left
    print(f"  {curr['id']:20s} right={curr_right:8s} <-> {next_node['id']:20s} left={next_left:8s} {'✓' if can_abut else ''}")

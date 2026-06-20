"""
Add device reordering to maximize abutment.
"""
import sys
import json
from pathlib import Path
from itertools import permutations, product

sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_agent.placement.finger_grouper import get_block_boundary_nets

# Load test data
with open('scratch/no_optimize_test.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Get row y=2.454
row = [n for n in data if round(n.get('geometry', {}).get('y', 0.0), 6) == 2.454003]
row.sort(key=lambda n: n['geometry']['x'])

# Get active devices
active = [n for n in row if not n.get('is_dummy', False)]

print("=== Testing All Permutations and Flip Combinations ===\n")

N = len(active)
best_abutments = -1
best_perm = None
best_flips = None

# Try all permutations (for small N)
if N <= 8:
    for perm in permutations(range(N)):
        # Try all flip combinations for this permutation
        for flip_combo in product([False, True], repeat=N):
            # Create permuted and flipped devices
            devices = []
            for i, idx in enumerate(perm):
                dev = {
                    'id': active[idx]['id'],
                    'net_s': active[idx].get('net_s'),
                    'net_d': active[idx].get('net_d'),
                    'swapped_sd': active[idx].get('swapped_sd', False),
                    'geometry': {'orientation': 'R0'},
                    'electrical': {'nf': 1}
                }
                if flip_combo[i]:
                    dev['swapped_sd'] = not dev['swapped_sd']
                    dev['net_s'], dev['net_d'] = dev['net_d'], dev['net_s']
                devices.append(dev)
            
            # Count abutments
            abutments = 0
            for i in range(N - 1):
                _, curr_right = get_block_boundary_nets([devices[i]], False)
                next_left, _ = get_block_boundary_nets([devices[i+1]], False)
                
                if curr_right and next_left and curr_right != 'NC' and next_left != 'NC' and curr_right == next_left:
                    abutments += 1
            
            if abutments > best_abutments:
                best_abutments = abutments
                best_perm = list(perm)
                best_flips = list(flip_combo)

print(f"Best abutments: {best_abutments} out of {N-1} possible")
print(f"Best permutation: {best_perm}")
print(f"Best flip combination: {best_flips}\n")

# Apply best permutation and flips
if best_perm and best_flips:
    reordered = []
    for i, idx in enumerate(best_perm):
        dev = dict(active[idx])
        if best_flips[i]:
            dev['swapped_sd'] = not dev.get('swapped_sd', False)
            dev['net_s'], dev['net_d'] = dev['net_d'], dev['net_s']
        reordered.append(dev)
    
    print("After applying best permutation and flips:")
    for i, n in enumerate(reordered):
        node = {
            'id': n['id'],
            'net_s': n.get('net_s'),
            'net_d': n.get('net_d'),
            'swapped_sd': n.get('swapped_sd', False),
            'geometry': {'orientation': 'R0'},
            'electrical': {'nf': 1}
        }
        left, right = get_block_boundary_nets([node], False)
        print(f"  {i}. {n['id']:20s} swapped={n.get('swapped_sd',False)} left={left:8s} right={right:8s}")
    
    print(f"\nAbutment verification:")
    for i in range(N - 1):
        curr = reordered[i]
        next_node = reordered[i + 1]
        
        curr_node = {
            'id': curr['id'],
            'net_s': curr.get('net_s'),
            'net_d': curr.get('net_d'),
            'swapped_sd': curr.get('swapped_sd', False),
            'geometry': {'orientation': 'R0'},
            'electrical': {'nf': 1}
        }
        next_node_dict = {
            'id': next_node['id'],
            'net_s': next_node.get('net_s'),
            'net_d': next_node.get('net_d'),
            'swapped_sd': next_node.get('swapped_sd', False),
            'geometry': {'orientation': 'R0'},
            'electrical': {'nf': 1}
        }
        
        _, curr_right = get_block_boundary_nets([curr_node], False)
        next_left, _ = get_block_boundary_nets([next_node_dict], False)
        
        can_abut = curr_right == next_left
        print(f"  {i}-{i+1}: {curr['id']:20s} right={curr_right:8s} <-> {next_node['id']:20s} left={next_left:8s} {'OK' if can_abut else 'GAP'}")

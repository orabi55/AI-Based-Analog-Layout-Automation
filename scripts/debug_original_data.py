"""
Debug the data flow to understand why integrated version achieves fewer abutments.
"""
import sys
import json
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

# Load the original placement data
placement_file = Path("examples/comparator/comparator_initial_placement.json")
with open(placement_file, 'r', encoding='utf-8') as f:
    original_data = json.load(f)

print("=== Original Placement Data for Row y=2.454 ===\n")

# Get row y=2.454
original_row = [n for n in original_data['nodes'] if round(n.get('geometry', {}).get('y', 0.0), 6) == 2.454003]
original_row.sort(key=lambda n: n['geometry']['x'])

# Get active devices only
original_active = [n for n in original_row if not n.get('is_dummy', False)]

print(f"Original active devices: {len(original_active)}\n")

for i, n in enumerate(original_active):
    parent = n['id'].split('_')[0]
    print(f"{i}. {n['id']:20s} ({parent})")
    print(f"   net_s={n.get('net_s','?'):8s} net_d={n.get('net_d','?'):8s}")
    print(f"   swapped_sd={n.get('swapped_sd', False)}")
    print()

# Now test boundary nets on original data
from ai_agent.placement.finger_grouper import get_block_boundary_nets

print("\n=== Boundary Nets on Original Data ===\n")
for i, n in enumerate(original_active):
    node = {
        'id': n['id'],
        'net_s': n.get('net_s'),
        'net_d': n.get('net_d'),
        'swapped_sd': n.get('swapped_sd', False),
        'geometry': {'orientation': n.get('geometry', {}).get('orientation', 'R0')},
        'electrical': {'nf': n.get('electrical', {}).get('nf', 1)}
    }
    left, right = get_block_boundary_nets([node], False)
    print(f"{i}. {n['id']:20s} left={left:8s} right={right:8s}")

# Count abutments on original data
print("\n=== Abutments on Original Data ===\n")
abutments = 0
for i in range(len(original_active) - 1):
    curr = original_active[i]
    next_node = original_active[i + 1]
    
    curr_node = {
        'id': curr['id'],
        'net_s': curr.get('net_s'),
        'net_d': curr.get('net_d'),
        'swapped_sd': curr.get('swapped_sd', False),
        'geometry': {'orientation': curr.get('geometry', {}).get('orientation', 'R0')},
        'electrical': {'nf': curr.get('electrical', {}).get('nf', 1)}
    }
    next_node_dict = {
        'id': next_node['id'],
        'net_s': next_node.get('net_s'),
        'net_d': next_node.get('net_d'),
        'swapped_sd': next_node.get('swapped_sd', False),
        'geometry': {'orientation': next_node.get('geometry', {}).get('orientation', 'R0')},
        'electrical': {'nf': next_node.get('electrical', {}).get('nf', 1)}
    }
    
    _, curr_right = get_block_boundary_nets([curr_node], False)
    next_left, _ = get_block_boundary_nets([next_node_dict], False)
    
    can_abut = curr_right == next_left
    if can_abut:
        abutments += 1
    status = "OK" if can_abut else "GAP"
    print(f"{i}-{i+1}: {curr['id']:20s} right={curr_right:8s} <-> {next_node['id']:20s} left={next_left:8s} [{status}]")

print(f"\nTotal abutments on original data: {abutments} out of {len(original_active)-1}")

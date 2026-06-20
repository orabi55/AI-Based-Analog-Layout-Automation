"""
Debug why abutment flags aren't being set even though optimization achieves 7/7.
"""
import sys
import json
from pathlib import Path

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

print("=== Boundary Nets After Optimization ===\n")
for i, n in enumerate(active):
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

print("\n=== Abutment Check ===\n")
for i in range(len(active) - 1):
    curr = active[i]
    next_node = active[i + 1]
    
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
    status = "OK" if can_abut else "GAP"
    print(f"{i}-{i+1}: {curr['id']:20s} right={curr_right:8s} <-> {next_node['id']:20s} left={next_left:8s} [{status}]")

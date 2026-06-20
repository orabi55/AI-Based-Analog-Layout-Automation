"""
Debug the flip optimization for row y=2.454
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

print("=== Row y=2.454 Analysis ===\n")
print("Devices in order:")
for i, n in enumerate(row_nodes):
    is_dummy = n.get('is_dummy', False)
    swapped = n.get('swapped_sd', False)
    print(f"{i:2d}. {n['id']:20s} D={n.get('net_d','?'):8s} S={n.get('net_s','?'):8s} swapped={swapped} dummy={is_dummy}")

print("\nAbutment gaps:")
for i in range(len(row_nodes) - 1):
    curr = row_nodes[i]
    next_node = row_nodes[i + 1]
    curr_right = curr.get('abutment', {}).get('abut_right', False)
    next_left = next_node.get('abutment', {}).get('abut_left', False)
    
    if not (curr_right and next_left):
        curr_d = curr.get('net_d', '?')
        curr_s = curr.get('net_s', '?')
        next_s = next_node.get('net_s', '?')
        next_d = next_node.get('net_d', '?')
        
        can_abut = (curr_d == next_s) or (curr_s == next_d)
        if can_abut:
            print(f"  GAP: {curr['id']} (D={curr_d},S={curr_s}) <-> {next_node['id']} (D={next_d},S={next_s})")
            print(f"       Shared net: {curr_d if curr_d == next_s else curr_s}")
            print(f"       curr swapped={curr.get('swapped_sd', False)}, next swapped={next_node.get('swapped_sd', False)}")

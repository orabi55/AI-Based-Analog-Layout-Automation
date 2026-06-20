"""
Debug why flip optimizer isn't fixing the COULD ABUT gaps in row y=2.454
"""
import sys
import json
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

# Load the test output
output_file = Path("scratch/comparator_test_output.json")
with open(output_file, 'r', encoding='utf-8') as f:
    final = json.load(f)

# Focus on row y=2.454
row_nodes = [n for n in final if round(n.get('geometry', {}).get('y', 0.0), 6) == 2.454003]
row_nodes.sort(key=lambda n: n['geometry']['x'])

print("=== Row y=2.454 Detailed Analysis ===\n")

# Group by parent transistor
blocks = defaultdict(list)
for n in row_nodes:
    if not n.get('is_dummy', False):
        # Extract parent ID (e.g., MM4 from MM4_m2)
        parent_id = n['id'].split('_')[0]
        blocks[parent_id].append(n)

print("Blocks in row:")
for parent_id, nodes in sorted(blocks.items()):
    print(f"\n{parent_id}: {len(nodes)} fingers")
    for n in nodes:
        print(f"  {n['id']:20s} D={n.get('net_d','?'):8s} S={n.get('net_s','?'):8s} swapped={n.get('swapped_sd', False)}")

print("\n\nGap analysis for COULD ABUT cases:")
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
            print(f"\nGAP: {curr['id']} <-> {next_node['id']}")
            print(f"  curr: D={curr_d}, S={curr_s}, swapped={curr.get('swapped_sd', False)}")
            print(f"  next: D={next_d}, S={next_s}, swapped={next_node.get('swapped_sd', False)}")
            print(f"  Shared net: {curr_d if curr_d == next_s else curr_s}")
            
            # What would need to happen to abut?
            if curr_d == next_s:
                print(f"  To abut: curr right boundary should be {curr_d}, next left boundary should be {next_s}")
            else:
                print(f"  To abut: curr right boundary should be {curr_s}, next left boundary should be {next_d}")

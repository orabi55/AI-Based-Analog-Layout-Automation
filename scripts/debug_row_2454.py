"""
Debug why row y=2.454 isn't achieving 100% abutment.
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

print("=== Debugging Row y=2.454 ===\n")

# Get row y=2.454
row = [n for n in data if round(n.get('geometry', {}).get('y', 0.0), 6) == 2.454003]
row.sort(key=lambda n: n['geometry']['x'])

print(f"Row has {len(row)} devices\n")

# Show all devices
for i, n in enumerate(row):
    is_dummy = n.get('is_dummy', False)
    parent = n['id'].split('_')[0] if not is_dummy else 'DUMMY'
    abut_l = n.get('abutment', {}).get('abut_left', False)
    abut_r = n.get('abutment', {}).get('abut_right', False)
    print(f"{i:2d}. {n['id']:20s} ({parent:5s}) D={n.get('net_d','?'):8s} S={n.get('net_s','?'):8s} swapped={n.get('swapped_sd',False)} abut=({abut_l},{abut_r}) {'DUMMY' if is_dummy else ''}")

print("\n=== Gap Analysis ===")
for i in range(len(row) - 1):
    curr = row[i]
    next_node = row[i + 1]
    curr_right = curr.get('abutment', {}).get('abut_right', False)
    next_left = next_node.get('abutment', {}).get('abut_left', False)
    
    if not (curr_right and next_left):
        curr_d = curr.get('net_d', '?')
        curr_s = curr.get('net_s', '?')
        next_s = next_node.get('net_s', '?')
        next_d = next_node.get('net_d', '?')
        
        can_abut = (curr_d == next_s) or (curr_s == next_d)
        status = "COULD ABUT" if can_abut else "NO SHARED NET"
        
        print(f"GAP {i}-{i+1}: {curr['id']} <-> {next_node['id']} [{status}]")
        if can_abut:
            shared = curr_d if curr_d == next_s else curr_s
            print(f"  Shared net: {shared}")
            print(f"  curr: D={curr_d}, S={curr_s}, swapped={curr.get('swapped_sd', False)}")
            print(f"  next: D={next_d}, S={next_s}, swapped={next_node.get('swapped_sd', False)}")
            
            # What flip would fix this?
            if curr_d == next_s:
                print(f"  Need: curr right boundary = {curr_d}, next left boundary = {next_s}")
            else:
                print(f"  Need: curr right boundary = {curr_s}, next left boundary = {next_d}")

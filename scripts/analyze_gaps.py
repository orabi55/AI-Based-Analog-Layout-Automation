"""
Analyze abutment gaps in the comparator placement.
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

print("=== Abutment Gap Analysis ===\n")

rows = defaultdict(list)
for n in final:
    y = round(n.get('geometry', {}).get('y', 0.0), 6)
    rows[(y, n.get('type', 'unknown'))].append(n)

total_gaps = 0
for key in sorted(rows.keys()):
    y, typ = key
    row = sorted(rows[key], key=lambda n: n['geometry']['x'])
    
    print(f"Row y={y:.3f} ({typ}):")
    for i in range(len(row) - 1):
        curr = row[i]
        next_node = row[i + 1]
        curr_right = curr.get('abutment', {}).get('abut_right', False)
        next_left = next_node.get('abutment', {}).get('abut_left', False)
        
        curr_id = curr.get('id', '?')
        next_id = next_node.get('id', '?')
        curr_s = curr.get('net_s', '?')
        curr_d = curr.get('net_d', '?')
        next_s = next_node.get('net_s', '?')
        next_d = next_node.get('net_d', '?')
        
        if not (curr_right and next_left):
            total_gaps += 1
            # Check if they could abut
            can_abut = (curr_d == next_s) or (curr_s == next_d)
            status = "COULD ABUT" if can_abut else "NO SHARED NET"
            print(f"  GAP[{total_gaps}]: {curr_id} (D={curr_d},S={curr_s}) <-> {next_id} (D={next_d},S={next_s}) [{status}]")
    print()

print(f"Total gaps: {total_gaps}")

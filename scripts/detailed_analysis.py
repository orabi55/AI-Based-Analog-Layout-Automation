import sys
import json
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

# Load the test output
output_file = Path("scratch/no_optimize_test.json")
with open(output_file, 'r', encoding='utf-8') as f:
    data = json.load(f)

print("=== Detailed Abutment Analysis ===\n")

# Group by row
rows = defaultdict(list)
for n in data:
    y = round(n.get('geometry', {}).get('y', 0.0), 6)
    rows[(y, n.get('type', 'unknown'))].append(n)

total_abutments = 0
total_gaps = 0

for key in sorted(rows.keys()):
    y, typ = key
    row = sorted(rows[key], key=lambda n: n['geometry']['x'])
    
    # Get active devices
    active = [n for n in row if not n.get('is_dummy', False)]
    
    print(f"Row y={y:.3f} ({typ}): {len(active)} active devices")
    
    row_abutments = 0
    row_gaps = 0
    
    for i in range(len(active) - 1):
        curr = active[i]
        next_node = active[i + 1]
        curr_right = curr.get('abutment', {}).get('abut_right', False)
        next_left = next_node.get('abutment', {}).get('abut_left', False)
        
        if curr_right and next_left:
            row_abutments += 1
            total_abutments += 1
        else:
            row_gaps += 1
            total_gaps += 1
            print(f"  GAP: {curr['id']:20s} <-> {next_node['id']:20s}")
    
    print(f"  Abutments: {row_abutments}, Gaps: {row_gaps}\n")

print(f"=== Summary ===")
print(f"Total abutments: {total_abutments}")
print(f"Total gaps: {total_gaps}")
print(f"Abutment rate: {total_abutments/(total_abutments+total_gaps)*100:.1f}%")

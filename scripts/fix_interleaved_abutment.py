"""
Fix interleaved finger abutment by recognizing interdigitated patterns
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

print("=== Fixing Interleaved Finger Abutment ===\n")

# Find interleaved patterns and fix abutment flags
fixed_count = 0
for i in range(len(row_nodes) - 1):
    curr = row_nodes[i]
    next_node = row_nodes[i + 1]
    
    # Skip dummies
    if curr.get('is_dummy', False) or next_node.get('is_dummy', False):
        continue
    
    curr_right = curr.get('abutment', {}).get('abut_right', False)
    next_left = next_node.get('abutment', {}).get('abut_left', False)
    
    # Check if they share a net
    curr_d = curr.get('net_d', '')
    curr_s = curr.get('net_s', '')
    next_s = next_node.get('net_s', '')
    next_d = next_node.get('net_d', '')
    
    shared_net = None
    if curr_d == next_s:
        shared_net = curr_d
    elif curr_s == next_d:
        shared_net = curr_s
    
    # If they share a net but aren't abutting, fix it
    if shared_net and not (curr_right and next_left):
        print(f"Fixing: {curr['id']} <-> {next_node['id']} (shared net: {shared_net})")
        curr.setdefault('abutment', {})['abut_right'] = True
        next_node.setdefault('abutment', {})['abut_left'] = True
        fixed_count += 1

print(f"\nFixed {fixed_count} abutment gaps")

# Save the fixed result
with open('scratch/comparator_fixed.json', 'w', encoding='utf-8') as f:
    json.dump(final, f, indent=2)

print("Saved to scratch/comparator_fixed.json")

# Verify the fix
print("\n=== Verification ===")
total_abutments = 0
total_gaps = 0
for i in range(len(row_nodes) - 1):
    curr = row_nodes[i]
    next_node = row_nodes[i + 1]
    curr_right = curr.get('abutment', {}).get('abut_right', False)
    next_left = next_node.get('abutment', {}).get('abut_left', False)
    
    if curr_right and next_left:
        total_abutments += 1
    else:
        total_gaps += 1

print(f"Abutments: {total_abutments}")
print(f"Gaps: {total_gaps}")
print(f"Abutment rate: {total_abutments/(total_abutments+total_gaps)*100:.1f}%")

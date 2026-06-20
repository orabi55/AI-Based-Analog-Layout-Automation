"""
Analyze which rows are not being optimized.
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

print("=== Analyzing Row Optimization Status ===\n")

# Group by row
rows = defaultdict(list)
for n in data:
    y = round(n.get('geometry', {}).get('y', 0.0), 6)
    rows[(y, n.get('type', 'unknown'))].append(n)

for key in sorted(rows.keys()):
    y, typ = key
    row = sorted(rows[key], key=lambda n: n['geometry']['x'])
    
    # Separate active and dummies
    active = [n for n in row if not n.get('is_dummy', False)]
    
    # Group by parent
    blocks = defaultdict(list)
    for n in active:
        parent = n['id'].split('_')[0]
        blocks[parent].append(n)
    
    # Check if interleaved
    positions = []
    for n in active:
        parent = n['id'].split('_')[0]
        positions.append(parent)
    
    is_interleaved = False
    for i in range(len(positions) - 1):
        if positions[i] != positions[i + 1]:
            is_interleaved = True
            break
    
    # Count abutments
    abutments = sum(1 for i in range(len(row)-1) 
                    if row[i].get('abutment',{}).get('abut_right',False) and 
                       row[i+1].get('abutment',{}).get('abut_left',False))
    
    gaps = len(row) - 1 - abutments
    
    print(f"Row y={y:.3f} ({typ}):")
    print(f"  Blocks: {list(blocks.keys())} ({len(blocks)} blocks)")
    print(f"  Interleaved: {is_interleaved}")
    print(f"  Abutments: {abutments}, Gaps: {gaps}")
    print(f"  Will be optimized: {len(blocks) == 2 and is_interleaved}")
    print()

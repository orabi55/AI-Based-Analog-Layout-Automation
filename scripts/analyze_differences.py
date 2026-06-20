"""
Analyze what the comprehensive fix does differently.
"""
import sys
import json
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

# Load both outputs
no_optimize_file = Path("scratch/no_optimize_test.json")
with open(no_optimize_file, 'r', encoding='utf-8') as f:
    no_optimize = json.load(f)

comprehensive_file = Path("scratch/comparator_final_fixed.json")
with open(comprehensive_file, 'r', encoding='utf-8') as f:
    comprehensive = json.load(f)

print("=== Analyzing Differences ===\n")

# Handle both formats
if isinstance(comprehensive, dict) and 'nodes' in comprehensive:
    comprehensive = comprehensive['nodes']

# Group by row
def group_by_row(nodes):
    rows = defaultdict(list)
    for n in nodes:
        if isinstance(n, dict):
            y = round(n.get('geometry', {}).get('y', 0.0), 6)
            rows[(y, n.get('type', 'unknown'))].append(n)
    return rows

no_optimize_rows = group_by_row(no_optimize)
comprehensive_rows = group_by_row(comprehensive)

# Compare row y=0.000 (nmos)
key = (0.0, 'nmos')
if key in no_optimize_rows and key in comprehensive_rows:
    no_opt_row = sorted(no_optimize_rows[key], key=lambda n: n['geometry']['x'])
    comp_row = sorted(comprehensive_rows[key], key=lambda n: n['geometry']['x'])
    
    print(f"=== Row y=0.000 (nmos) ===")
    print(f"No optimize: {len(no_opt_row)} devices")
    print(f"Comprehensive: {len(comp_row)} devices")
    
    # Count abutments
    no_opt_abut = sum(1 for i in range(len(no_opt_row)-1) 
                      if no_opt_row[i].get('abutment',{}).get('abut_right',False) and 
                         no_opt_row[i+1].get('abutment',{}).get('abut_left',False))
    
    comp_abut = sum(1 for i in range(len(comp_row)-1) 
                    if comp_row[i].get('abutment',{}).get('abut_right',False) and 
                       comp_row[i+1].get('abutment',{}).get('abut_left',False))
    
    print(f"No optimize abutments: {no_opt_abut}")
    print(f"Comprehensive abutments: {comp_abut}")
    
    # Show active devices
    print("\nNo optimize active devices:")
    for n in no_opt_row:
        if not n.get('is_dummy', False):
            print(f"  {n['id']:20s} D={n.get('net_d','?'):8s} S={n.get('net_s','?'):8s} swapped={n.get('swapped_sd',False)}")
    
    print("\nComprehensive active devices:")
    for n in comp_row:
        if not n.get('is_dummy', False):
            print(f"  {n['id']:20s} D={n.get('net_d','?'):8s} S={n.get('net_s','?'):8s} swapped={n.get('swapped_sd',False)}")

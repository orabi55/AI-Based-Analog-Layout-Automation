"""
Compare comprehensive fix vs integrated pipeline for row y=2.454.
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Load comprehensive fix output
comp_file = Path("scratch/comparator_final_fixed.json")
with open(comp_file, 'r', encoding='utf-8') as f:
    comp_data = json.load(f)

# Load integrated pipeline output
int_file = Path("scratch/no_optimize_test.json")
with open(int_file, 'r', encoding='utf-8') as f:
    int_data = json.load(f)

print("=== Comparing Row y=2.454 ===\n")

# Handle different JSON formats
if isinstance(comp_data, dict) and 'nodes' in comp_data:
    comp_data = comp_data['nodes']
if isinstance(int_data, dict) and 'nodes' in int_data:
    int_data = int_data['nodes']

# Get row y=2.454 from both
comp_row = [n for n in comp_data if round(n.get('geometry', {}).get('y', 0.0), 6) == 2.454003]
comp_row.sort(key=lambda n: n['geometry']['x'])

int_row = [n for n in int_data if round(n.get('geometry', {}).get('y', 0.0), 6) == 2.454003]
int_row.sort(key=lambda n: n['geometry']['x'])

print(f"Comprehensive fix: {len(comp_row)} devices")
print(f"Integrated pipeline: {len(int_row)} devices\n")

# Show active devices from comprehensive fix
print("Comprehensive fix - Active devices:")
for i, n in enumerate(comp_row):
    if not n.get('is_dummy', False):
        print(f"  {n['id']:20s} D={n.get('net_d','?'):8s} S={n.get('net_s','?'):8s} swapped={n.get('swapped_sd',False)}")

print("\nIntegrated pipeline - Active devices:")
for i, n in enumerate(int_row):
    if not n.get('is_dummy', False):
        print(f"  {n['id']:20s} D={n.get('net_d','?'):8s} S={n.get('net_s','?'):8s} swapped={n.get('swapped_sd',False)}")

# Count abutments
comp_abut = sum(1 for i in range(len(comp_row)-1) 
                if comp_row[i].get('abutment',{}).get('abut_right',False) and 
                   comp_row[i+1].get('abutment',{}).get('abut_left',False))

int_abut = sum(1 for i in range(len(int_row)-1) 
               if int_row[i].get('abutment',{}).get('abut_right',False) and 
                  int_row[i+1].get('abutment',{}).get('abut_left',False))

print(f"\nComprehensive fix abutments: {comp_abut}")
print(f"Integrated pipeline abutments: {int_abut}")

"""
Check initial device state before optimization.
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Load the original placement data
placement_file = Path("examples/comparator/comparator_initial_placement.json")
with open(placement_file, 'r', encoding='utf-8') as f:
    data = json.load(f)

print("=== Initial Device State for Row y=2.454 ===\n")

# Get row y=2.454
row = [n for n in data['nodes'] if round(n.get('geometry', {}).get('y', 0.0), 6) == 2.454003]
row.sort(key=lambda n: n['geometry']['x'])

# Get active devices only
active = [n for n in row if not n.get('is_dummy', False)]

print(f"Active devices: {len(active)}\n")

for i, n in enumerate(active):
    parent = n['id'].split('_')[0]
    print(f"{i}. {n['id']:20s} ({parent})")
    print(f"   net_s={n.get('net_s','?'):8s} net_d={n.get('net_d','?'):8s}")
    print(f"   swapped_sd={n.get('swapped_sd', False)}")
    print(f"   orientation={n.get('geometry', {}).get('orientation', 'R0')}")
    print()

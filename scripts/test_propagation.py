"""
Test if maximize_interleaved_abutment modifications propagate back.
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_agent.placement.finger_grouper import maximize_interleaved_abutment

# Load test data
with open('scratch/no_optimize_test.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Get row y=2.454
row = [n for n in data if round(n.get('geometry', {}).get('y', 0.0), 6) == 2.454003]
row.sort(key=lambda n: n['geometry']['x'])

# Get active devices
active = [n for n in row if not n.get('is_dummy', False)]

print(f"Before optimization: {len(active)} active devices")
print(f"Abutment flags before:")
for i, n in enumerate(active):
    abut = n.get('abutment', {})
    print(f"  {n['id']:20s} abut_left={abut.get('abut_left', False)} abut_right={abut.get('abut_right', False)}")

# Call the function
result = maximize_interleaved_abutment(row, {})

print(f"\nAfter optimization:")
print(f"Abutment flags after:")
for i, n in enumerate(active):
    abut = n.get('abutment', {})
    print(f"  {n['id']:20s} abut_left={abut.get('abut_left', False)} abut_right={abut.get('abut_right', False)}")

# Check if modifications propagated
print(f"\nModifications propagated: {result is row}")
print(f"Same objects: {all(a is b for a, b in zip(active, [n for n in result if not n.get('is_dummy', False)]))}")

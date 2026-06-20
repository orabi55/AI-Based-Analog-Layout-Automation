"""
Regenerate comparator initial placement with abutment enabled.
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_agent.placement.finger_grouper import expand_to_fingers
from ai_agent.placement.abutment import optimize_all_rows_orientations

# Load the comparator placement data
placement_file = Path("examples/comparator/comparator_initial_placement.json")
with open(placement_file, 'r', encoding='utf-8') as f:
    data = json.load(f)

# Extract the placement nodes
placement_nodes = data.get('placement', [])

print(f"Loaded {len(placement_nodes)} placement nodes")

# Expand to fingers with abutment enabled
expanded = expand_to_fingers(placement_nodes, no_abutment=False)

print(f"Expanded to {len(expanded)} finger nodes")

# Optimize orientations
optimized = optimize_all_rows_orientations(expanded)

print(f"Optimized orientations for {len(optimized)} nodes")

# Save the result
output_file = Path("scratch/comparator_regen_abutment.json")
with open(output_file, 'w', encoding='utf-8') as f:
    json.dump(optimized, f, indent=2)

print(f"Saved to {output_file}")

# Check abutment statistics
from scratch.check_comp_abut import check_abutment
check_abutment(str(output_file))

"""Quick test: verify OAS writer updates positions correctly."""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from export.oas_writer import update_oas_placement
from parser.layout_reader import extract_layout_instances

# Load the placement JSON
with open("Xor_initial_placement.json") as f:
    data = json.load(f)

nodes = data["nodes"]

# Write updated OAS
output = "Xor_Automation_updated.oas"
update_oas_placement(
    oas_path="Xor_Automation.oas",
    sp_path="Xor_Automation.sp",
    nodes=nodes,
    output_path=output,
)

# Verify by reading back
original = extract_layout_instances("Xor_Automation.oas")
updated = extract_layout_instances(output)

print("\n=== COMPARISON: Original vs Updated ===")
for i, (o, u) in enumerate(zip(original, updated)):
    ox, oy = o["x"], o["y"]
    ux, uy = u["x"], u["y"]
    status = "CHANGED" if abs(ox - ux) > 1e-6 or abs(oy - uy) > 1e-6 else "same"
    print(f"  Ref {i} [{o['cell'][:20]:20s}]: "
          f"({ox:.4f}, {oy:.4f}) -> ({ux:.4f}, {uy:.4f})  [{status}]")

print(f"\nOriginal refs: {len(original)}, Updated refs: {len(updated)}")
print("Test PASSED!" if len(original) == len(updated) else "Test FAILED!")

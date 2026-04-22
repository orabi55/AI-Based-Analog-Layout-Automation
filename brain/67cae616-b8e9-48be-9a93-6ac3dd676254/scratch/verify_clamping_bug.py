import json
import os
import sys

# Ensure project root is on the path
_project_root = os.path.abspath('.')
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from ai_agent.ai_chat_bot.agents.drc_critic import run_drc_check, compute_prescriptive_fixes

file_path = r'c:\Users\DELL G3\Desktop\GP\Automation\AI-Automation-New\examples\current_mirror\Current_Mirror_CM_initial_placement.json'
with open(file_path, 'r') as f:
    data = json.load(f)

nodes = data.get('nodes', [])

# Intentionally flip a device to trigger a ROW_ERROR
# Move MM2_f1 (NMOS) to a high Y (e.g. 0.0)
for n in nodes:
    if n['id'] == 'MM2_f1':
        n['geometry']['y'] = 0.0  # Misplaced! NMOS is currently at -0.823

print("--- Testing Fix with Negative Coordinate Layout ---")
result = run_drc_check(nodes, gap_px=0.0)
print(f"Violations found: {len(result['violations'])}")

fixes = compute_prescriptive_fixes(result, gap_px=0.0, nodes=nodes)
print("\nGenerated Fixes:")
for f in fixes:
    if f['device'] == 'MM2_f1':
        print(f"  Fix for MM2_f1: y={f['y']}")
        # Check if it was clamped to 0.0
        if f['y'] == 0.0:
            print("  BUG CONFIRMED: Fix clamped to 0.0, which is still ABOVE PMOS (-0.245)!")

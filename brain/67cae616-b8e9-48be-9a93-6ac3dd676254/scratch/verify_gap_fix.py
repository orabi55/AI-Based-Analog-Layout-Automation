"""
Verify the PMOS/NMOS gap fix in drc_critic.py.
Simulates a fully-inverted layout: NMOS above PMOS.
After the fix, PMOS should be exactly one ROW_PITCH above NMOS.
"""
import sys, os
sys.path.insert(0, r'c:\Users\DELL G3\Desktop\GP\Automation\AI-Automation-New')

from ai_agent.ai_chat_bot.agents.drc_critic import run_drc_check, compute_prescriptive_fixes

ROW_PITCH = 0.668

# Simulate a fully-inverted layout:
# PMOS at y = -0.823 (should be TOP), NMOS at y = -0.245 (should be BOTTOM)
nodes = [
    {"id": "MM3_f1", "type": "pmos", "geometry": {"x": 0.0,   "y": -0.823, "width": 0.294, "height": 0.568}},
    {"id": "MM4_f1", "type": "pmos", "geometry": {"x": 0.294, "y": -0.823, "width": 0.294, "height": 0.568}},
    {"id": "MM0_f1", "type": "nmos", "geometry": {"x": 0.0,   "y": -0.245, "width": 0.294, "height": 0.568}},
    {"id": "MM1_f1", "type": "nmos", "geometry": {"x": 0.294, "y": -0.245, "width": 0.294, "height": 0.568}},
]

result = run_drc_check(nodes, gap_px=0.0)
fixes = compute_prescriptive_fixes(result, gap_px=0.0, nodes=nodes)

print("=== DRC Result ===")
print(f"Violations: {len(result['violations'])}")
for v in result['violations']:
    print(f"  • {v}")

print("\n=== Generated Fixes ===")
fix_map = {}
for f in fixes:
    print(f"  {f['device']}: → y={f['y']:.4f}")
    fix_map[f['device']] = f['y']

print("\n=== Post-Fix Row Analysis ===")
pmos_fixed = [fix_map.get(d, -0.823) for d in ['MM3_f1', 'MM4_f1']]
nmos_fixed = [fix_map.get(d, -0.245) for d in ['MM0_f1', 'MM1_f1']]

pmos_y = pmos_fixed[0]
nmos_y = nmos_fixed[0]
gap = pmos_y - nmos_y

print(f"  PMOS final y: {pmos_y:.4f}")
print(f"  NMOS final y: {nmos_y:.4f}")
print(f"  Gap (PMOS - NMOS): {gap:.4f} (expected {ROW_PITCH:.4f})")

if abs(gap - ROW_PITCH) < 0.001:
    print("\n✅ PASS: PMOS is exactly one ROW_PITCH above NMOS — no gap!")
elif pmos_y > nmos_y:
    print(f"\n⚠️  WARNING: Correct ordering (PMOS > NMOS) but gap={gap:.4f} ≠ {ROW_PITCH:.4f}")
else:
    print(f"\n❌ FAIL: PMOS is still below or at NMOS level!")

import sys
sys.path.insert(0, r'c:\Users\DELL G3\Desktop\GP\Automation\AI-Automation-New')
from ai_agent.ai_chat_bot.agents.drc_critic import run_drc_check, compute_prescriptive_fixes

print("=== TEST 1: Single NMOS row above single PMOS row ===")
nodes = [
    {"id": "P1", "type": "pmos", "geometry": {"x": 0.0, "y": -0.823, "width": 0.294, "height": 0.568}},
    {"id": "N1", "type": "nmos", "geometry": {"x": 0.0, "y": -0.245, "width": 0.294, "height": 0.568}},
]
result = run_drc_check(nodes, gap_px=0.0)
fixes = compute_prescriptive_fixes(result, gap_px=0.0, nodes=nodes)
fm = {f["device"]: f["y"] for f in fixes}
py = fm.get("P1", -0.823)
ny = fm.get("N1", -0.245)
gap = py - ny  # should equal device height for zero gap
pmos_h = 0.568
print(f"  PMOS final y: {py:.4f} (unchanged from -0.823)")
print(f"  NMOS final y: {ny:.4f}")
print(f"  Distance (PMOS - NMOS): {gap:.4f} (expected = device_height = {pmos_h})")
status1 = "PASS" if abs(gap - pmos_h) < 0.002 else "FAIL"
print(f"  Status: {status1}")

print()
print("=== TEST 2: Two NMOS rows above two PMOS rows (multi-row) ===")
# PMOS rows at -0.823 and -0.255 (correct positions but NMOS is above them)
# NMOS rows at 0.313 and 0.881 (inverted -- they're above PMOS)
nodes2 = [
    {"id": "P1", "type": "pmos", "geometry": {"x": 0.0, "y": -0.823, "width": 0.294, "height": 0.568}},
    {"id": "P2", "type": "pmos", "geometry": {"x": 0.0, "y": -0.255, "width": 0.294, "height": 0.568}},
    {"id": "N1", "type": "nmos", "geometry": {"x": 0.0, "y":  0.313, "width": 0.294, "height": 0.568}},
    {"id": "N2", "type": "nmos", "geometry": {"x": 0.0, "y":  0.881, "width": 0.294, "height": 0.568}},
]
result2 = run_drc_check(nodes2, gap_px=0.0)
fixes2 = compute_prescriptive_fixes(result2, gap_px=0.0, nodes=nodes2)
fm2 = {f["device"]: f["y"] for f in fixes2}
p1y = fm2.get("P1", -0.823)
p2y = fm2.get("P2", -0.255)
n1y = fm2.get("N1", 0.313)
n2y = fm2.get("N2", 0.881)
print(f"  P1 final y: {p1y:.4f}  P2 final y: {p2y:.4f}")
print(f"  N1 final y: {n1y:.4f}  N2 final y: {n2y:.4f}")
lowest_pmos = min(p1y, p2y)
top_nmos = max(n1y, n2y)
gap2 = lowest_pmos - top_nmos
print(f"  Lowest PMOS y: {lowest_pmos:.4f}")
print(f"  Highest corrected NMOS y: {top_nmos:.4f}")
print(f"  Distance (lowest PMOS - highest NMOS): {gap2:.4f} (expected = 0.568 for zero gap)")
status2 = "PASS" if abs(gap2 - 0.568) < 0.002 else "FAIL"
print(f"  Status: {status2}")
print()
nmos_row_gap = abs(n1y - n2y)
print(f"  Gap between two NMOS rows: {nmos_row_gap:.4f} (expected = 0.568 for zero gap)")
status3 = "PASS" if abs(nmos_row_gap - 0.568) < 0.002 else "FAIL"
print(f"  Status (multi-row stacking): {status3}")

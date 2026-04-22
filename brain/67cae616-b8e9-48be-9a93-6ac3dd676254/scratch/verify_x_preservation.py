import sys
sys.path.insert(0, r'c:\Users\DELL G3\Desktop\GP\Automation\AI-Automation-New')
from ai_agent.ai_chat_bot.agents.drc_critic import run_drc_check, compute_prescriptive_fixes

print("=== TEST: Row fix must NOT change X coordinates ===")
nodes = [
    {"id": "DUMMYN", "type": "nmos", "geometry": {"x": 0.0,   "y": 0.245, "width": 0.294, "height": 0.568}},
    {"id": "MM0_f1", "type": "nmos", "geometry": {"x": 0.294, "y": 0.245, "width": 0.294, "height": 0.568}},
    {"id": "MM1_f1", "type": "nmos", "geometry": {"x": 0.588, "y": 0.245, "width": 0.294, "height": 0.568}},
    {"id": "MM2_f1", "type": "nmos", "geometry": {"x": 0.882, "y": 0.245, "width": 0.294, "height": 0.568}},
    {"id": "DUMMYP", "type": "pmos", "geometry": {"x": 0.0,   "y": -0.245, "width": 0.294, "height": 0.568}},
    {"id": "MM3_f1", "type": "pmos", "geometry": {"x": 0.294, "y": -0.245, "width": 0.294, "height": 0.568}},
    {"id": "MM4_f1", "type": "pmos", "geometry": {"x": 0.588, "y": -0.245, "width": 0.294, "height": 0.568}},
    {"id": "MM5_f1", "type": "pmos", "geometry": {"x": 0.882, "y": -0.245, "width": 0.294, "height": 0.568}},
]
orig_x = {n["id"]: n["geometry"]["x"] for n in nodes}
result = run_drc_check(nodes, gap_px=0.0)
fixes = compute_prescriptive_fixes(result, gap_px=0.0, nodes=nodes)
fix_map = {f["device"]: f for f in fixes}

vcount = len(result["violations"])
fcount = len(fixes)
print(f"  Violations detected: {vcount}")
print(f"  Fixes generated: {fcount}")
print()
print("  Device        | orig_x | fix_x  | fix_y  | X_ok?")
print("  " + "-" * 57)
all_x_ok = True
for dev_id in sorted(orig_x.keys()):
    ox = orig_x[dev_id]
    if dev_id in fix_map:
        fx = fix_map[dev_id]["x"]
        fy = fix_map[dev_id]["y"]
        changed = abs(fx - ox) > 0.001
        if changed:
            all_x_ok = False
        flag = "FAIL - X CHANGED!" if changed else "ok"
        print(f"  {dev_id:<14} | {ox:6.3f} | {fx:6.3f} | {fy:6.3f} | {flag}")
    else:
        print(f"  {dev_id:<14} | {ox:6.3f} | (no fix — already correct)")

print()
verdict = "PASS - all X coords preserved, only Y changed" if all_x_ok else "FAIL - X was modified!"
print(f"  RESULT: {verdict}")

# Verify PMOS is above NMOS in fixed layout
pmos_ys = [fix_map[d]["y"] for d in fix_map if any(n["id"] == d and n["type"] == "pmos" for n in nodes)]
nmos_ys = [fix_map[d]["y"] for d in fix_map if any(n["id"] == d and n["type"] == "nmos" for n in nodes)]
if pmos_ys and nmos_ys:
    ok = min(pmos_ys) > max(nmos_ys)
    print(f"  Row order: min(PMOS)={min(pmos_ys):.3f} > max(NMOS)={max(nmos_ys):.3f} -> {'OK' if ok else 'VIOLATION!'}")

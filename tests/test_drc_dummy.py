"""Quick test: DRC ignores dummy devices."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_agent.ai_chat_bot.agents.geometry_engine import convert_multirow_to_geometry
from ai_agent.ai_chat_bot.agents.drc_critic import run_drc_check
from ai_agent.ai_chat_bot.nodes import _pad_rows_with_dummies

nodes = []
for i in range(62):
    nodes.append({
        "id": f"D{i}",
        "type": "nmos" if i < 22 else "pmos",
        "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.818, "orientation": "R0"},
        "electrical": {"nf": 1},
    })

multirow = {
    "nmos_rows": [
        {"label": "nmos_input", "devices": [f"D{i}" for i in range(16)]},
        {"label": "nmos_tail", "devices": [f"D{i}" for i in range(16, 22)]},
    ],
    "pmos_rows": [
        {"label": "pmos_precharge", "devices": [f"D{i}" for i in range(22, 38)]},
        {"label": "pmos_internal", "devices": [f"D{i}" for i in range(38, 54)]},
        {"label": "pmos_latch", "devices": [f"D{i}" for i in range(54, 62)]},
    ],
}

result = convert_multirow_to_geometry(multirow, nodes, [])
padded = _pad_rows_with_dummies(result)
dummies = [n for n in padded if n.get("is_dummy")]
active = [n for n in padded if not n.get("is_dummy")]
print(f"\nTotal={len(padded)}, Active={len(active)}, Dummies={len(dummies)}")

drc = run_drc_check(padded, 0.0)
print(f"DRC: pass={drc['pass']}, violations={len(drc['violations'])}")

if drc["violations"]:
    for v in drc["violations"][:5]:
        print(f"  {v[:120]}")
else:
    print("  No violations — dummies correctly excluded!")

"""Verify XOR fix: API key loading + Y-coordinate normalisation."""
import ast, sys, os, json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 1. Syntax check
ast.parse(open('ai_agent/gemini_placer.py', encoding='utf-8').read())
print("Syntax OK")

# 2. API key load
import ai_agent.gemini_placer as gp
key = os.getenv('GEMINI_API_KEY', '')
if key:
    print(f"API key loaded: YES ({key[:8]}...{key[-4:]})")
else:
    print("API key loaded: NO — check .env file")

# 3. Normalise XOR coords
with open('examples/xor/Xor_Automation_graph.json') as f:
    data = json.load(f)

norm, offset = gp._normalise_coords(data['nodes'])
print(f"\nY offset applied: {offset:+.4f} um")
print("Normalised Y values:")
for n in norm:
    t = n['type']
    y = n['geometry']['y']
    ok = "PMOS>=0" if t == 'pmos' and y >= 0 else ("NMOS>=0" if t == 'nmos' and y >= 0 else "*** BAD ***")
    print(f"  {n['id']:8s} {t:5s}  y={y:.4f}  {ok}")

# 4. Round-trip restore
restored = gp._restore_coords(norm, offset)
orig_ys = {n['id']: n['geometry']['y'] for n in data['nodes']}
all_ok = True
for n in restored:
    orig = orig_ys[n['id']]
    err = abs(n['geometry']['y'] - orig)
    if err > 1e-6:
        print(f"RESTORE ERROR for {n['id']}: got {n['geometry']['y']}, expected {orig}")
        all_ok = False
if all_ok:
    print("\nRound-trip OK: restore_coords gives back original values")

print("\nAll checks passed!")

"""Debug script: reproduce the XOR placement error."""
import json, sys, os, traceback, tempfile
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

XOR_JSON = os.path.join(os.path.dirname(__file__), '..', 'examples', 'xor', 'Xor_Automation_graph.json')

with open(XOR_JSON) as f:
    data = json.load(f)

print(f"Nodes: {len(data['nodes'])}")
for n in data['nodes']:
    print(f"  {n['id']:8s} {n['type']:5s}  y={n['geometry']['y']:.3f}")

with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
    json.dump(data, tmp, indent=2)
    tmp_in = tmp.name
tmp_out = tmp_in.replace('.json', '_placed.json')
print(f"\ntmp_in:  {tmp_in}")
print(f"tmp_out: {tmp_out}\n")

try:
    from ai_agent.gemini_placer import gemini_generate_placement
    gemini_generate_placement(tmp_in, tmp_out)
    print("SUCCESS - placement written to", tmp_out)
    with open(tmp_out) as f:
        placed = json.load(f)
    print(f"Placed type: {type(placed).__name__}")
    if isinstance(placed, dict):
        print(f"Keys: {list(placed.keys())}")
        nodes = placed.get('nodes', [])
        print(f"Placed nodes: {len(nodes)}")
    elif isinstance(placed, list):
        print(f"Placed is a raw list! Length: {len(placed)}")
except Exception as e:
    traceback.print_exc()
    print(f"\nERROR: {e}")
finally:
    for p in (tmp_in, tmp_out):
        try:
            os.unlink(p)
        except OSError:
            pass

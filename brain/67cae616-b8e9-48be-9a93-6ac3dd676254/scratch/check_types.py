import json
file_path = r'c:\Users\DELL G3\Desktop\GP\Automation\AI-Automation-New\examples\current_mirror\Current_Mirror_CM_graph.json'
with open(file_path, 'r') as f:
    data = json.load(f)

nodes = data.get('nodes', [])
types_found = {}
for n in nodes:
    did = n.get('id', '')
    dtype = n.get('type', '')
    # Get the parent name (e.g., MM2_f1 -> MM2)
    parent = did.split('_')[0]
    if parent not in types_found:
        types_found[parent] = dtype

for p, t in sorted(types_found.items()):
    print(f'{p}: {t}')

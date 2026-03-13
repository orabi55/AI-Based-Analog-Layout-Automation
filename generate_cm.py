import json
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from parser.layout_reader import extract_layout_instances
from parser.netlist_reader import read_netlist
from parser.circuit_graph import build_circuit_graph

def main():
    root = os.path.dirname(os.path.abspath(__file__))
    oas_file = os.path.join(root, "Current_Mirror_CM.oas")
    sp_file = os.path.join(root, "Current_Mirror_CM.sp")

    # 1. Parse OAS
    instances = extract_layout_instances(oas_file)
    layout_map = {inst["cell"]: inst for inst in instances}

    # 2. Parse SP
    netlist = read_netlist(sp_file)

    nodes = []
    # 3. Combine
    for dev_name, dev in netlist.devices.items():
        # Match SPICE device name to Layout cell name
        # Often layout names have a prefix or suffix, but let's try exact match first
        inst = layout_map.get(dev_name)
        if not inst:
            # Maybe layout cells have different names, let's just map them.
            # If not found, create a dummy geometry
            geom = {
                "x": 0.0,
                "y": 0.0,
                "width": 0.294,
                "height": 0.668,
                "orientation": "R0"
            }
        else:
            geom = {
                "x": inst["x"],
                "y": inst["y"],
                "width": inst["width"],
                "height": inst["height"],
                "orientation": inst["orientation"]
            }

        electrical = {
            "l": dev.params.get("l", 1.4e-08),
            "nf": dev.params.get("nf", 1),
            "nfin": dev.params.get("nfin", 4.0)
        }

        # SPICE type is 'n08' so 'nmos'
        dev_type = "nmos" if "n" in dev.type.lower() else "pmos"
        
        nodes.append({
            "id": dev_name,
            "type": dev_type,
            "electrical": electrical,
            "geometry": geom
        })

    # 4. Generate edges
    G = build_circuit_graph(netlist)
    edges = []
    for u, v, data in G.edges(data=True):
        edges.append({
            "source": u,
            "target": v,
            "net": data.get("net", "")
        })

    out_data = {
        "nodes": nodes,
        "edges": edges
    }

    out_file = os.path.join(root, "CM_initial_placement.json")
    with open(out_file, "w") as f:
        json.dump(out_data, f, indent=4)
    
    out_file2 = os.path.join(root, "cm_layout_graph.json")
    with open(out_file2, "w") as f:
        json.dump(out_data, f, indent=4)
        
    print("Successfully generated CM_initial_placement.json and cm_layout_graph.json")
    
if __name__ == "__main__":
    main()

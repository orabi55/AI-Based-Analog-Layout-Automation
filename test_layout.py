from parser.netlist_reader import read_netlist
from parser.layout_reader import extract_layout_instances
from parser.device_matcher import match_devices
from parser.merged_graph import build_merged_graph
from export.export_json import graph_to_json


# Files
netlist_file = "Xor_Automation.sp"
layout_file  = "Xor_Automation.oas"

# Read netlist
nl = read_netlist(netlist_file)

# Read layout
layout_devices = extract_layout_instances(layout_file)

# Match
mapping = match_devices(nl, layout_devices)

print("\n--- DEVICE MAPPING ---")
for k in list(mapping.keys())[:10]:
    print(k, "-> layout index", mapping[k])





G = build_merged_graph(nl, layout_devices, mapping)

print("\n--- MERGED GRAPH SAMPLE NODES ---")
for n, data in list(G.nodes(data=True))[:5]:
    print(n, data)




graph_to_json(G, "xor_layout_graph.json")






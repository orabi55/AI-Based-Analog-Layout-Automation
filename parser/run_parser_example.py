import os
import sys

# Ensure imports work from the root directory
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from parser.netlist_reader import read_netlist
from parser.layout_reader import extract_layout_instances
from parser.device_matcher import match_devices
from parser.circuit_graph import build_merged_graph

def run_parser_pipeline():
    print("Starting Parser Walkthrough...")
    base_dir = os.path.abspath(os.path.dirname(__file__))
    spice_path = os.path.join(base_dir, "examples", "xor", "Xor_Automation.sp")
    layout_path = os.path.join(base_dir, "examples", "xor", "Xor_Automation.oas")

    if not os.path.exists(spice_path) or not os.path.exists(layout_path):
        print(f"Error: Could not find example files in {base_dir}/examples/xor/")
        return

    print("\n" + "="*50)
    print("STEP 1: Parse Netlist (netlist_reader.py)")
    print("="*50)
    nl = read_netlist(spice_path)
    print(f"Extracted {len(nl.devices)} devices from SPICE.")
    for name, dev in nl.devices.items():
        print(f" - {name}: Type={dev.type}, Pins={dev.pins}")

    print("\n" + "="*50)
    print("STEP 2: Extract Layout Instances (layout_reader.py)")
    print("="*50)
    layout_devices = extract_layout_instances(layout_path)
    print(f"Extracted {len(layout_devices)} geometric shapes from OASIS.")
    for idx, geo in enumerate(layout_devices):
        print(f" - Index {idx}: Cell='{geo['cell']}', Position=({geo['x']}, {geo['y']})")

    print("\n" + "="*50)
    print("STEP 3: Match Netlist to Layout (device_matcher.py)")
    print("="*50)
    mapping = match_devices(nl, layout_devices)
    for dev_name, layout_idx in mapping.items():
        cell_name = layout_devices[layout_idx]['cell']
        print(f" - Netlist Device '{dev_name}' logically matches Layout Index {layout_idx} ('{cell_name}')")

    print("\n" + "="*50)
    print("STEP 4: Build Merged Circuit Graph (circuit_graph.py)")
    print("="*50)
    merged_graph = build_merged_graph(nl, layout_devices, mapping)
    print(f"Final Graph created with {merged_graph.number_of_nodes()} nodes and {merged_graph.number_of_edges()} edges.")
    
    for node, data in merged_graph.nodes(data=True):
        print(f" - Node {node}: Physical Type={data.get('type')}, Position=({data.get('x')}, {data.get('y')})")
    
    for u, v, data in merged_graph.edges(data=True):
        print(f" - Electrical Edge {u} <-> {v}: Connected via Net={data.get('net')}")
        
    print("\nPIPELINE COMPLETED SUCCESSFULLY!")

if __name__ == "__main__":
    run_parser_pipeline()

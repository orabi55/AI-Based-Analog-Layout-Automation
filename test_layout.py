"""
Test pipeline: read netlist + layout, match devices, build graph, export JSON.

Usage:
    python test_layout.py                       # default: xor example
    python test_layout.py examples/std_cell     # std_cell example
    python test_layout.py examples/comparator   # comparator example
"""

import sys
import os
import glob

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from parser.netlist_reader import read_netlist
from parser.layout_reader import extract_layout_instances
from parser.device_matcher import match_devices
from parser.merged_graph import build_merged_graph
from export.export_json import graph_to_json


def run_pipeline(example_dir):
    """Run the full pipeline for files in the given directory."""
    # Find .sp and .oas files
    sp_files = glob.glob(os.path.join(example_dir, "*.sp"))
    oas_files = (glob.glob(os.path.join(example_dir, "*.oas")) +
                 glob.glob(os.path.join(example_dir, "*.gds")))

    if not sp_files:
        print(f"ERROR: No .sp file found in {example_dir}")
        return
    if not oas_files:
        print(f"ERROR: No .oas/.gds file found in {example_dir}")
        return

    netlist_file = sp_files[0]
    layout_file = oas_files[0]
    base_name = os.path.splitext(os.path.basename(netlist_file))[0]
    output_json = os.path.join(example_dir, f"{base_name}_layout_graph.json")

    print(f"\n{'='*60}")
    print(f"Example: {base_name}")
    print(f"  Netlist: {netlist_file}")
    print(f"  Layout:  {layout_file}")
    print(f"{'='*60}")

    # Read netlist
    nl = read_netlist(netlist_file)

    # Read layout
    layout_devices = extract_layout_instances(layout_file)

    # Match
    mapping = match_devices(nl, layout_devices)

    print(f"\n--- DEVICE MAPPING ({len(mapping)} entries) ---")
    for k in list(mapping.keys())[:10]:
        print(f"  {k} -> layout index {mapping[k]}")
    if len(mapping) > 10:
        print(f"  ... and {len(mapping) - 10} more")

    # Build merged graph
    G = build_merged_graph(nl, layout_devices, mapping)

    print(f"\n--- MERGED GRAPH: {G.number_of_nodes()} nodes, "
          f"{G.number_of_edges()} edges ---")
    for n, data in list(G.nodes(data=True))[:3]:
        print(f"  {n}: {data}")

    # Export JSON
    graph_to_json(G, output_json)
    print(f"\nJSON exported to {output_json}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        example_dir = sys.argv[1]
    else:
        example_dir = os.path.join("examples", "xor")

    run_pipeline(example_dir)

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
import pytest
import copy

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parser.netlist_reader import read_netlist
from parser.layout_reader import extract_layout_instances
from parser.device_matcher import match_devices
from parser.merged_graph import build_merged_graph
from export.export_json import graph_to_json
from ai_agent.drc_critic import run_drc_check
from ai_agent.tools import tool_validate_device_count
from ai_agent.orchestrator import _apply_cmds_to_nodes
from ai_agent.orchestrator import _extract_cmd_blocks


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


def test_drc_check_overlap():
    nodes = [
        {"id": "MM1", "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.668}},
        {"id": "MM2", "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.668}}
    ]
    result = run_drc_check(nodes, gap_px=0.0)
    assert result["pass"] is False
    assert "overlapped" in "\n".join(result["violations"]) or "overlap" in "\n".join(result["violations"]).lower()

def test_drc_check_pass():
    nodes = [
        {"id": "MM1", "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.668}},
        {"id": "MM2", "geometry": {"x": 0.400, "y": 0.0, "width": 0.294, "height": 0.668}}
    ]
    result = run_drc_check(nodes, gap_px=0.0)
    assert result["pass"] is True

def test_tool_validate_device_count():
    orig_nodes = [{"id": "MM1"}, {"id": "MM2"}]
    new_nodes = [{"id": "MM1"}]
    result = tool_validate_device_count(orig_nodes, new_nodes)
    assert result["pass"] is False
    assert "missing" in str(result["missing"]).lower() or "missing" in str(result).lower()

def test_apply_cmds_to_nodes():
    nodes = [
        {"id": "MM1", "geometry": {"x": 0.0, "y": 0.0}},
        {"id": "MM2", "geometry": {"x": 1.0, "y": 0.0}}
    ]
    cmds = [{"action": "swap", "device_a": "MM1", "device_b": "MM2"}]
    new_nodes = _apply_cmds_to_nodes(nodes, cmds)
    assert new_nodes[0]["geometry"]["x"] == 1.0
    assert new_nodes[1]["geometry"]["x"] == 0.0

def test_extract_cmd_blocks():
    text = """Here is the command:
[CMD]{"action": "move", "device": "MM1", "x": 1.2}[/CMD]
    """
    cmds = _extract_cmd_blocks(text)
    assert len(cmds) == 1
    assert cmds[0]["action"] == "move"

def test_extract_cmd_blocks_malformed():
    text = """Here is the command:
[CMD]{"action": "move", "device": "MM1", "x": }[/CMD]
    """
    cmds = _extract_cmd_blocks(text)
    assert len(cmds) == 0

if __name__ == "__main__":
    if len(sys.argv) > 1:
        example_dir = sys.argv[1]
    else:
        example_dir = os.path.join("examples", "xor")

    run_pipeline(example_dir)

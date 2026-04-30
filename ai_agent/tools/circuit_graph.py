"""
Circuit Graph Tool Wrapper
==========================
Provides utilities for building a networkx circuit graph from SPICE netlist files.

Functions:
- build_circuit_graph (tool_build_circuit_graph): Reads a netlist and constructs a circuit graph.
  - Inputs: sp_file_path (str)
  - Outputs: networkx.Graph object or None.
"""

import os
import sys


def build_circuit_graph(sp_file_path):
    """Build a networkx circuit graph from a SPICE .sp file.

    Args:
        sp_file_path (str): absolute path to the .sp netlist file.

    Returns:
        networkx.Graph | None: the circuit graph, or None on failure.
    """
    if not sp_file_path or not os.path.isfile(sp_file_path):
        return None
    try:
        project_root = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..")
        )
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from parser.netlist_reader import read_netlist
        from parser.circuit_graph import build_circuit_graph
        netlist = read_netlist(sp_file_path)
        return build_circuit_graph(netlist)
    except Exception as exc:
        print(f"[TOOLS] build_circuit_graph failed: {exc}")
        return None


# Backward-compatible alias
tool_build_circuit_graph = build_circuit_graph

"""
Routing Pre-Viewer Node
======================
A LangGraph node that evaluates the routing complexity of a placement 
using estimated wire lengths and net-crossing counts.

Functions:
- node_routing_previewer: Executes the routing scoring logic and updates the state.
  - Inputs: state (dict)
  - Outputs: routing result updates for the state.
"""

import time
from ai_agent.agents.routing_previewer import score_routing, format_routing_for_llm
from ai_agent.nodes._shared import vprint, ip_step


def node_routing_previewer(state):
    t0 = time.time()
    vprint("\n" + "─" * 60, flush=True)
    vprint("  ROUTING PRE-VIEWER", flush=True)
    vprint("─" * 60, flush=True)

    nodes = state.get("placement_nodes", [])
    edges = state.get("edges", [])
    terminal_nets = state.get("terminal_nets", {})

    routing_result = score_routing(nodes, edges or [], terminal_nets or {})
    vprint(f"[ROUTING] Score: {routing_result.get('score', 'N/A')}", flush=True)

    elapsed = time.time() - t0
    ip_step("5.5/5 Routing Pre-Viewer", f"score={routing_result.get('score')} ({elapsed:.1f}s)")

    return {"routing_result": routing_result}

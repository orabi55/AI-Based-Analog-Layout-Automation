"""
Scoring Tool Wrapper
====================
Provides a wrapper for the routing complexity and wire-length estimation engine.

Functions:
- score_net_crossings (tool_score_net_crossings): Estimates routing quality from node and edge data.
  - Inputs: nodes (list), edges (list), terminal_nets (dict)
  - Outputs: routing analysis dictionary.
"""


def score_net_crossings(nodes, edges, terminal_nets):
    """Estimate routing complexity with the pure-Python heuristic.

    Returns:
        dict: see ai_agent.agents.routing_previewer.score_routing() for schema.
    """
    try:
        from ai_agent.agents.routing_previewer import score_routing
        return score_routing(nodes, edges or [], terminal_nets or {})
    except Exception as exc:
        print(f"[TOOLS] score_net_crossings failed: {exc}")
        return {"score": 0, "worst_nets": [], "net_spans": {}, "summary": str(exc)}


# Backward-compatible alias
tool_score_net_crossings = score_net_crossings

"""
Graph Routing Logic
==================
Defines the conditional routing logic for navigating between nodes in the 
LangGraph state machine.

Functions:
- route_after_drc: Determines whether to retry DRC or proceed to human review.
  - Inputs: state (LayoutState)
  - Outputs: name of the next node.
- route_after_human: Determines whether to save the layout or retry placement.
  - Inputs: state (LayoutState)
  - Outputs: name of the next node.
- route_by_mode: Directs the entry point based on the execution mode.
  - Inputs: state (LayoutState)
  - Outputs: name of the first operational node.
"""

from ai_agent.graph.state import LayoutState

MAX_ROUTING_PASSES = 3
MAX_DRC_RETRIES = 2


def route_after_drc(state: LayoutState):
    """After DRC critic: loop back if violations remain and retries available."""
    if state.get("drc_pass", False):
        return "node_human_viewer"

    retry_count = state.get("drc_retry_count", 0)
    if retry_count < MAX_DRC_RETRIES:
        return "node_drc_critic"

    return "node_human_viewer"


def route_after_human(state: LayoutState):
    """After human viewer: save if approved, loop back to placement if rejected."""
    if state.get("approved", False):
        return "node_save_to_rag"
    return "node_placement_specialist"


def route_by_mode(state: LayoutState):
    """Entry routing: select pipeline branch based on execution mode."""
    mode = state.get("mode", "initial")
    if mode == "initial":
        return "full_pipeline"
    return "interactive"

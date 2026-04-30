"""
Unified LangGraph Builder
=========================
Constructs the LangGraph StateGraph for the layout automation pipeline.
Supports "initial" auto-run and "chat" interactive modes.

Functions:
- build_layout_graph: Constructs and compiles the LayoutState graph.
  - Inputs: mode (str: "initial" or "chat")
  - Outputs: tuple (compiled_app, memory)
"""

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from ai_agent.graph.state import LayoutState
from ai_agent.graph.edges import route_after_drc, route_after_human, route_by_mode
from ai_agent.nodes import (
    node_topology_analyst,
    node_strategy_selector,
    node_placement_specialist,
    node_finger_expansion,
    node_drc_critic,
    node_routing_previewer,
    node_human_viewer,
    node_save_to_rag,
)


def build_layout_graph(mode: str = "initial"):
    """Build a fresh LangGraph with its own MemorySaver per run.

    Args:
        mode: "initial" for full auto-run, "chat" for interactive mode.

    Returns:
        (compiled_app, memory) tuple.
    """
    memory = MemorySaver()
    builder = StateGraph(LayoutState)

    # ── Register all Nodes ──
    builder.add_node("node_topology_analyst", node_topology_analyst)
    builder.add_node("node_strategy_selector", node_strategy_selector)
    builder.add_node("node_placement_specialist", node_placement_specialist)
    builder.add_node("node_finger_expansion", node_finger_expansion)
    builder.add_node("node_drc_critic", node_drc_critic)
    builder.add_node("node_routing_previewer", node_routing_previewer)
    builder.add_node("node_human_viewer", node_human_viewer)
    builder.add_node("node_save_to_rag", node_save_to_rag)

    # ── Mode-based entry routing ──
    builder.add_conditional_edges(START, route_by_mode, {
        "full_pipeline": "node_topology_analyst",
        "interactive":   "node_topology_analyst",
    })

    # ── Linear flow (shared by both modes) ──
    builder.add_edge("node_topology_analyst", "node_strategy_selector")
    builder.add_edge("node_strategy_selector", "node_placement_specialist")
    builder.add_edge("node_placement_specialist", "node_finger_expansion")
    builder.add_edge("node_finger_expansion", "node_routing_previewer")
    builder.add_edge("node_routing_previewer", "node_drc_critic")

    # ── Conditional / cyclic flows ──
    builder.add_conditional_edges("node_drc_critic", route_after_drc)

    # ── Terminal ──
    if mode == "initial":
        # Initial placement: go directly to END after human viewer
        # (no interactive review, no RAG save)
        builder.add_edge("node_human_viewer", END)
    else:
        # Chat mode: human viewer routes to save or back to placement
        builder.add_conditional_edges("node_human_viewer", route_after_human)
        builder.add_edge("node_save_to_rag", END)

    return builder.compile(checkpointer=memory), memory


# Backward compatibility: module-level app for legacy imports
app, _memory = build_layout_graph()

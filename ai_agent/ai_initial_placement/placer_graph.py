from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from ai_agent.ai_chat_bot.state import LayoutState
from ai_agent.ai_chat_bot.nodes import (
    node_topology_analyst,
    node_strategy_selector,
    node_placement_specialist,
    node_finger_expansion,
    node_drc_critic,
    node_human_viewer,
)
from ai_agent.ai_chat_bot.edges import route_after_drc


def build_placer_graph():
    """Build a FRESH placer graph with its own MemorySaver for each run.
    
    This prevents state leaks between runs that occurred when using a
    module-level singleton graph + shared MemorySaver.
    """
    memory = MemorySaver()
    builder = StateGraph(LayoutState)

    # 1. Register all Nodes
    builder.add_node("node_topology_analyst", node_topology_analyst)
    builder.add_node("node_strategy_selector", node_strategy_selector)
    builder.add_node("node_placement_specialist", node_placement_specialist)

    # Physics & Post-Processing
    builder.add_node("node_finger_expansion", node_finger_expansion) 

    # Critics & Viewers
    builder.add_node("node_drc_critic", node_drc_critic)
    builder.add_node("node_human_viewer", node_human_viewer)

    # 2. Linear & Entry Flows
    builder.add_edge(START, "node_topology_analyst")

    # (Intent classifier routes conditionally to topology_analyst or placement_specialist)
    builder.add_edge("node_topology_analyst", "node_strategy_selector")
    builder.add_edge("node_strategy_selector", "node_placement_specialist")

    # --- Placement Pipeline ---
    builder.add_edge("node_placement_specialist", "node_finger_expansion")
    builder.add_edge("node_finger_expansion", "node_drc_critic")

    # Conditional DRC routing: loop back if DRC fails and retries remain,
    # otherwise proceed to human viewer for review.
    builder.add_conditional_edges("node_drc_critic", route_after_drc)

    builder.add_edge("node_human_viewer", END)

    # 5. Compile Graph
    return builder.compile(checkpointer=memory), memory


# Legacy compatibility: module-level `app` for any existing imports
# (but new code should use build_placer_graph() for fresh instances)
app, _memory = build_placer_graph()
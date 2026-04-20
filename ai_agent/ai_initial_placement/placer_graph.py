from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from ai_agent.ai_chat_bot.state import LayoutState
from ai_agent.ai_chat_bot.nodes import *
from ai_agent.ai_chat_bot.edges import *

# Initialize checkpointer (required for human-in-the-loop interrupts)
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

builder.add_edge("node_drc_critic", "node_human_viewer")
builder.add_edge("node_human_viewer", END)

# 5. Compile Graph
app = builder.compile(checkpointer=memory)
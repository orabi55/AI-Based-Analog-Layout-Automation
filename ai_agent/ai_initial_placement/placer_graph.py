from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from ai_agent.ai_chat_bot.state import LayoutState
from ai_agent.ai_chat_bot.nodes import *
from ai_agent.ai_chat_bot.edges import *

# Initialize checkpointer (required for human-in-the-loop interrupts)
memory = MemorySaver()
builder = StateGraph(LayoutState)

# 1. Register all Nodes
builder.add_node("node_topology_analyst",   node_topology_analyst)
builder.add_node("node_strategy_selector",  node_strategy_selector)
builder.add_node("node_placement_specialist", node_placement_specialist)

# Physics & Post-Processing
builder.add_node("node_finger_expansion",   node_finger_expansion)
builder.add_node("node_sa_optimizer",       node_sa_optimizer)

# Critics & Viewers
builder.add_node("node_drc_critic",         node_drc_critic)
builder.add_node("node_human_viewer",       node_human_viewer)

# 2. Linear & Entry Flows
builder.add_edge(START, "node_topology_analyst")

# Topology → Strategy → Placement
builder.add_edge("node_topology_analyst",    "node_strategy_selector")
builder.add_edge("node_strategy_selector",   "node_placement_specialist")

# --- Placement Pipeline ---
# Placement → Geometry Engine (finger expansion)
builder.add_edge("node_placement_specialist", "node_finger_expansion")

# Finger Expansion → SA Optimizer (conditional — runs only if run_sa=True)
builder.add_conditional_edges(
    "node_finger_expansion",
    lambda state: "node_sa_optimizer" if state.get("run_sa", False) else "node_drc_critic",
    {
        "node_sa_optimizer": "node_sa_optimizer",
        "node_drc_critic":   "node_drc_critic",
    },
)

# SA Optimizer always feeds into DRC Critic
builder.add_edge("node_sa_optimizer", "node_drc_critic")

builder.add_edge("node_drc_critic", END)

# 5. Compile Graph
app = builder.compile(checkpointer=memory)
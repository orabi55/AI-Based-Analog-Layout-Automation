from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from ai_agent.state import LayoutState
from ai_agent.nodes import *
from ai_agent.edges import *

# Initialize checkpointer (required for human-in-the-loop interrupts)
memory = MemorySaver()
builder = StateGraph(LayoutState)

# 1. Register all Nodes
builder.add_node("node_topology_analyst", node_topology_analyst)
builder.add_node("node_strategy_selector", node_strategy_selector)
builder.add_node("node_placement_specialist", node_placement_specialist)

# Physics & Post-Processing
builder.add_node("node_deterministic_optimizer", node_deterministic_optimizer)
builder.add_node("node_finger_expansion", node_finger_expansion) 

# Critics & Viewers
builder.add_node("node_drc_critic", node_drc_critic)
builder.add_node("node_routing_previewer", node_routing_previewer)
builder.add_node("node_human_viewer", node_human_viewer)
builder.add_node("node_save_to_rag", node_save_to_rag)

# 2. Linear & Entry Flows
builder.add_edge(START, "node_topology_analyst")

# (Intent classifier routes conditionally to topology_analyst or placement_specialist)

builder.add_edge("node_topology_analyst", "node_strategy_selector")
builder.add_edge("node_strategy_selector", "node_placement_specialist")

# --- Placement Pipeline ---
# NOTE: If you decide to completely skip the deterministic optimizer, 
# comment out the next two lines and uncomment the direct edge below.
# builder.add_edge("node_placement_specialist", "node_deterministic_optimizer")
# builder.add_edge("node_deterministic_optimizer", "node_finger_expansion")
builder.add_edge("node_placement_specialist", "node_finger_expansion") # <--- Direct edge

builder.add_edge("node_finger_expansion", "node_drc_critic")

# 3. Conditional / Cyclic Flows

# Loops back to node_drc_critic if violations exist and retries < MAX_DRC_RETRIES
builder.add_conditional_edges("node_drc_critic", route_after_drc)

# Loops back to node_routing_previewer if hill-climbing passes < MAX_ROUTING_PASSES
builder.add_conditional_edges("node_routing_previewer", route_after_routing)

# Routes to save_to_rag if approved, or loops back to placement_specialist if rejected/edited
builder.add_conditional_edges("node_human_viewer", route_after_human)

# 4. Final Step
builder.add_edge("node_save_to_rag", END)
# 5. Compile Graph
app = builder.compile(checkpointer=memory)
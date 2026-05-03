"""
Human Viewer Node
=================
A LangGraph node that interrupts execution to allow for human visual review 
and approval of the generated layout.

Functions:
- node_human_viewer: Presents the layout to the user and handles approval or edit requests.
  - Inputs: state (dict)
  - Outputs: state update with approval status and chat history.
"""

import json
from langgraph.types import interrupt
from ai_agent.nodes._shared import _update_and_save_chat_history


def node_human_viewer(state):
    interrupt({
        "type": "visual_review",
        "pending_cmds": state.get("pending_cmds", []),
        "last_agent": state.get("last_agent", {}),
        "Analysis": state.get("Analysis_result", ""),
        "Strategy": state.get("strategy_result", ""),
        "Placement": state.get("placement_text", ""),
        "Routing": state.get("routing_result", {}),
    })
    return {
        "approved": False,
        "chat_history": state.get("chat_history", []),
    }

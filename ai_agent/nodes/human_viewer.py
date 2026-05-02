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
        "placement": state.get("pending_cmds", []),
        "routing": state.get("routing_result", {}),
    })
    return {
        "approved": False,
        "pending_cmds": [],
        "chat_history": state.get("chat_history", []),
    }

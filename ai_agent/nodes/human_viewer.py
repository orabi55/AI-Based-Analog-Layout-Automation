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
    ui_response = interrupt({
        "type": "visual_review",
        "placement": state.get("pending_cmds", []),
        "routing": state.get("routing_result", {}),
    })

    chat_history = state.get("chat_history", [])
    if isinstance(ui_response, dict):
        if ui_response.get("approved"):
            user_content = "User approved the layout in visual review."
        else:
            edits = ui_response.get("edits", [])
            user_content = f"User requested visual review edits: {json.dumps(edits)}"
    else:
        user_content = str(ui_response)

    updated_chat_history = _update_and_save_chat_history(
        chat_history=chat_history, user_content=user_content,
    )

    if isinstance(ui_response, dict) and ui_response.get("approved"):
        return {"approved": True, "chat_history": updated_chat_history}
    else:
        return {
            "approved": False,
            "pending_cmds": ui_response.get("edits", []) if isinstance(ui_response, dict) else [],
            "chat_history": updated_chat_history,
        }

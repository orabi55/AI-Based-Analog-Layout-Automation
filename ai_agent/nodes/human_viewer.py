"""
Human Viewer Node
=================
A LangGraph node that interrupts execution to allow for human visual review
and approval of the generated layout.

Functions:
- node_human_viewer: Presents the layout to the user and handles approval or edit requests.
  - Inputs: state (dict)
  - Outputs: state update with approval status, optional user_feedback, and chat history.
"""

import json
from langgraph.types import interrupt


def node_human_viewer(state):
    """Interrupt for human visual review and return the user's decision.

    The ``interrupt()`` call pauses the graph and emits a payload to the
    GUI.  When the user resumes the graph (via ``Command(resume=…)``),
    the resume value is returned here so we can read approval status,
    optional feedback text, and any modified commands.

    Supported resume shapes:
    * ``{"approved": True/False}``
    * ``{"approved": True, "user_feedback": "…"}``
    * ``{"approved": True, "modified_cmds": [...]}``
    * ``True`` / ``False``  (shorthand)
    """
    viewer_response = interrupt({
        "type": "visual_review",
        "pending_cmds": state.get("pending_cmds", []),
        "last_agent": state.get("last_agent", {}),
        "Analysis": state.get("Analysis_result", ""),
        "Strategy": state.get("strategy_result", ""),
        "Placement": state.get("placement_text", ""),
        "Routing": state.get("routing_result", {}),
    })

    # -- Parse the resume value ------------------------------------------
    approved = False
    user_feedback = None
    modified_cmds = None

    if isinstance(viewer_response, dict):
        approved = bool(viewer_response.get("approved", False))
        user_feedback = viewer_response.get("user_feedback")
        modified_cmds = viewer_response.get("modified_cmds")
    elif isinstance(viewer_response, bool):
        approved = viewer_response

    # -- Build state update ----------------------------------------------
    update = {
        "approved": approved,
        "chat_history": state.get("chat_history", []),
    }

    if user_feedback is not None:
        update["user_feedback"] = str(user_feedback)

    if modified_cmds is not None and isinstance(modified_cmds, list):
        update["pending_cmds"] = modified_cmds

    return update

"""Graph Routing Logic
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
- route_after_session_chat: Routes based on the normalised session_route set by
  node_session_chat.  Maps each route to the appropriate specialist or finalizer.
  - Inputs: state (LayoutState)
  - Outputs: name of the next node.
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


# ---------------------------------------------------------------------------
# Session chat routing
# ---------------------------------------------------------------------------

#: Maps each session_route value to the downstream node name.
_SESSION_ROUTE_MAP: dict[str, str] = {
    "answer_only":    "node_session_finalizer",
    "clarify":        "node_session_finalizer",
    "command_edit":   "node_command_validator",
    "need_topology":  "node_topology_analyst",
    "need_strategy":  "node_strategy_selector",
    "need_placement": "node_placement_specialist",
    "need_drc":       "node_drc_checker",
    "fix_drc":        "node_drc_critic",
    "need_routing":   "node_routing_previewer",
    "fix_routing":    "node_routing_previewer",
}

#: Safe default when the route is unknown or missing.
_SESSION_FALLBACK_NODE = "node_session_finalizer"


def route_after_session_chat(state: LayoutState) -> str:
    """Deterministic edge after node_session_chat.

    Routes **only** on the normalised ``session_route`` that was set by the
    session chat node.  No user-message inspection, no LLM calls.

    Unknown or ``None`` routes are sent to ``node_session_finalizer`` so the
    user receives a safe reply rather than hitting a dead-end.
    """
    route = state.get("session_route")
    target = _SESSION_ROUTE_MAP.get(route, _SESSION_FALLBACK_NODE)
    return target


# ---------------------------------------------------------------------------
# Post-command-validator routing
# ---------------------------------------------------------------------------

def route_after_command_validator(state: LayoutState) -> str:
    """Conditional edge after node_command_validator.

    Routes to ``node_human_viewer`` only when there are valid pending
    commands to review.  Otherwise routes to ``node_session_finalizer``
    so the user receives feedback without an unnecessary visual-review
    interrupt.

    Validation *warnings* (e.g. symmetry concerns) do **not** block
    human review — only an empty ``pending_cmds`` list or an explicit
    ``clarify`` route prevents the viewer from being invoked.
    """
    pending_cmds = state.get("pending_cmds") or []
    session_route = state.get("session_route")

    if session_route == "clarify":
        return "node_session_finalizer"

    if not pending_cmds:
        return "node_session_finalizer"

    # Valid commands exist → allow visual review (even with warnings).
    return "node_human_viewer"


# ---------------------------------------------------------------------------
# Post-DRC-critic routing (for fix_drc route)
# ---------------------------------------------------------------------------

def route_after_session_drc(state: LayoutState) -> str:
    """Conditional edge after node_drc_critic in the session graph.

    When the DRC critic produces fix commands (``pending_cmds``),
    route through the command validator and then to human viewer.
    Otherwise, go straight to the session finalizer for a summary.
    """
    route = state.get("session_route")
    layout_decision = state.get("layout_session_decision")
    pending_cmds = state.get("pending_cmds") or []

    if (route == "fix_drc" or layout_decision == "fix_drc") and pending_cmds:
        return "node_command_validator"

    return "node_session_finalizer"


# ---------------------------------------------------------------------------
# Layout session agent routing (chat_v2)
# ---------------------------------------------------------------------------

def route_after_layout_session_agent(state: LayoutState) -> str:
    """Conditional edge after node_layout_session_agent.

    Maps the ``layout_session_decision`` to the next downstream node.
    For ``call_specialist``, a secondary lookup on
    ``layout_session_specialist`` determines the specific specialist node.

    Unknown decisions are sent to ``node_session_finalizer`` as a
    safe fallback.
    """
    decision = state.get("layout_session_decision")

    if decision in {"answer", "clarify"}:
        return "node_session_finalizer"

    if decision == "call_deterministic_tool":
        return "node_deterministic_tool_runner"

    if decision == "propose_commands":
        return "node_command_validator"

    if decision == "check_drc":
        return "node_drc_checker"

    if decision == "fix_drc":
        return "node_drc_critic"

    if decision == "check_routing":
        return "node_routing_previewer"

    if decision == "optimize_routing":
        return "node_routing_previewer"

    if decision == "call_specialist":
        specialist = state.get("layout_session_specialist")
        if specialist == "topology_analyst":
            return "node_topology_analyst"
        if specialist == "strategy_selector":
            return "node_strategy_selector"
        if specialist == "placement_specialist":
            return "node_placement_specialist"
        if specialist == "drc_critic":
            return "node_drc_critic"
        if specialist == "routing_previewer":
            return "node_routing_previewer"

    # Unknown decision — safe fallback
    return "node_session_finalizer"


def route_after_deterministic_tool_runner(state: LayoutState) -> str:
    """Conditional edge after node_deterministic_tool_runner.

    If the tool runner produced commands (decision is ``propose_commands``
    and ``pending_cmds`` is non-empty), route to command validation.
    Otherwise, go to the session finalizer for feedback.
    """
    decision = state.get("layout_session_decision")

    if decision == "propose_commands" and state.get("pending_cmds"):
        return "node_command_validator"

    return "node_session_finalizer"


"""
Graph State Definition
======================
Defines the shared state structure used by all nodes in the LangGraph pipeline.

Functions:
- None (Defines LayoutState TypedDict)
"""

from typing import TypedDict, List, Dict, Any, Literal, Optional

try:
    from typing import NotRequired          # Python 3.11+
except ImportError:
    from typing_extensions import NotRequired  # Python 3.10 and below


class LayoutState(TypedDict):
    """Shared state passed between all LangGraph nodes.

    Execution modes:
      - "initial":     Full pipeline, auto-run, no interrupts (Ctrl+P placement)
      - "chat":        Selective nodes, human-in-loop enabled (session chatbot)
      - "legacy_chat": Old chatbot graph kept for backward compatibility
    """
    # --- Execution mode ---
    mode: Literal["initial", "chat", "legacy_chat"]

    # --- Inputs ---
    user_message: str
    chat_history: List[Dict[str, str]]
    nodes: List[Dict[str, Any]]
    sp_file_path: str
    selected_model: str

    # --- Topology ---
    constraint_text: str
    edges: List[Dict]
    terminal_nets: Dict[str, Dict[str, Any]]

    # --- Strategy ---
    Analysis_result: str
    strategy_result: str

    # --- Placement ---
    placement_nodes: List[Dict]
    deterministic_snapshot: List[Dict]
    original_placement_cmds: List[Dict]

    # --- DRC ---
    drc_flags: List[Dict]
    drc_pass: bool
    drc_retry_count: int
    gap_px: float

    # --- Routing ---
    routing_pass_count: int
    routing_result: Dict[str, Any]

    # --- Pending updates ---
    pending_cmds: List[Dict]

    # --- Chat router metadata ---
    intent: str
    router_target: str
    last_agent: str

    # --- Human Approval ---
    approved: bool

    # --- Pipeline config (from UI) ---
    no_abutment: bool
    abutment_candidates: List[Dict]

    # --- Symmetry enforcement ---
    placement_mode: str   # "auto" | "two_half"

    # --- Quality benchmark ---
    placement_quality: Dict[str, Any]  # output of score_placement()

    # --- Placement goals (user-specified priorities) ---
    placement_goals: Dict[str, Any]    # area/matching/symmetry + max_area_um2

    # --- Agent output cache ---
    placement_text: str

    # ── Session chatbot state fields (all NotRequired) ─────────────────────────
    # These keys may be absent from legacy state dicts that predate the session
    # chatbot.  Using NotRequired ensures type checkers do not flag their
    # absence as an error.

    # Compact record of what initial-placement agents decided (topology,
    # strategy, placement list, routing, DRC pass/flags).
    initial_agent_trace: NotRequired[Optional[Dict[str, Any]]]

    # Final text to display in the chat UI after any node finishes.
    assistant_text: NotRequired[Optional[str]]

    # Strict route chosen by the session chatbot (see VALID_SESSION_ROUTES).
    session_route: NotRequired[Optional[str]]

    # Confidence score for the chosen route, in the range [0.0, 1.0].
    route_confidence: NotRequired[Optional[float]]

    # True when the session chatbot decides to delegate to a specialist agent.
    requires_specialist: NotRequired[bool]

    # Which specialist to call: one of the five agent names or None.
    # Valid values: "topology_analyst", "strategy_selector",
    #               "placement_specialist", "drc_critic", "drc_checker",
    #               "routing_previewer".
    specialist_target: NotRequired[Optional[str]]

    # Short human-readable reason explaining the routing/specialist decision.
    session_reason: NotRequired[Optional[str]]

    # Raw commands produced by the session chatbot, before validation.
    session_commands: NotRequired[Optional[List[Dict[str, Any]]]]

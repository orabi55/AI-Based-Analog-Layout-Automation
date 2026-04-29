"""
Graph State Definition
======================
Defines the shared state structure used by all nodes in the LangGraph pipeline.

Functions:
- None (Defines LayoutState TypedDict)
"""

from typing import TypedDict, List, Dict, Any, Literal


class LayoutState(TypedDict):
    """Shared state passed between all LangGraph nodes.

    Execution modes:
      - "initial": Full pipeline, auto-run, no interrupts (Ctrl+P placement)
      - "chat":    Selective nodes, human-in-loop enabled (chatbot mode)
    """
    # --- Execution mode ---
    mode: Literal["initial", "chat"]

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

    # --- Human Approval ---
    approved: bool

    # --- Pipeline config (from UI) ---
    no_abutment: bool
    abutment_candidates: List[Dict]

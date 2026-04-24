from typing import TypedDict, List, Dict, Any

class LayoutState(TypedDict):
    # Inputs
    user_message: str
    chat_history: List[Dict[str, str]]
    nodes: List[Dict[str, Any]]
    sp_file_path: str
    selected_model: str
    
    # Topology
    constraint_text: str  
    edges: List[Dict]
    terminal_nets: Dict[str, Dict[str, Any]]
    abutment_candidates: List[Dict]    # abutment pairs extracted by topology analyst

    # Strategy
    Analysis_result: str
    strategy_result: str
    multirow_layout: Dict[str, Any]    # {nmos_rows, pmos_rows} produced by strategy LLM call

    # Placement
    placement_nodes: List[Dict]
    deterministic_snapshot: List[Dict]

    # DRC
    drc_flags: List[Dict]
    drc_pass: bool
    drc_retry_count: int
    gap_px: float
    
    # Routing
    routing_pass_count: int
    routing_result: Dict[str, Any]
    
    # Pending updates (Fixed: No longer an accumulator)
    pending_cmds: List[Dict]
    
    # Human Approval
    approved: bool

    # SA Optimizer — opt-in via run_sa=True in initial state
    run_sa: bool

    # Matched blocks — protected from individual device moves by DRC/SA
    matched_blocks: List[Dict]
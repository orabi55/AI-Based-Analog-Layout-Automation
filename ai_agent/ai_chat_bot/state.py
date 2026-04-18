from typing import TypedDict, List, Dict, Any

class LayoutState(TypedDict):
    # Inputs
    user_message: str
    chat_history: List[Dict[str, str]]
    nodes: List[Dict[str, Any]]
    sp_file_path: str
    
    # Topology
    constraints: List[str]
    constraint_text: str  
    edges: List[Dict]
    terminal_nets: Dict[str, Dict[str, Any]]
    
    # Strategy
    strategy_question: str
    selected_strategy: str 
    
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
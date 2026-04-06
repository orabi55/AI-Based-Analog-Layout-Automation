"""
ai_agent/orchestrator.py
=========================
Multi-Agent Orchestrator for Analog Layout Automation with Multi-Finger Support.

Sequential pipeline:
  Stage 0  → Intent Classification (concrete/abstract/question)
  Stage 1  → Topology Analyst (constraint extraction with multi-finger detection)
  Stage 2  → Placement Specialist (generates commands for physical fingers)
  Stage 2.5 → Deterministic Optimizer (physics-based refinement)
  Stage 3  → DRC Critic (iterative violation correction)
  Stage 4  → Routing Pre-Viewer (net crossing optimization)

FIXES APPLIED:
  - Bug #1:  State fully reset at start of run_topology_analysis()
  - Bug #1b: sp_file_path overridable per-call via layout_context["sp_file_path"]
  - Bug #5:  Y-coordinate guard validates row membership before accepting
  - Bug #7:  Empty constraint_text logs a clear diagnostic
  - Bug #8:  Conservation failure logs missing device IDs explicitly

The Orchestrator is pure Python (no Qt). Driven by OrchestratorWorker
(in llm_worker.py) which runs it on a background QThread.
"""

import json
import re
import copy
from typing import List, Dict, Tuple, Optional, Callable

# ───────────────────────────────────────────────────────────────────────────
# Imports - All at module level
# ───────────────────────────────────────────────────────────────────────────
from ai_agent.topology_analyst import (
    TOPOLOGY_ANALYST_PROMPT,
    analyze_topology,
)
from ai_agent.placement_specialist import (
    PLACEMENT_SPECIALIST_PROMPT,
    build_placement_context,
)
from ai_agent.drc_critic import (
    DRC_CRITIC_PROMPT,
    run_drc_check,
    format_drc_violations_for_llm,
    compute_prescriptive_fixes,
)
from ai_agent.routing_previewer import (
    ROUTING_PREVIEWER_PROMPT,
    score_routing,
    format_routing_for_llm,
)
from ai_agent.strategy_selector import generate_strategies, parse_placement_mode
from ai_agent.classifier_agent import classify_intent
from ai_agent.rag_retriever import build_rag_context, save_run_as_example
from ai_agent.pipeline_optimizer import apply_deterministic_optimizations
from ai_agent.tools import (
    tool_validate_device_count,
    tool_resolve_overlaps,
)
from ai_agent.finger_grouping import (
    aggregate_to_logical_devices,
    expand_logical_to_fingers,
    validate_finger_integrity,
    group_fingers,
)


# ───────────────────────────────────────────────────────────────────────────
# Constants
# ───────────────────────────────────────────────────────────────────────────
DEFAULT_MIN_DEVICE_SPACING_UM: float = 0.294
DEFAULT_GAP_PX:                float = 0.0
MAX_DRC_RETRIES:                int   = 2
MAX_ROUTING_HILL_CLIMB_PASSES:  int   = 3

# GUI canvas pixel-to-µm conversion factor.
# Typical symbolic-editor canvas: 1 µm = approximately 34 pixels (tunable).
# This is used to convert gap_px (pixels) → gap_um (µm) for the DRC checker.
# If gap_px == 0 (default) the conversion produces 0.0, meaning no gap enforcement.
PIXELS_PER_UM: float = 34.0

# Y-coordinate row convention:
#   NMOS: y >= NMOS_ROW_Y_MIN
#   PMOS: y <  NMOS_ROW_Y_MIN
NMOS_ROW_Y_MIN: float = 0.0


# ───────────────────────────────────────────────────────────────────────────
# Logging Helper
# ───────────────────────────────────────────────────────────────────────────
def _log(msg: str):
    """Unified logging prefix for orchestrator."""
    print(f"[ORCH] {msg}")


# ───────────────────────────────────────────────────────────────────────────
# Command Block Parsing
# ───────────────────────────────────────────────────────────────────────────
def _extract_cmd_blocks(text: str) -> List[dict]:
    """
    Extract and parse [CMD]...[/CMD] blocks from LLM response.

    Handles:
      - Markdown code fences
      - Unicode lookalike brackets
      - Extra whitespace / lowercase in tags
      - Malformed JSON (attempts light repair)

    Returns:
        List of parsed command dicts
    """
    if not text:
        return []

    # Strip markdown fences
    text = re.sub(r'```[a-zA-Z]*\n?', '', text)
    text = re.sub(r'```', '', text)

    # Normalize unicode lookalike brackets
    text = text.replace('\uff3b', '[').replace('\uff3d', ']')
    text = text.replace('\u27e6', '[').replace('\u27e7', ']')

    # Normalize tag whitespace and case
    text = re.sub(
        r'\[\s*/?\s*[Cc][Mm][Dd]\s*\]',
        lambda m: '[/CMD]' if '/' in m.group() else '[CMD]',
        text,
    )

    cmds: List[dict] = []
    pattern = re.compile(r'\[CMD\](.*?)\[/CMD\]', re.DOTALL | re.IGNORECASE)

    for match in pattern.finditer(text):
        raw = match.group(1).strip()
        raw = re.sub(r'```[a-zA-Z]*', '', raw).strip()

        if not raw:
            _log("Warning: empty CMD block skipped")
            continue

        try:
            cmds.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            repaired = raw
            repaired = re.sub(r',\s*}', '}', repaired)
            repaired = re.sub(r',\s*\]', ']', repaired)
            repaired = repaired.replace("'", '"')

            try:
                cmds.append(json.loads(repaired))
                _log(f"Warning: CMD block auto-repaired: {raw[:80]!r}")
            except json.JSONDecodeError:
                _log(
                    f"Warning: skipping malformed CMD block: "
                    f"{raw[:80]!r} (error: {exc})"
                )

    # Diagnostic: found markers but nothing parsed
    if not cmds:
        raw_markers = re.findall(r'(?i)\[/?cmd\]|［/?CMD］', text)
        if raw_markers:
            _log(f"⚠ Found {len(raw_markers)} CMD markers but parsed 0 blocks")

    return cmds


def _cmds_to_text(cmds: List[dict]) -> str:
    """Serialize command dicts back to [CMD] block strings."""
    return "\n".join(f"[CMD]{json.dumps(c)}[/CMD]" for c in cmds)


# ───────────────────────────────────────────────────────────────────────────
# Command Key Helper (for deduplication)
# ───────────────────────────────────────────────────────────────────────────
def _cmd_key(cmd: dict) -> str:
    """Generate stable lookup key for command deduplication.
    
    Handles move, swap, flip, delete commands.
    Returns device ID or 'unknown' for malformed commands.
    """
    return (
        cmd.get("device")
        or cmd.get("device_id")
        or cmd.get("id")
        or cmd.get("device_a")  # swap primary key
        or cmd.get("a")
        or "unknown"
    )


# ───────────────────────────────────────────────────────────────────────────
# Row Validation Helper (Bug #5 support)
# ───────────────────────────────────────────────────────────────────────────
def _device_is_nmos(node: dict) -> bool:
    """Return True if node is NMOS (or unknown type assumed NMOS)."""
    dev_type = str(node.get("type", "")).lower()
    if dev_type.startswith("p"):
        return False
    return True  # nmos or unknown


def _y_in_correct_row(proposed_y: float, node: dict) -> bool:
    """
    Validate proposed Y against device type row convention.

    Convention (configurable via NMOS_ROW_Y_MIN):
      NMOS: y >= NMOS_ROW_Y_MIN
      PMOS: y <  NMOS_ROW_Y_MIN

    Returns:
        True if proposed_y respects the device's row membership
    """
    if _device_is_nmos(node):
        return proposed_y >= NMOS_ROW_Y_MIN
    else:
        return proposed_y < NMOS_ROW_Y_MIN


# ───────────────────────────────────────────────────────────────────────────
# Command Application
# ───────────────────────────────────────────────────────────────────────────
def _apply_cmds_to_nodes(nodes: List[dict], cmds: List[dict]) -> List[dict]:
    """Apply swap/move/flip/delete commands to a copy of nodes.
    
    Args:
        nodes: Original node list
        cmds: List of command dicts
    
    Returns:
        Updated node list (deep copy)
    """
    nodes = copy.deepcopy(nodes)
    id_map = {n['id']: n for n in nodes}

    for cmd in cmds:
        action = cmd.get('action', '').lower()
        
        # ─── SWAP ───
        if action in ('swap', 'swap_devices'):
            a_id = cmd.get('device_a', cmd.get('a'))
            b_id = cmd.get('device_b', cmd.get('b'))
            
            if a_id in id_map and b_id in id_map:
                ga = id_map[a_id]['geometry']
                gb = id_map[b_id]['geometry']
                
                # Swap positions
                ga['x'], gb['x'] = gb['x'], ga['x']
                ga['y'], gb['y'] = gb['y'], ga['y']
                
                # Swap orientations
                ga['orientation'], gb['orientation'] = (
                    gb.get('orientation', 'R0'),
                    ga.get('orientation', 'R0')
                )
        
        # ─── MOVE ───
        elif action in ('move', 'move_device'):
            dev_id = cmd.get('device', cmd.get('device_id', cmd.get('id')))
            
            if dev_id in id_map:
                node = id_map[dev_id]
                
                # X: always accept (LLMs are reliable for X placement)
                if cmd.get('x') is not None:
                    node['geometry']['x'] = float(cmd['x'])
                
                # Y: validate row membership before accepting (Bug #5 fix)
                if cmd.get('y') is not None:
                    proposed_y = float(cmd['y'])
                    force_y    = bool(cmd.get('force_y', False))
                    
                    if force_y:
                        # Explicit override — trust caller
                        node['geometry']['y'] = proposed_y
                        _log(
                            f"  MOVE {dev_id}: y={proposed_y} "
                            f"(forced by force_y=True)"
                        )
                    elif _y_in_correct_row(proposed_y, node):
                        node['geometry']['y'] = proposed_y
                    else:
                        current_y = float(node['geometry'].get('y', 0))
                        dev_type  = node.get('type', '?')
                        _log(
                            f"  ⚠ MOVE {dev_id}: rejected y={proposed_y} "
                            f"(type={dev_type}, current_y={current_y}, "
                            f"would cross row boundary)"
                        )
            else:
                _log(f"  MOVE: device not found: {dev_id!r}")
        
        # ─── FLIP ───
        elif action in ('flip', 'flip_h', 'flip_v'):
            dev_id = cmd.get('device', cmd.get('id'))
            
            if dev_id in id_map:
                cur = id_map[dev_id]['geometry'].get('orientation', 'R0')
                flip_map = {
                    'R0': 'R0_FH',
                    'R0_FH': 'R0',
                    'R0_FV': 'R0_FH_FV',
                    'R0_FH_FV': 'R0_FV'
                }
                id_map[dev_id]['geometry']['orientation'] = flip_map.get(cur, cur)
        
        # ─── DELETE ───
        elif action == 'delete':
            dev_id = cmd.get('device', cmd.get('id'))
            nodes = [n for n in nodes if n['id'] != dev_id]
            id_map = {n['id']: n for n in nodes}
    
    # Ensure no overlaps after command application
    _deduplicate_positions(nodes)
    
    return nodes


def _deduplicate_positions(
    nodes: List[dict],
    min_spacing: float = DEFAULT_MIN_DEVICE_SPACING_UM
):
    """Ensure no two devices in same row share x-slot (in-place).
    
    Args:
        nodes: Node list to deduplicate
        min_spacing: Minimum X spacing in µm
    """
    rows = {}
    for n in nodes:
        ry = round(float(n['geometry']['y']), 4)
        rows.setdefault(ry, []).append(n)
    
    for row_nodes in rows.values():
        row_nodes.sort(key=lambda n: float(n['geometry']['x']))
        
        for i in range(1, len(row_nodes)):
            prev = row_nodes[i - 1]
            curr = row_nodes[i]
            
            prev_end = float(prev['geometry']['x']) + float(prev['geometry']['width'])
            curr_x = float(curr['geometry']['x'])
            
            # If overlap detected, snap current device to grid after prev device
            if curr_x < prev_end - 0.001:
                snapped = round(prev_end / min_spacing) * min_spacing
                if snapped < prev_end - 0.001:
                    snapped += min_spacing
                curr['geometry']['x'] = snapped


# ───────────────────────────────────────────────────────────────────────────
# Routing Optimization Helpers
# ───────────────────────────────────────────────────────────────────────────
def _generate_targeted_swaps(
    nodes: List[dict],
    worst_nets: List[str],
    terminal_nets: Dict[str, dict]
) -> List[dict]:
    """Generate swap commands targeting highest-cost nets.
    
    For each worst net:
      - Find leftmost and rightmost devices on that net
      - Propose swapping each with its immediate neighbor (if not on same net)
      - Goal: shorten wire span
    
    Args:
        nodes: Current node list
        worst_nets: List of net names with highest routing cost
        terminal_nets: Device terminal connections
    
    Returns:
        List of swap command dicts
    """
    if not worst_nets or not nodes:
        return []

    # Build net → devices mapping
    net_to_devices = {}
    for node in nodes:
        dev_id = node['id']
        nets = terminal_nets.get(dev_id, {})
        for net in nets.values():
            net_to_devices.setdefault(net, []).append(node)

    # Sort nodes by row and X for neighbor lookup
    sorted_nodes = sorted(
        nodes,
        key=lambda n: (
            round(float(n['geometry']['y']), 2),
            float(n['geometry']['x'])
        )
    )
    index_map = {n['id']: i for i, n in enumerate(sorted_nodes)}

    swap_cmds = []
    seen_pairs = set()

    for net in worst_nets:
        net_devices = net_to_devices.get(net, [])
        if len(net_devices) < 2:
            continue

        # Sort by X position
        net_devices_sorted = sorted(
            net_devices,
            key=lambda n: float(n['geometry']['x'])
        )
        
        left_node = net_devices_sorted[0]
        right_node = net_devices_sorted[-1]
        net_ids = {d['id'] for d in net_devices}

        left_idx = index_map.get(left_node['id'], -1)
        right_idx = index_map.get(right_node['id'], -1)

        # Try swapping left device with right neighbor
        if 0 <= left_idx < len(sorted_nodes) - 1:
            neighbor = sorted_nodes[left_idx + 1]
            n_id = neighbor['id']
            pair = tuple(sorted([left_node['id'], n_id]))
            
            if n_id not in net_ids and pair not in seen_pairs:
                swap_cmds.append({
                    'action': 'swap',
                    'device_a': left_node['id'],
                    'device_b': n_id,
                })
                seen_pairs.add(pair)

        # Try swapping right device with left neighbor
        if right_idx > 0:
            neighbor = sorted_nodes[right_idx - 1]
            n_id = neighbor['id']
            pair = tuple(sorted([right_node['id'], n_id]))
            
            if n_id not in net_ids and pair not in seen_pairs:
                swap_cmds.append({
                    'action': 'swap',
                    'device_a': right_node['id'],
                    'device_b': n_id,
                })
                seen_pairs.add(pair)

    return swap_cmds


# ───────────────────────────────────────────────────────────────────────────
# Multi-Finger Validation
# ───────────────────────────────────────────────────────────────────────────
def _validate_multi_finger_placement(
    physical_nodes: List[dict]
) -> dict:
    """Validate multi-finger device placement correctness.
    
    Checks:
      - All fingers of one device are consecutive
      - All fingers have same orientation
      - All fingers in same row
    
    Returns:
        dict: {pass, violations, summary}
    """
    from ai_agent.finger_grouping import extract_base_and_finger
    
    finger_groups = group_fingers(physical_nodes)
    violations = []

    for base_name, finger_list in finger_groups.items():
        if len(finger_list) <= 1:
            continue  # Single finger or non-finger device

        # Sort by finger number
        finger_list.sort(key=lambda n: extract_base_and_finger(n["id"])[1])

        # Check 1: Consecutive x-positions
        x_positions = [float(f["geometry"]["x"]) for f in finger_list]
        for i in range(len(x_positions) - 1):
            gap = x_positions[i + 1] - (x_positions[i] + 0.294)
            if gap > 0.01:  # 10nm tolerance
                violations.append(
                    f"❌ {base_name}: fingers not consecutive - "
                    f"gap of {gap:.3f}µm between F{i+1} and F{i+2}"
                )

        # Check 2: Same orientation
        orientations = [f["geometry"].get("orientation", "R0") for f in finger_list]
        if len(set(orientations)) > 1:
            violations.append(
                f"❌ {base_name}: mixed orientations {set(orientations)}"
            )

        # Check 3: Same row
        y_positions = [float(f["geometry"]["y"]) for f in finger_list]
        if len(set(round(y, 2) for y in y_positions)) > 1:
            violations.append(
                f"❌ {base_name}: fingers in different rows {set(y_positions)}"
            )

    passed = len(violations) == 0
    summary = (
        f"✅ All multi-finger devices correctly placed"
        if passed
        else f"❌ {len(violations)} multi-finger placement violation(s)"
    )

    return {
        "pass": passed,
        "violations": violations,
        "summary": summary,
    }


# ───────────────────────────────────────────────────────────────────────────
# Orchestrator Class
# ───────────────────────────────────────────────────────────────────────────
class Orchestrator:
    """Multi-agent orchestrator with multi-finger transistor support.
    
    Manages 4-stage pipeline:
      1. Topology Analysis (with multi-finger detection)
      2. Placement Specialist (generates finger-level commands)
      3. DRC Critic (iterative correction)
      4. Routing Pre-Viewer (crossing optimization)
    
    Args:
        run_llm_fn: Callable that takes (messages, full_prompt) and returns LLM response
        sp_file_path: Path to SPICE netlist (optional)
        gap_px: Minimum device gap for DRC (pixels)
        max_drc_retries: Maximum DRC correction attempts
        min_spacing: Minimum device X-spacing (µm)
        stage_callback: Optional callback(stage_index, stage_name, data)
    """

    STAGE_NAMES = [
        "Topology Analyst",
        "Placement Specialist",
        "DRC Critic",
        "Routing Pre-Viewer",
    ]

    def __init__(
        self,
        run_llm_fn: Callable,
        sp_file_path: Optional[str] = None,
        gap_px: float = DEFAULT_GAP_PX,
        max_drc_retries: int = MAX_DRC_RETRIES,
        min_spacing: float = DEFAULT_MIN_DEVICE_SPACING_UM,
        stage_callback: Optional[Callable] = None,
    ):
        self._llm = run_llm_fn
        self._sp_file = sp_file_path
        self._gap_px = gap_px
        self._max_drc_retries = max_drc_retries
        self._min_spacing = min_spacing
        self._stage_cb = stage_callback

        # Internal state
        self.original_physical_nodes = []
        self.working_nodes = []
        self.constraint_text = ""

    # ───────────────────────────────────────────────────────────────────────
    # Stage Callback Helper
    # ───────────────────────────────────────────────────────────────────────
    def _emit_stage(self, index: int, data: dict):
        """Safely call stage callback if provided."""
        if self._stage_cb is not None:
            try:
                self._stage_cb(index, self.STAGE_NAMES[index], data)
            except Exception as exc:
                _log(f"stage_callback error (ignored): {exc}")

    # ───────────────────────────────────────────────────────────────────────
    # Stage 0: Intent Classification
    # ───────────────────────────────────────────────────────────────────────
    def _classify_intent(self, user_message: str) -> str:
        """Classify user intent as concrete/abstract/question.
        
        Returns:
            'concrete' | 'abstract' | 'question'
        """
        try:
            return classify_intent(user_message, self._llm)
        except Exception as exc:
            _log(f"Intent classification failed: {exc} - defaulting to abstract")
            return "abstract"

    # ───────────────────────────────────────────────────────────────────────
    # Stage 1: Topology Analysis
    # ───────────────────────────────────────────────────────────────────────
    def run_topology_analysis(
        self,
        user_message: str,
        layout_context: dict
    ) -> Tuple[str, str]:
        """Stage 1: Extract topology constraints and ask user for confirmation.
        
        Args:
            user_message: User's request text
            layout_context: {nodes, edges, terminal_nets, gap_px (optional)}
        
        Returns:
            (question_for_user, constraint_text)
        """
        nodes = layout_context.get("nodes", [])
        edges = layout_context.get("edges", [])
        terminal_nets = layout_context.get("terminal_nets", {})

        _log(f"Stage 1 starting: {user_message[:80]!r}")

        # Bug #1: Stale state fully reset at start
        self.original_physical_nodes = []
        self.working_nodes = []
        self.constraint_text = ""

        # Store original physical nodes
        self.original_physical_nodes = copy.deepcopy(nodes)

        # Aggregate fingers to logical devices
        logical_nodes = aggregate_to_logical_devices(nodes)
        _log(f"Aggregated {len(nodes)} physical → {len(logical_nodes)} logical devices")

        # Bug #1b: sp_file_path overridable per-call via layout_context
        sp_file_path = layout_context.get("sp_file_path", self._sp_file)

        # Extract topology using logical devices
        self.constraint_text = analyze_topology(
            logical_nodes,
            terminal_nets,
            sp_file_path
        )
        _log(f"Topology constraints: {len(self.constraint_text)} chars")
        
        # M2 fix: Surface empty constraint_text as an explicit user-visible warning.
        # An empty constraint means Stage 2 will get no guidance and produce
        # generic placement. Log clearly and embed the warning in the question.
        _constraint_warning = ""
        if not self.constraint_text.strip():
            sp_status = (
                f"SPICE file not found: {sp_file_path!r}"
                if sp_file_path
                else "No SPICE file provided"
            )
            _constraint_warning = (
                f"\n\n⚠️ **WARNING**: No topology constraints could be extracted.\n"
                f"({sp_status}, and no terminal_nets from layout canvas).\n"
                f"Please load a SPICE netlist (.sp/.cir) before running AI placement "
                f"to get topology-aware results."
            )
            _log(
                f"⚠ constraint_text is EMPTY — "
                f"sp_file_path={sp_file_path!r}, "
                f"terminal_nets={len(terminal_nets)} entries, "
                f"nodes={len(nodes)}"
            )

        # Try to get LLM to present constraints conversationally
        analyst_user = (
            f"User request: {user_message}\n\n"
            f"Device inventory ({len(logical_nodes)} logical devices):\n"
            + "\n".join(
                f"  {n['id']} ({n.get('type', '?')}) "
                f"nf={n.get('electrical', {}).get('nf', 1)}"
                for n in logical_nodes[:30] if not n.get('is_dummy')
            )
            + (f"\n  ... ({len(logical_nodes) - 30} more)" if len(logical_nodes) > 30 else "")
            + f"\n\nPre-extracted constraints:\n{self.constraint_text}"
        )

        analyst_msgs = [
            {"role": "system", "content": TOPOLOGY_ANALYST_PROMPT},
            {"role": "user", "content": analyst_user},
        ]

        try:
            analyst_response = self._llm(analyst_msgs, analyst_user)
            question = analyst_response.strip()

            # If LLM returned JSON, extract and use default question
            if question.startswith("{") and question.endswith("}"):
                question = None
        except Exception as exc:
            _log(f"Stage 1 LLM failed (using fallback): {exc}")
            question = None

        # Fallback question
        if not question:
            question = (
                f"🔬 **Topology Analysis Complete**\n\n"
                f"I identified the following structures:\n\n"
                f"{self.constraint_text}\n\n"
                f"**Is this correct?** Reply 'Yes' to proceed, or let me know any corrections."
            )

        # Append constraint warning if no topology data was found (M2)
        if _constraint_warning:
            question += _constraint_warning

        # Stage 1.5: Strategy Selection
        try:
            strategy_text = generate_strategies(
                user_message,
                self.constraint_text,
                self._llm
            )
        except Exception as exc:
            _log(f"Strategy selector failed (using fallback): {exc}")
            strategy_text = (
                "Recommended strategies:\n\n"
                "1. **Enhance Symmetry** — Place matched pairs equidistant from row center\n"
                "2. **Improve Matching** — Abut mirror devices with same orientation\n"
                "3. **Minimize DRC Violations** — Resolve overlaps first\n\n"
                "Type a number (1-3), 'all', or describe a custom approach."
            )

        combined_question = question + "\n\n---\n\n" + strategy_text

        self._emit_stage(0, {
            "constraint_text": self.constraint_text,
            "working_nodes": logical_nodes,
        })

        return combined_question, self.constraint_text

    # ───────────────────────────────────────────────────────────────────────
    # Stage 2: Placement Specialist
    # ───────────────────────────────────────────────────────────────────────
    def _run_placement_specialist(
        self,
        logical_nodes: List[dict],
        constraint_text: str,
        user_message: str,
        edges: List[dict],
        terminal_nets: dict,
        additional_context: str = ""
    ) -> str:
        """Run Stage 2: Placement Specialist.
        
        Returns:
            Raw LLM response containing [CMD] blocks
        """
        _log("Stage 2 - Placement Specialist")

        # Build RAG context
        try:
            rag_context = build_rag_context(
                logical_nodes,
                edges,
                terminal_nets,
                top_k=3
            )
        except Exception as exc:
            _log(f"RAG retrieval failed: {exc}")
            rag_context = ""

        # Build placement context
        placement_context = build_placement_context(
            logical_nodes,
            constraint_text,
            terminal_nets=terminal_nets,
            edges=edges,
        )

        specialist_user = (
            f"User request: {user_message}\n\n"
            f"SELECTED STRATEGY: {user_message}\n\n"
        )

        if rag_context:
            specialist_user += f"{rag_context}\n\n"

        specialist_user += f"{placement_context}\n"

        if additional_context:
            specialist_user += f"\n{additional_context}\n"

        specialist_msgs = [
            {"role": "system", "content": PLACEMENT_SPECIALIST_PROMPT},
            {"role": "user", "content": specialist_user},
        ]

        try:
            response = self._llm(specialist_msgs, specialist_user)
            _log(f"Stage 2 LLM response: {len(response)} chars")
            return response
        except Exception as exc:
            _log(f"Stage 2 LLM failed: {exc}")
            return ""

    # ───────────────────────────────────────────────────────────────────────
    # Stage 3: DRC Critic
    # ───────────────────────────────────────────────────────────────────────
    def _run_drc_critic(
        self,
        working_nodes: List[dict],
        constraint_text: str,
        user_message: str,
        terminal_nets: dict,
        edges: List[dict],
        accumulated_cmds: List[dict],
        post_deterministic_snapshot: List[dict]
    ) -> Tuple[List[dict], dict, List[dict]]:
        """Run Stage 3: DRC Critic with iterative correction.
        
        Returns:
            (updated_nodes, drc_result, accumulated_cmds)
        """
        _log("Stage 3 - DRC Critic")

        drc_result = {"pass": True, "violations": [], "summary": ""}

        # C1 fix: gap_px is in GUI pixels; DRC works in µm.  Convert here.
        # When gap_px == 0, gap_um == 0 (no gap enforcement — only overlap checking).
        gap_um = self._gap_px / PIXELS_PER_UM if self._gap_px > 0 else 0.0

        for attempt in range(self._max_drc_retries):
            drc_result = run_drc_check(working_nodes, gap_um)
            n_violations = len(drc_result["violations"])
            
            _log(f"DRC attempt {attempt + 1}/{self._max_drc_retries}: "
                 f"pass={drc_result['pass']}, violations={n_violations}")

            if drc_result["pass"]:
                break

            # Try LLM correction
            prior_cmds_text = _cmds_to_text(accumulated_cmds)
            violation_text = format_drc_violations_for_llm(drc_result, prior_cmds_text)

            # Rebuild placement context from current positions
            current_placement_context = build_placement_context(
                working_nodes,
                constraint_text,
                terminal_nets=terminal_nets,
                edges=edges,
            )

            critic_user = (
                f"User request: {user_message}\n\n"
                f"{violation_text}\n\n"
                f"=== CURRENT DEVICE POSITIONS ===\n{current_placement_context}"
            )

            critic_msgs = [
                {"role": "system", "content": DRC_CRITIC_PROMPT},
                {"role": "user", "content": critic_user},
            ]

            llm_correction_cmds = []
            try:
                critic_response = self._llm(critic_msgs, critic_user)
                llm_correction_cmds = _extract_cmd_blocks(critic_response)
                _log(f"DRC LLM gave {len(llm_correction_cmds)} correction(s)")
            except Exception as exc:
                _log(f"DRC critic LLM failed: {exc}")

            # Prescriptive fixes
            prescriptive_cmds = compute_prescriptive_fixes(
                drc_result,
                self._gap_px,
                working_nodes
            )
            _log(f"Prescriptive fixes: {len(prescriptive_cmds)}")

            # Merge corrections
            llm_dev_ids = {_cmd_key(c) for c in llm_correction_cmds}
            merged_cmds = list(llm_correction_cmds)
            
            for pc in prescriptive_cmds:
                dev = _cmd_key(pc)
                if dev not in llm_dev_ids:
                    merged_cmds.append(pc)

            if merged_cmds:
                # Update accumulated commands
                prev_by_dev = {_cmd_key(c): c for c in accumulated_cmds}
                for mc in merged_cmds:
                    prev_by_dev[_cmd_key(mc)] = mc
                accumulated_cmds = list(prev_by_dev.values())

                # Apply to post-deterministic snapshot
                working_nodes = _apply_cmds_to_nodes(
                    post_deterministic_snapshot,
                    accumulated_cmds
                )
                _log(f"Accumulated {len(accumulated_cmds)} total commands")

        # Final physics guard
        _log("Running final Physics Guard (overlap resolution)...")
        moved_ids = tool_resolve_overlaps(working_nodes)
        
        if moved_ids:
            _log(f"Physics Guard nudged {len(moved_ids)} devices")
            prev_by_dev = {_cmd_key(c): c for c in accumulated_cmds}
            
            for n in working_nodes:
                if n["id"] in moved_ids:
                    new_cmd = {
                        "action": "move",
                        "device": n["id"],
                        "x": n["geometry"]["x"],
                        "y": n["geometry"]["y"]
                    }
                    prev_by_dev[n["id"]] = new_cmd
            
            accumulated_cmds = list(prev_by_dev.values())
            
            # Re-check DRC
            drc_result = run_drc_check(working_nodes, self._gap_px)
            _log(f"Final DRC after Physics Guard: pass={drc_result['pass']}")

        self._emit_stage(2, {
            "drc_result": drc_result,
            "cmds": accumulated_cmds,
            "working_nodes": working_nodes,
        })

        return working_nodes, drc_result, accumulated_cmds

    # ───────────────────────────────────────────────────────────────────────
    # Stage 4: Routing Pre-Viewer
    # ───────────────────────────────────────────────────────────────────────
    def _run_routing_previewer(
        self,
        working_nodes: List[dict],
        edges: List[dict],
        terminal_nets: dict,
        user_message: str,
        accumulated_cmds: List[dict]
    ) -> Tuple[List[dict], dict, List[dict]]:
        """Run Stage 4: Routing optimization.
        
        Returns:
            (updated_nodes, routing_result, accumulated_cmds)
        """
        _log("Stage 4 - Routing Pre-Viewer")

        routing_result = score_routing(working_nodes, edges, terminal_nets)
        initial_cost = routing_result["placement_cost"]

        routing_text = format_routing_for_llm(
            routing_result,
            working_nodes,
            terminal_nets
        )

        current_positions = ", ".join(
            f"{n['id']}@({round(float(n['geometry']['x']), 3)},"
            f"{round(float(n['geometry']['y']), 3)})"
            for n in working_nodes if not n.get('is_dummy')
        )

        router_user = (
            f"User request: {user_message}\n\n"
            f"{routing_text}\n\n"
            f"Current positions: {current_positions}"
        )

        router_msgs = [
            {"role": "system", "content": ROUTING_PREVIEWER_PROMPT},
            {"role": "user", "content": router_user},
        ]

        try:
            router_response = self._llm(router_msgs, router_user)
            router_cmds = _extract_cmd_blocks(router_response)
        except Exception as exc:
            _log(f"Routing LLM failed: {exc}")
            router_cmds = []

        # Evaluate LLM suggestions
        if router_cmds:
            trial_nodes = _apply_cmds_to_nodes(working_nodes, router_cmds)
            tool_resolve_overlaps(trial_nodes)
            
            new_routing = score_routing(trial_nodes, edges, terminal_nets)
            new_cost = new_routing["placement_cost"]

            if new_cost < initial_cost:
                _log(f"Router improved cost: {new_cost:.4f} < {initial_cost:.4f}")
                working_nodes = trial_nodes
                routing_result = new_routing
                
                accum_dict = {_cmd_key(c): c for c in accumulated_cmds}
                for rc in router_cmds:
                    accum_dict[_cmd_key(rc)] = rc
                accumulated_cmds = list(accum_dict.values())
                
                initial_cost = new_cost
            else:
                _log(f"Router swaps rejected: {new_cost:.4f} >= {initial_cost:.4f}")

        # Hill-climb optimization
        for pass_num in range(MAX_ROUTING_HILL_CLIMB_PASSES):
            worst_nets = routing_result.get("worst_nets", [])
            if not worst_nets:
                _log(f"Hill-climb pass {pass_num + 1}: no worst nets")
                break

            targeted_swaps = _generate_targeted_swaps(
                working_nodes,
                worst_nets,
                terminal_nets
            )
            
            if not targeted_swaps:
                _log(f"Hill-climb pass {pass_num + 1}: no swap candidates")
                break

            trial_nodes = _apply_cmds_to_nodes(working_nodes, targeted_swaps)
            tool_resolve_overlaps(trial_nodes)
            trial_routing = score_routing(trial_nodes, edges, terminal_nets)
            trial_cost = trial_routing["placement_cost"]

            if trial_cost < initial_cost:
                _log(f"Hill-climb pass {pass_num + 1}: "
                     f"{initial_cost:.4f} → {trial_cost:.4f}")
                
                working_nodes = trial_nodes
                routing_result = trial_routing
                
                accum_dict = {_cmd_key(c): c for c in accumulated_cmds}
                for tc in targeted_swaps:
                    accum_dict[_cmd_key(tc)] = tc
                accumulated_cmds = list(accum_dict.values())
                
                initial_cost = trial_cost
            else:
                _log(f"Hill-climb pass {pass_num + 1}: no improvement")
                break

        self._emit_stage(3, {
            "routing_result": routing_result,
            "cmds": accumulated_cmds,
            "working_nodes": working_nodes,
        })

        return working_nodes, routing_result, accumulated_cmds

    # ───────────────────────────────────────────────────────────────────────
    # Main Entry Point
    # ───────────────────────────────────────────────────────────────────────
    def continue_placement(
        self,
        user_message: str,
        layout_context: dict,
        constraint_text: str
    ) -> str:
        """Execute Stages 2-4 and return final response.
        
        Args:
            user_message: User's confirmation/feedback text
            layout_context: {nodes, edges, terminal_nets, gap_px (optional)}
            constraint_text: Topology constraints from Stage 1
        
        Returns:
            Final agent response with [CMD] blocks and summary
        """
        nodes = layout_context.get("nodes", [])
        edges = layout_context.get("edges", [])
        terminal_nets = layout_context.get("terminal_nets", {})
        gap_px = float(layout_context.get("gap_px", self._gap_px))

        _log("Resuming pipeline from Stage 2...")

        # Parse user's placement mode choice (interdigitated / common_centroid / auto)
        placement_mode = parse_placement_mode(user_message, constraint_text)
        _log(f"Placement mode: {placement_mode} (from user reply: {user_message[:40]!r})")

        # Store original physical nodes
        self.original_physical_nodes = copy.deepcopy(nodes)
        self.constraint_text = constraint_text

        # Aggregate to logical devices
        logical_nodes = aggregate_to_logical_devices(nodes)
        _log(f"Aggregated {len(nodes)} physical → {len(logical_nodes)} logical devices")

        # ═══════════════════════════════════════════════════════════════════
        # STAGE 2: Placement Specialist
        # ═══════════════════════════════════════════════════════════════════
        placement_response = self._run_placement_specialist(
            logical_nodes,
            constraint_text,
            user_message,
            edges,
            terminal_nets
        )

        stage2_cmds = _extract_cmd_blocks(placement_response)
        working_nodes = _apply_cmds_to_nodes(nodes, stage2_cmds)

        # Device conservation check
        conservation = tool_validate_device_count(nodes, working_nodes)
        if not conservation["pass"]:
            # Bug #8: conservation failure logs missing device IDs explicitly
            missing_ids = conservation.get("missing", [])
            _log(f"⚠ Stage 2 CONSERVATION FAILURE: missing devices {missing_ids}")
            working_nodes = copy.deepcopy(nodes)
            stage2_cmds = []

        self._emit_stage(1, {
            "cmds": stage2_cmds,
            "placement_response": placement_response,
            "working_nodes": working_nodes,
        })

        # ═══════════════════════════════════════════════════════════════════
        # STAGE 2.5: Deterministic Optimizer
        # ═══════════════════════════════════════════════════════════════════
        _log("Stage 2.5 - Deterministic Optimizer")
        _log(f"  constraint_text has 'MIRROR': {'MIRROR' in constraint_text.upper() if constraint_text else 'NO CONSTRAINT TEXT'}")
        _log(f"  constraint_text length: {len(constraint_text) if constraint_text else 0}")
        _log(f"  terminal_nets keys: {list((terminal_nets or {}).keys())[:10]}")
        
        # Snapshot pre-optimization x-positions
        pre_opt_positions = {
            n["id"]: round(float(n["geometry"]["x"]), 3)
            for n in working_nodes if not n.get("is_dummy")
        }
        
        working_nodes = apply_deterministic_optimizations(
            working_nodes,
            constraint_text,
            terminal_nets,
            edges,
            placement_mode=placement_mode,
        )
        
        # Check if positions changed
        post_opt_positions = {
            n["id"]: round(float(n["geometry"]["x"]), 3)
            for n in working_nodes if not n.get("is_dummy")
        }
        changed = {k: (pre_opt_positions.get(k), v) 
                   for k, v in post_opt_positions.items() 
                   if pre_opt_positions.get(k) != v}
        if changed:
            _log(f"  Deterministic optimizer CHANGED {len(changed)} positions")
            for dev_id, (old, new) in list(changed.items())[:5]:
                _log(f"    {dev_id}: x={old} -> x={new}")
        else:
            _log("  Deterministic optimizer: NO position changes")

        # Generate accumulated commands from deterministic state
        accumulated_cmds = []
        for n in working_nodes:
            if not n.get("is_dummy"):
                accumulated_cmds.append({
                    "action": "move",
                    "device": n["id"],
                    "x": float(n["geometry"]["x"]),
                    "y": float(n["geometry"]["y"])
                })

        _log(f"Deterministic optimizer applied to {len(accumulated_cmds)} devices")

        # Snapshot after deterministic optimization
        post_deterministic_snapshot = copy.deepcopy(working_nodes)

        # ═══════════════════════════════════════════════════════════════════
        # STAGE 3: DRC Critic
        # ═══════════════════════════════════════════════════════════════════
        working_nodes, drc_result, accumulated_cmds = self._run_drc_critic(
            working_nodes,
            constraint_text,
            user_message,
            terminal_nets,
            edges,
            accumulated_cmds,
            post_deterministic_snapshot
        )

        # ═══════════════════════════════════════════════════════════════════
        # STAGE 4: Routing Pre-Viewer
        # ═══════════════════════════════════════════════════════════════════
        working_nodes, routing_result, accumulated_cmds = self._run_routing_previewer(
            working_nodes,
            edges,
            terminal_nets,
            user_message,
            accumulated_cmds
        )

        # ═══════════════════════════════════════════════════════════════════
        # POSTPROCESSING: Multi-Finger Validation
        # ═══════════════════════════════════════════════════════════════════
        finger_validation = _validate_multi_finger_placement(working_nodes)
        if not finger_validation["pass"]:
            _log(f"⚠ Multi-finger validation failed:\n{finger_validation['summary']}")
            for v in finger_validation["violations"]:
                _log(f"  {v}")

        # ═══════════════════════════════════════════════════════════════════
        # FINAL COMMAND COMPILATION
        # ═══════════════════════════════════════════════════════════════════
        final_cmds = []
        for n in working_nodes:
            if n.get("is_dummy"):
                continue

            try:
                x = round(float(n["geometry"]["x"]), 3)
                y = round(float(n["geometry"]["y"]), 3)
            except (TypeError, KeyError, ValueError) as exc:
                _log(f"⚠ Skipping device {n.get('id', '?')}: bad geometry ({exc})")
                continue

            final_cmds.append({
                "action": "move",
                "device": n["id"],
                "x": x,
                "y": y,
            })

        if not final_cmds:
            _log("⚠ CRITICAL: final_cmds empty! Falling back to original nodes")
            for n in nodes:
                if not n.get("is_dummy"):
                    final_cmds.append({
                        "action": "move",
                        "device": n["id"],
                        "x": round(float(n["geometry"]["x"]), 3),
                        "y": round(float(n["geometry"]["y"]), 3),
                    })

        # ═══════════════════════════════════════════════════════════════════
        # SAVE TO RAG — only when the run produced a high-quality result
        # ═══════════════════════════════════════════════════════════════════
        # Gate: save only if DRC passed AND routing cost is below a threshold.
        # This prevents the RAG store from growing unboundedly with mediocre
        # examples, which would degrade retrieval quality over time (M5 fix).
        drc_passed   = drc_result.get("pass", False)
        routing_cost = routing_result.get("placement_cost", 9999)
        RAG_QUALITY_THRESHOLD = 5.0   # normalized cost units
        if drc_passed and routing_cost < RAG_QUALITY_THRESHOLD:
            try:
                run_label = (
                    f"quality_drcOK_cost{routing_cost:.2f}_"
                    f"{len(final_cmds)}cmds"
                )
                save_run_as_example(
                    working_nodes,
                    edges,
                    terminal_nets,
                    drc_result,
                    routing_result,
                    label=run_label,
                )
                _log(f"[RAG] High-quality run saved as '{run_label}'")
            except Exception as rag_exc:
                _log(f"[RAG] Save failed: {rag_exc}")
        else:
            _log(
                f"[RAG] Run NOT saved: drc_pass={drc_passed}, "
                f"routing_cost={routing_cost:.2f} "
                f"(threshold={RAG_QUALITY_THRESHOLD})"
            )

        # ═══════════════════════════════════════════════════════════════════
        # BUILD FINAL RESPONSE
        # ═══════════════════════════════════════════════════════════════════
        drc_status = (
            "✅ Pass"
            if drc_result["pass"]
            else f"⚠ {len(drc_result['violations'])} violation(s)"
        )

        summary_header = (
            f"**[Multi-Agent Pipeline Complete]**\n\n"
            f"• Topology: {len(constraint_text.splitlines())} constraint lines\n"
            f"• DRC: {drc_status}\n"
            f"• Routing: {routing_result['score']} overlap(s)\n"
            f"• Commands: {len(final_cmds)} emitted\n\n"
        )

        final_response = summary_header + _cmds_to_text(final_cmds)

        _log(f"Pipeline complete: {len(final_cmds)} final commands")

        return final_response
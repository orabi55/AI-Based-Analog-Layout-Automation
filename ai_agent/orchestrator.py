"""
ai_agent/orchestrator.py  (v3 – fixes only, no lines deleted)
=============================================================
Multi-Agent Orchestrator for Analog Layout Automation.

Sequential pipeline:
  Stage 0 → (pre-stage: pure-Python DRC baseline on original placement)
  Stage 1 – Topology Analyst     → constraint extraction
  Stage 2 – Placement Specialist → [CMD] blocks
  Stage 3 – DRC Critic (retry)  → context-preserving, prescriptive correction
               ↳ Fallback: compute_prescriptive_fixes() if LLM unavailable
  Stage 4 – Routing Pre-Viewer  → swap suggestions for minimal crossings

The Orchestrator is pure Python (no Qt). Driven by OrchestratorWorker
(in llm_worker.py) which runs it on a background QThread.

Stage callback  (optional):
    stage_callback(stage_index: int, stage_name: str, data: dict) → None
    Called after each stage completes. Use to emit Qt signals from the worker.

Fixes applied (v2 → v3):
  [1] Router CMD blocks no longer applied twice (duplicate second block removed).
  [2] Redundant `import re` inside continue_placement() removed.
  [3] All inline hot-path imports moved to module level.
  [4] DRC retry loop now applies accumulated_cmds to post-2.5 snapshot,
      not to the original `nodes`.
  [5] Stage 3 critic now rebuilds placement_context from current
      working_nodes on every retry attempt.
  [6] accumulated_cmds merge uses stable _cmd_key() helper to avoid
      silent key collisions between swap / move / flip commands.
  [7] Dead hill-climb `pass` block replaced with real swap evaluation.
  [8] MIN_DEVICE_SPACING_UM promoted to a named module-level constant
      and made configurable via Orchestrator.__init__.
"""

import json
import re
import copy

from ai_agent.topology_analyst import (
    TOPOLOGY_ANALYST_PROMPT, analyze_topology,
)
from ai_agent.placement_specialist import (
    PLACEMENT_SPECIALIST_PROMPT, build_placement_context,
)
from ai_agent.drc_critic import (
    DRC_CRITIC_PROMPT, run_drc_check,
    format_drc_violations_for_llm, compute_prescriptive_fixes,
)
from ai_agent.routing_previewer import (
    ROUTING_PREVIEWER_PROMPT, score_routing, format_routing_for_llm,
)
from ai_agent.strategy_selector import generate_strategies
from ai_agent.rag_retriever import build_rag_context, save_run_as_example
from ai_agent.pipeline_optimizer import apply_deterministic_optimizations

# Fix [3] – moved from inside continue_placement() to module level
from ai_agent.tools import tool_validate_device_count, tool_resolve_overlaps

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------
# Fix [8] – named constant replaces magic number 0.294 inside helper
DEFAULT_MIN_DEVICE_SPACING_UM: float = 0.294

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _log(msg):
    print(f"[ORCH] {msg}")
def _extract_cmd_blocks(text: str) -> list:
    """Return list of parsed [CMD]...[/CMD] dicts from response text.

    Hardened against:
      - Markdown code fences wrapping CMD blocks  (``` or ```json)
      - Unicode lookalike brackets  ［CMD］
      - Extra whitespace inside tags  [ CMD ]
      - Lowercase tags  [cmd]
      - Newlines between tag and JSON body
      - Truncated / malformed JSON (repair attempted before skip)
    """
    if not text:
        return []

    # ── Step 1: strip markdown code fences ──────────────────────────
    text = re.sub(r'```[a-zA-Z]*\n?', '', text)  # opening fence  ``` or ```json
    text = re.sub(r'```',             '',  text)  # closing fence  ```

    # ── Step 2: normalise unicode lookalike brackets ─────────────────
    text = text.replace('\uff3b', '[').replace('\uff3d', ']')  # ［ ］ full-width
    text = text.replace('\u27e6', '[').replace('\u27e7', ']')  # ⟦ ⟧ mathematical

    # ── Step 3: normalise tag whitespace and case ────────────────────
    # Converts [ CMD ], [cmd], [ /CMD ], [/cmd] → [CMD] / [/CMD]
    text = re.sub(
        r'\[\s*/?\s*[Cc][Mm][Dd]\s*\]',
        lambda m: '[/CMD]' if '/' in m.group() else '[CMD]',
        text,
    )

    # ── Step 4: extract and parse ────────────────────────────────────
    cmds    = []
    pattern = re.compile(r'\[CMD\](.*?)\[/CMD\]', re.DOTALL | re.IGNORECASE)

    for match in pattern.finditer(text):
        raw = match.group(1).strip()

        # Remove any stray fence markers that snuck inside the block
        raw = re.sub(r'```[a-zA-Z]*', '', raw).strip()

        if not raw:
            _log("  Warning: empty CMD block skipped.")
            continue

        try:
            cmds.append(json.loads(raw))

        except json.JSONDecodeError as exc:
            # Light repair pass before giving up
            repaired = raw
            repaired = re.sub(r',\s*}',  '}',  repaired)  # trailing comma in {}
            repaired = re.sub(r',\s*\]', ']',  repaired)  # trailing comma in []
            repaired = repaired.replace("'", '"')           # single → double quotes
            try:
                cmds.append(json.loads(repaired))
                _log(f"  Warning: CMD block auto-repaired: {raw[:80]!r}")
            except json.JSONDecodeError:
                _log(
                    f"  Warning: skipping malformed CMD block "
                    f"(raw={raw[:80]!r}, error={exc})"
                )

    # ── Step 5: diagnostic when CMD markers present but nothing parsed
    if not cmds:
        raw_markers = re.findall(r'(?i)\[/?cmd\]|［/?CMD］', text)
        if raw_markers:
            _log(
                f"  ⚠ _extract_cmd_blocks: found {len(raw_markers)} CMD "
                f"marker(s) in text but parsed 0. "
                f"First 300 chars repr: {repr(text[:300])}"
            )

    return cmds




def _cmds_to_text(cmds):
    """Serialise command dicts back to [CMD] block strings for LLM context."""
    return "\n".join(
        f"[CMD]{json.dumps(c)}[/CMD]" for c in cmds
    )


# Fix [6] – new stable key helper used in all merge operations
def _cmd_key(cmd: dict) -> str:
    """Stable, collision-free lookup key for any command type.

    Handles:
      move / move_device  → keyed on 'device' / 'device_id' / 'id'
      swap / swap_devices → keyed on 'device_a' / 'a'
      flip / flip_h / flip_v → keyed on 'device' / 'id'
    Falls back to 'unknown' so bad commands never raise KeyError.
    """
    return (
        cmd.get("device")
        or cmd.get("device_id")
        or cmd.get("id")
        or cmd.get("device_a")   # swap commands use device_a as primary key
        or cmd.get("a")
        or "unknown"
    )


def _apply_cmds_to_nodes(nodes, cmds):
    """Apply swap/move/flip commands to a *copy* of nodes."""
    nodes = copy.deepcopy(nodes)
    id_map = {n['id']: n for n in nodes}

    for cmd in cmds:
        action = cmd.get('action', '')
        if action in ('swap', 'swap_devices'):
            a_id = cmd.get('device_a', cmd.get('a'))
            b_id = cmd.get('device_b', cmd.get('b'))
            if a_id in id_map and b_id in id_map:
                ga, gb = id_map[a_id]['geometry'], id_map[b_id]['geometry']
                ga['x'], gb['x'] = gb['x'], ga['x']
                ga['y'], gb['y'] = gb['y'], ga['y']
                ga['orientation'], gb['orientation'] = (
                    gb.get('orientation','R0'), ga.get('orientation','R0'))
        elif action in ('move', 'move_device'):
            dev_id = cmd.get('device', cmd.get('device_id', cmd.get('id')))
            if dev_id in id_map:
                if cmd.get('x') is not None:
                    id_map[dev_id]['geometry']['x'] = float(cmd['x'])
                
                # NOTE: We deliberately IGNORE any 'y' coordinate changes from the LLM.
                # The LLM is strictly instructed to keep PMOS in the PMOS row and NMOS in the NMOS row.
                # However, Gemini occasionally hallucinates bizarre negative values (e.g. y=-0.672),
                # which throws the device extremely off-screen in the GUI, making it "disappear".
        elif action in ('flip', 'flip_h', 'flip_v'):
            dev_id = cmd.get('device', cmd.get('id'))
            if dev_id in id_map:
                cur = id_map[dev_id]['geometry'].get('orientation','R0')
                flip_map = {
                    'R0':'R0_FH','R0_FH':'R0','R0_FV':'R0_FH_FV','R0_FH_FV':'R0_FV'
                }
                id_map[dev_id]['geometry']['orientation'] = (
                    flip_map.get(cur, cur))
        elif action == 'delete':
            dev_id = cmd.get('device', cmd.get('id'))
            nodes = [n for n in nodes if n['id'] != dev_id]
            id_map = {n['id']: n for n in nodes}
            
    _deduplicate_positions(nodes)
    return nodes


def _deduplicate_positions(nodes, min_spacing=DEFAULT_MIN_DEVICE_SPACING_UM):  # Fix [8]
    """Ensure no two devices in the same row share an x-slot (in-place)."""
    rows = {}
    for n in nodes:
        ry = round(float(n['geometry']['y']), 4)
        rows.setdefault(ry, []).append(n)
        
    for row_nodes in rows.values():
        row_nodes.sort(key=lambda n: float(n['geometry']['x']))
        for i in range(1, len(row_nodes)):
            prev, curr = row_nodes[i-1], row_nodes[i]
            prev_end = float(prev['geometry']['x']) + float(prev['geometry']['width'])
            curr_x = float(curr['geometry']['x'])
            
            if curr_x < prev_end - 0.001:
                snapped = round(prev_end / min_spacing) * min_spacing
                if snapped < prev_end - 0.001:
                    snapped += min_spacing
                curr['geometry']['x'] = snapped


# Fix [7] – replaces the dead `pass` hill-climb loop
def _generate_targeted_swaps(nodes, worst_nets, terminal_nets):
    """Generate swap commands targeting the highest-cost nets.

    For each worst net, find the leftmost and rightmost device that
    belongs to the net and propose swapping each with their immediate
    x-neighbour (if that neighbour is NOT on the same net) to shorten
    the estimated wire span.

    Args:
        nodes        : current working node list.
        worst_nets   : list of net names with highest routing cost.
        terminal_nets: dict mapping device_id -> {pin: net_name}.

    Returns:
        List of swap command dicts (may be empty).
    """
    if not worst_nets or not nodes:
        return []

    # Build net -> [node, ...] mapping
    net_to_devices = {}
    for node in nodes:
        dev_id = node['id']
        nets = terminal_nets.get(dev_id, {})
        for net in nets.values():
            net_to_devices.setdefault(net, []).append(node)

    # Sort nodes row-by-row, left-to-right for neighbour lookup
    sorted_nodes = sorted(
        nodes,
        key=lambda n: (
            round(float(n['geometry']['y']), 2),
            float(n['geometry']['x']),
        ),
    )
    index_map = {n['id']: i for i, n in enumerate(sorted_nodes)}

    swap_cmds  = []
    seen_pairs = set()

    for net in worst_nets:
        net_devices = net_to_devices.get(net, [])
        if len(net_devices) < 2:
            continue

        net_devices_sorted = sorted(
            net_devices, key=lambda n: float(n['geometry']['x'])
        )
        left_node  = net_devices_sorted[0]
        right_node = net_devices_sorted[-1]
        left_id    = left_node['id']
        right_id   = right_node['id']
        net_ids    = {d['id'] for d in net_devices}

        left_idx  = index_map.get(left_id,  -1)
        right_idx = index_map.get(right_id, -1)

        # Try swapping left_node with its immediate right neighbour
        if 0 <= left_idx < len(sorted_nodes) - 1:
            neighbour = sorted_nodes[left_idx + 1]
            n_id = neighbour['id']
            pair = tuple(sorted([left_id, n_id]))
            if n_id not in net_ids and pair not in seen_pairs:
                swap_cmds.append({
                    'action':   'swap',
                    'device_a': left_id,
                    'device_b': n_id,
                })
                seen_pairs.add(pair)

        # Try swapping right_node with its immediate left neighbour
        if right_idx > 0:
            neighbour = sorted_nodes[right_idx - 1]
            n_id = neighbour['id']
            pair = tuple(sorted([right_id, n_id]))
            if n_id not in net_ids and pair not in seen_pairs:
                swap_cmds.append({
                    'action':   'swap',
                    'device_a': right_id,
                    'device_b': n_id,
                })
                seen_pairs.add(pair)

    return swap_cmds


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
class Orchestrator:
    """Runs the 4-stage self-correcting multi-agent pipeline.

    Args:
        run_llm_fn: callable(chat_messages: list, full_prompt: str) → str
        sp_file_path (str | None): path to SPICE .sp file for topology.
        gap_px (float): minimum device gap for DRC check (pixels).
        max_drc_retries (int): max DRC critic retry attempts per run.
        min_spacing (float): minimum x-spacing between devices in same row (µm).
                             Defaults to DEFAULT_MIN_DEVICE_SPACING_UM.
        stage_callback (callable | None):
            Optional hook called at the end of each stage:
                stage_callback(stage_index: int, stage_name: str, data: dict)
            data keys vary per stage but always include "working_nodes".
    """

    STAGE_NAMES = [
        "Topology Analyst",
        "Placement Specialist",
        "DRC Critic",
        "Routing Pre-Viewer",
    ]

    def __init__(
        self,
        run_llm_fn,
        sp_file_path=None,
        gap_px=0.0,
        max_drc_retries=2,
        min_spacing=DEFAULT_MIN_DEVICE_SPACING_UM,  # Fix [8]
        stage_callback=None,
    ):
        self._llm = run_llm_fn
        self._sp_file = sp_file_path
        self._gap_px = gap_px
        self._max_drc_retries = max_drc_retries
        self._min_spacing = min_spacing  # Fix [8]
        self._stage_cb = stage_callback
        

    # ------------------------------------------------------------------
    def _emit_stage(self, index, data):
        """Call the optional stage callback safely."""
        if self._stage_cb is not None:
            try:
                self._stage_cb(index, self.STAGE_NAMES[index], data)
            except Exception as exc:
                _log(f"stage_callback error (ignored): {exc}")

    # ------------------------------------------------------------------
    def run_topology_analysis(self, user_message, layout_context):
        """Stage 1 only: Extract topology and ASK the user for confirmation.
        
        Args:
            user_message (str): the user's request text.
            layout_context (dict): {nodes, edges, terminal_nets, [gap_px]}

        Returns:
            tuple: (str, str) The question for the user, and the raw constraint text.
        """
        nodes          = layout_context.get("nodes", [])
        terminal_nets  = layout_context.get("terminal_nets", {})

        _log(f"Stage 1 starting for: {user_message[:80]!r}")

        # 1. Extract Topology (Pure Python initially)
        constraint_text = analyze_topology(nodes, terminal_nets, self._sp_file)
        _log(f"  Pure-Python constraints: {len(constraint_text)} chars")

        try:
            analyst_user = (
                f"User request: {user_message}\n\n"
                f"Device list ({len(nodes)} devices):\n"
                + "\n".join(
                    f"  {n['id']} ({n.get('type','?')}) "
                    f"nets={terminal_nets.get(n['id'], {})}"
                    for n in nodes[:30]
                )
                + (f"\n  ... ({len(nodes)-30} more)" if len(nodes) > 30 else "")
                + f"\n\nPre-extracted constraints:\n{constraint_text}"
            )
            analyst_msgs = [
                {"role": "system", "content": TOPOLOGY_ANALYST_PROMPT},
                {"role": "user",   "content": analyst_user},
            ]
            analyst_response = self._llm(analyst_msgs, analyst_user)
            
            # Use the LLM response as the question if it looks conversational
            question = analyst_response.strip()
            
            # If the LLM returned JSON (old prompt behavior), fallback to a standard question
            if question.startswith("{") and question.endswith("}"):
                try:
                    j = json.loads(question)
                    if "constraints" in j:
                        extra = "\n".join(
                            f"  {c.get('type','?')}: {', '.join(c.get('devices', []))}"
                            for c in j["constraints"]
                        )
                        constraint_text += "\n[LLM constraints]\n" + extra
                except (json.JSONDecodeError, TypeError):
                    pass
                question = None

        except Exception as exc:
            _log(f"  Stage 1 LLM failed (pure-Python only): {exc}")
            question = None

        if not question:
            # Provide a fallback question based on purely extracted constraints
            question = (
                f"🔬 **Topology Analysis Complete**\n\n"
                f"I identified the following structures automatically:\n\n"
                f"{constraint_text}\n\n"
                f"**Is this correct?** Please reply with 'Yes' to proceed, "
                f"or let me know if any matches are incorrect before I start the placement."
            )

        self._emit_stage(0, {
            "constraint_text": constraint_text,
            "working_nodes": nodes,
        })

        # ── Stage 1.5: Strategy Selector ─────────────────────────────────
        # Generate circuit-specific strategy options, append to topology question.
        try:
            strategy_text = generate_strategies(user_message, constraint_text, self._llm)
        except Exception as exc:
            _log(f"  Strategy selector failed (using fallback): {exc}")
            strategy_text = (
                "1. **Enhance Symmetry** — Place matched pairs equidistant from the row centre.\n"
                "2. **Improve Matching** — Abut mirror devices with the same orientation.\n"
                "3. **Minimise DRC Violations** — Resolve all overlaps first.\n\n"
                "Type a number (1-3), 'all', or describe a custom approach to proceed."
            )

        combined_question = question + "\n\n---\n\n" + strategy_text
        return combined_question, constraint_text

    def continue_placement(self, user_message, layout_context, constraint_text):
        """Execute Stages 2-4 and return the final response string.

        Args:
            user_message (str): the user's feedback/confirmation text.
            layout_context (dict): {nodes, edges, terminal_nets, [gap_px]}
            constraint_text (str): The topology constraints extracted in Stage 1.

        Returns:
            str: final agent response (contains [CMD] blocks + summary header).
        """
        nodes          = layout_context.get("nodes", [])
        edges          = layout_context.get("edges", [])
        terminal_nets  = layout_context.get("terminal_nets", {})
        gap_px         = float(layout_context.get("gap_px", self._gap_px))

        _log("Resuming pipeline from Stage 2...")

        # ──────────────────────────────────────────────────────────────
        # Stage 2 – Placement Specialist
        # ──────────────────────────────────────────────────────────────
        _log("Stage 2 – Placement Specialist")
        placement_context = build_placement_context(
            nodes, constraint_text,
            terminal_nets=terminal_nets,
            edges=edges,
        )
        specialist_user = (
            f"User request: {user_message}\n\n"
            f"SELECTED STRATEGY: The user's chosen improvement approach is: {user_message}\n"
            f"Apply this strategy specifically when generating placement commands.\n\n"
            f"\n{placement_context}"
        )
        specialist_msgs = [
            {"role": "system", "content": PLACEMENT_SPECIALIST_PROMPT},
            {"role": "user",   "content": specialist_user},
        ]
        try:
            placement_response = self._llm(specialist_msgs, specialist_user)
            _log(f"  [RAW Stage 2 Response]:\n{placement_response}\n")
        except Exception as exc:
            _log(f"  Stage 2 failed: {exc}")
            placement_response = ""

        stage2_cmds   = _extract_cmd_blocks(placement_response)
        working_nodes = _apply_cmds_to_nodes(nodes, stage2_cmds)
        
        # Fix [3] – tool_validate_device_count already imported at module level
        conservation = tool_validate_device_count(nodes, working_nodes)
        if not conservation["pass"]:
            _log(f"  ⚠ Stage 2 CONSERVATION FAILURE: {conservation['missing']}. Reverting to original inventory.")
            working_nodes = copy.deepcopy(nodes)
            stage2_cmds = []

        self._emit_stage(1, {
            "cmds": stage2_cmds,
            "placement_response": placement_response,
            "working_nodes": working_nodes,
        })

        # ──────────────────────────────────────────────────────────────
        # Stage 2.5 – Deterministic Optimizer (Critical Step)
        # ──────────────────────────────────────────────────────────────
        # Stage 2.5 – Deterministic Optimizer
        # ──────────────────────────────────────────────────────────────
        _log("Stage 2.5 – Deterministic Optimizer")
        
        working_nodes = apply_deterministic_optimizations(
            working_nodes,
            constraint_text,
            terminal_nets,
            edges,
            gap_px
        )
        
        # Generate fresh accumulated_cmds from deterministic state
        accumulated_cmds = []
        for n in working_nodes:
            if not n.get("is_dummy"):
                accumulated_cmds.append({
                    "action": "move",
                    "device": n["id"],
                    "x": float(n["geometry"]["x"]),
                    "y": float(n["geometry"]["y"])
                })
        
        _log(f"  Deterministic optimizer applied to {len(accumulated_cmds)} devices.")

        # Fix [4] – snapshot nodes AFTER Stage 2.5 so DRC retries apply
        # accumulated_cmds on top of physics-valid positions, not `nodes`.
        post_deterministic_snapshot = copy.deepcopy(working_nodes)

        # ──────────────────────────────────────────────────────────────
        # Stage 3 – DRC Critic (context-preserving retry loop)
        # ──────────────────────────────────────────────────────────────
        _log("Stage 3 – DRC Critic")

        # Track: which CMDs produced the current working_nodes
        # We start with accumulated_cmds computed in Stage 2.5
        critic_response  = placement_response
        drc_result       = {"pass": True, "violations": [], "structured": [], "summary": ""}


        for attempt in range(self._max_drc_retries):
            drc_result = run_drc_check(working_nodes, gap_px)
            n_v = len(drc_result["violations"])
            _log(f"  DRC attempt {attempt+1}/{self._max_drc_retries}: "
                 f"pass={drc_result['pass']}, violations={n_v}")

            if drc_result["pass"]:
                break
                
            # ---- Step A: try LLM correction first ----
            prior_cmds_text = _cmds_to_text(accumulated_cmds)
            violation_text  = format_drc_violations_for_llm(drc_result, prior_cmds_text)

            # Fix [5] – rebuild placement_context from current working_nodes
            # so the LLM sees up-to-date coordinates, not the original positions.
            current_placement_context = build_placement_context(
                working_nodes, constraint_text,
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
                {"role": "user",   "content": critic_user},
            ]
            llm_correction_cmds = []
            try:
                critic_response      = self._llm(critic_msgs, critic_user)
                _log(f"  [RAW Stage 3 Critic Response]:\n{critic_response}\n")
                llm_correction_cmds  = _extract_cmd_blocks(critic_response)
                _log(f"  DRC LLM gave {len(llm_correction_cmds)} correction CMD(s)")
            except Exception as exc:
                _log(f"  DRC critic LLM call failed: {exc}")

            # ---- Step B: always layer in prescriptive fixes for remaining overlaps ----
            prescriptive_cmds = compute_prescriptive_fixes(drc_result, gap_px, working_nodes)
            _log(f"  Prescriptive fallback: {len(prescriptive_cmds)} fix(es)")

            # Merge: LLM cmds take precedence; prescriptive fills gaps
            # Fix [6] – use _cmd_key for collision-safe key lookup
            llm_dev_ids   = {_cmd_key(c) for c in llm_correction_cmds}
            merged_cmds   = list(llm_correction_cmds)
            for pc in prescriptive_cmds:
                dev = _cmd_key(pc)
                if dev not in llm_dev_ids:
                    merged_cmds.append(pc)
                    _log(f"    Prescriptive: Move {dev} to x={pc.get('x')}")

            if merged_cmds:
                # Fix [6] – use _cmd_key in the merge dict for collision safety
                prev_by_dev = {_cmd_key(c): c for c in accumulated_cmds}
                for mc in merged_cmds:
                    prev_by_dev[_cmd_key(mc)] = mc   # new cmd overrides old for same device
                accumulated_cmds = list(prev_by_dev.values())

                # Fix [4] – apply to post-deterministic snapshot, not original `nodes`
                working_nodes    = _apply_cmds_to_nodes(post_deterministic_snapshot, accumulated_cmds)
                _log(f"  Accumulated total: {len(accumulated_cmds)} cmd(s) across {attempt+1} attempt(s)")

        # ---- Step C: Final Safety Pass (Physics Guard) ----
        # Unconditionally force a pure-python overlap resolution pass
        # Fix [3] – tool_resolve_overlaps already imported at module level
        _log("  Running final Physics Guard post-processing (tool_resolve_overlaps)...")
        moved_ids = tool_resolve_overlaps(working_nodes)
        
        # Since working_nodes were mutated in place, we need to generate new [CMD] blocks
        # for any device X-positions that were nudged to the right.
        if moved_ids:
            _log(f"  Physics Guard nudged {len(moved_ids)} device(s): {', '.join(moved_ids)}")
            prev_by_dev = {_cmd_key(c): c for c in accumulated_cmds}  # Fix [6]
            
            for n in working_nodes:
                if n["id"] in moved_ids:
                    target_x = n["geometry"]["x"]
                    target_y = n["geometry"]["y"]
                    new_cmd = {"action": "move", "device": n["id"], "x": target_x, "y": target_y}
                    prev_by_dev[n["id"]] = new_cmd
            
            accumulated_cmds = list(prev_by_dev.values())
            
            # Re-run DRC check to confirm the Physics Guard worked
            drc_result = run_drc_check(working_nodes, gap_px)
            _log(f"  Final DRC status after Physics Guard: pass={drc_result['pass']}")

        _log(f"Stage 3 done: pass={drc_result['pass']}, "
             f"final_cmds={len(accumulated_cmds)}")

        self._emit_stage(2, {
            "drc_result": drc_result,
            "cmds": accumulated_cmds,
            "working_nodes": working_nodes,
        })

        # ──────────────────────────────────────────────────────────────
        # Stage 4 – Routing Pre-Viewer
        # ──────────────────────────────────────────────────────────────
        # Stage 4 – Routing Pre-Viewer
        # ──────────────────────────────────────────────────────────────
        _log("Stage 4 – Routing Pre-Viewer")
        
        routing_result = score_routing(working_nodes, edges, terminal_nets)
        initial_cost = routing_result["placement_cost"]
        
        routing_text = format_routing_for_llm(
            routing_result, working_nodes, terminal_nets
        )
        
        current_positions_compact = ", ".join(
            f"{n['id']}@({round(float(n['geometry']['x']),3)},"
            f"{round(float(n['geometry']['y']),3)})"
            for n in working_nodes
            if not n.get('is_dummy')
        )

        router_user = (
            f"User request: {user_message}\n\n"
            f"{routing_text}\n\n"
            f"Current device positions: {current_positions_compact}"
        )
        
        router_msgs = [
            {"role": "system", "content": ROUTING_PREVIEWER_PROMPT},
            {"role": "user", "content": router_user},
        ]
        
        try:
            final_response = self._llm(router_msgs, router_user)
        except Exception:
            final_response = ""
        
        router_cmds = _extract_cmd_blocks(final_response)
        
        # Fix reference leak: always deep-copy before trial so working_nodes
        # is NEVER mutated by tool_resolve_overlaps on a rejected path
        if router_cmds:
            # Deep copy so rejection leaves working_nodes completely untouched
            trial_nodes = _apply_cmds_to_nodes(working_nodes, router_cmds)
            tool_resolve_overlaps(trial_nodes)   # mutates trial_nodes only
        
            new_routing = score_routing(trial_nodes, edges, terminal_nets)
            new_cost    = new_routing["placement_cost"]
        
            if new_cost < initial_cost:
                _log(f"  Router improved cost: {new_cost:.4f} < {initial_cost:.4f}. Accepting.")
                working_nodes    = trial_nodes   # safe: trial_nodes is already a copy
                routing_result   = new_routing
                
                accum_dict = {_cmd_key(c): c for c in accumulated_cmds}
                for rc in router_cmds:
                    accum_dict[_cmd_key(rc)] = rc
                accumulated_cmds = list(accum_dict.values())
                
                initial_cost     = new_cost      # keep initial_cost in sync
            else:
                _log(
                    f"  Router swaps did not improve cost "
                    f"({new_cost:.4f} >= {initial_cost:.4f}). Rejected. "
                    f"working_nodes unchanged."
                )
                # trial_nodes is discarded here – working_nodes never touched
        else:
            _log("  Stage 4 LLM returned no CMD blocks. Skipping router evaluation.")

        # Hill-climb: initial_cost is now always correct because it tracks
        # the actual cost of the current working_nodes
        for pass_num in range(3):
            worst_nets = routing_result.get("worst_nets", [])
            if not worst_nets:
                _log(f"  Hill-climb pass {pass_num + 1}: no worst nets, stopping.")
                break

            targeted_swaps = _generate_targeted_swaps(
                working_nodes, worst_nets, terminal_nets
            )
            if not targeted_swaps:
                _log(f"  Hill-climb pass {pass_num + 1}: no swap candidates, stopping.")
                break

            # Deep copy again – never mutate working_nodes on a rejected path
            trial_nodes   = _apply_cmds_to_nodes(working_nodes, targeted_swaps)
            tool_resolve_overlaps(trial_nodes)
            trial_routing = score_routing(trial_nodes, edges, terminal_nets)
            trial_cost    = trial_routing["placement_cost"]

            if trial_cost < initial_cost:
                _log(
                    f"  Hill-climb pass {pass_num + 1}: "
                    f"cost improved {initial_cost:.4f} → {trial_cost:.4f}. "
                    f"Accepting {len(targeted_swaps)} swap(s)."
                )
                working_nodes    = trial_nodes
                routing_result   = trial_routing
                
                accum_dict = {_cmd_key(c): c for c in accumulated_cmds}
                for tc in targeted_swaps:
                    accum_dict[_cmd_key(tc)] = tc
                accumulated_cmds = list(accum_dict.values())
                
                initial_cost     = trial_cost
            else:
                _log(
                    f"  Hill-climb pass {pass_num + 1}: "
                    f"cost did not improve "
                    f"({trial_cost:.4f} >= {initial_cost:.4f}). Stopping."
                )
                break

        # ──────────────────────────────────────────────────────────────
        # Final Command Compilation
        # ──────────────────────────────────────────────────────────────
        # Strip all intermediate [CMD] blocks from the LLM's raw text
        cleaned_text = re.sub(
            r"\[CMD\].*?\[/CMD\]", "", final_response, flags=re.DOTALL | re.IGNORECASE
        ).strip()
        
        final_cmds = []
        for n in working_nodes:
            if n.get("is_dummy"):
                continue

            # Guard against None / missing geometry values that cause crashes
            try:
                x = round(float(n["geometry"]["x"]), 3)
                y = round(float(n["geometry"]["y"]), 3)
            except (TypeError, KeyError, ValueError) as exc:
                _log(
                    f"  ⚠ Skipping device {n.get('id', '?')} in final CMD: "
                    f"bad geometry ({exc})"
                )
                continue

            final_cmds.append({
                "action": "move",
                "device": n["id"],
                "x":      x,
                "y":      y,
            })

        if not final_cmds:
            _log("  ⚠ CRITICAL: final_cmds is empty! "
                 "working_nodes may be corrupted. Falling back to original nodes.")
            for n in nodes:
                if not n.get("is_dummy"):
                    final_cmds.append({
                        "action": "move",
                        "device": n["id"],
                        "x":      round(float(n["geometry"]["x"]), 3),
                        "y":      round(float(n["geometry"]["y"]), 3),
                    })
        
        final_response = (
            cleaned_text + "\n\n" + _cmds_to_text(final_cmds)
        ).strip()

        _log(f"Pipeline complete. {len(final_cmds)} final CMD block(s)")

        self._emit_stage(3, {
            "routing_result": routing_result,
            "final_cmds": final_cmds,
            "working_nodes": working_nodes,
        })

        try:
            run_label = f"auto_{len(final_cmds)}cmds_drc{len(drc_result.get('violations', []))}"
            save_run_as_example(
                working_nodes, edges, terminal_nets,
                drc_result, routing_result,
                label=run_label,
            )
            _log(f"[RAG] Run saved as example '{run_label}'")
        except Exception as rag_exc:
            _log(f"[RAG] Save failed (non-critical): {rag_exc}")

        # ──────────────────────────────────────────────────────────────
        # Build user-facing summary header
        # ──────────────────────────────────────────────────────────────
        drc_status = "✅ Pass" if drc_result["pass"] else f"⚠ {len(drc_result['violations'])} violation(s) remain"
        summary_header = (
            f"*[Multi-Agent Pipeline Complete]*\n"
            f"• Topology: {len(constraint_text.splitlines())} constraint lines\n"
            f"• DRC: {drc_status}\n"
            f"• Routing score: {routing_result['score']} overlap(s)\n"
            f"• Commands: {len(final_cmds)} command block(s) emitted\n\n"
        )
        return summary_header + final_response
"""
File Description:
This module implements Node 3 of the LangGraph pipeline: Placement Specialist.
It calculates symmetrical matching groups and row assignments, invokes the
Placement Specialist agent (via ReAct + SkillMiddleware) to generate positioning
commands, and expands resulting groups into physical fingers while ensuring
device conservation.

Functions
---------
node_placement_specialist(state)
    Orchestrates placement for the primary pipeline path.
    Uses _compute_matching_and_rows for richer context and full finger-map.

node_placement_specialist_chatbot(state)
    Orchestrates placement for the chat/interactive path.
    Uses aggregate_to_logical_devices for lightweight grouping.

Both nodes share:
- The same module-level agent singleton (_PLACEMENT_SPECIALIST_AGENT)
- The same pre-built system prompt and tool list (_PLACEMENT_SYSTEM_PROMPT,
  _PLACEMENT_TOOLS) so middleware augmentation runs exactly once at import time.

Inputs  (state keys consumed)
------------------------------
nodes, constraint_text, user_message, chat_history, edges, terminal_nets,
strategy_result, selected_model, no_abutment, placement_nodes, pending_cmds

Outputs (state keys produced)
------------------------------
placement_nodes, pending_cmds, original_placement_cmds, chat_history
"""

import copy
import time

from ai_agent.agents.placement_specialist import (
    PLACEMENT_SPECIALIST_PROMPT,
    build_placement_context,
    build_placement_context_chatbot,
    create_placement_specialist_agent,
)
from ai_agent.knowledge.skill_injector import SkillMiddleware
from ai_agent.placement.finger_grouper import aggregate_to_logical_devices, legalize_vertical_rows
from ai_agent.tools.cmd_parser import extract_cmd_blocks, apply_cmds_to_nodes
from ai_agent.tools.overlap_resolver import resolve_overlaps
from ai_agent.nodes._shared import (
    _build_llm_messages,
    _invoke_with_retry,
    _invoke_react_agent_with_retry,
    _extract_agent_output_content,
    _split_content_and_thinking,
    _strip_thinking_text,
    _print_thinking_block,
    _update_and_save_chat_history,
    ip_step,
)
from ai_agent.utils.logging import (
    log_section, log_detail, log_device_positions, stage_start,
)
from ai_agent.tools.inventory import validate_device_count
from ai_agent.placement.symmetry import enforce_reflection_symmetry
from ai_agent.placement.quality_metrics import score_placement

# ── Module-level singletons ─────────────────────────────────────────────────
#
# Middleware augmentation (catalog injection + tool-dict construction) happens
# once at import time.  Both nodes share the same prompt and tool list so
# there is no per-call overhead and no risk of diverging configurations.

_PLACEMENT_SKILL_MIDDLEWARE = SkillMiddleware()

_PLACEMENT_SPECIALIST_AGENT = create_placement_specialist_agent(
    middlewares=[_PLACEMENT_SKILL_MIDDLEWARE]
)
# Build augmented prompt and collect tool dicts from all middlewares.
_PLACEMENT_SYSTEM_PROMPT: str = str(
    _PLACEMENT_SPECIALIST_AGENT.get("system_prompt", PLACEMENT_SPECIALIST_PROMPT)
)
_PLACEMENT_TOOLS: list = []

for _mw in _PLACEMENT_SPECIALIST_AGENT.get("middlewares", []):
    if isinstance(_mw, SkillMiddleware):
        _PLACEMENT_SYSTEM_PROMPT = _mw.augment_system_prompt(_PLACEMENT_SYSTEM_PROMPT)
        _PLACEMENT_TOOLS.extend(_mw.tool_dicts)  # plain dicts - ReAct-compatible

log_detail(
    f"[placement_specialist] SkillMiddleware: "
    f"{len(_PLACEMENT_SKILL_MIDDLEWARE.registry)} skill(s) registered, "
    f"{len(_PLACEMENT_TOOLS)} tool(s) available"
)


# Priority level -> numeric weight (mirrors placement_goals_widget.PRIORITY_WEIGHTS)
_PRIORITY_NUM = {"Low": 1, "Medium": 5, "High": 10}

_ROW_HEIGHT = 0.668   # um per row


def _snap_orphan_dummies(nodes: list) -> list:
    """
    Post-placement safety pass: detects dummy devices that ended up at Y
    coordinates far from the rest of the layout (the "flying transistor" bug
    that occurs when matching=Low removes ABBA blocks that used to anchor them).

    For each orphan dummy:
      - Collect all valid Y values used by active (non-dummy) devices of the
        same PMOS/NMOS type.
      - Snap the dummy's Y to the closest valid Y.
      - Append it at the rightmost X of that row (after the last real device).

    A device is considered a "dummy" if its id starts with D, FILLER_DUMMY_,
    EDGE_DUMMY, or DUMMY_matrix_.
    """
    import statistics

    def _is_dummy(node):
        nid = str(node.get("id", ""))
        return (
            node.get("is_dummy")
            or nid.startswith(("FILLER_DUMMY_", "DUMMY_matrix_", "EDGE_DUMMY"))
            or (len(nid) >= 2 and nid[0] == "D" and nid[1:].isdigit())
        )

    def _dev_type(node):
        t = str(node.get("type", "")).lower()
        return "pmos" if "pmos" in t or "p_mos" in t else "nmos"

    # Gather valid Y values per type from non-dummy devices
    active_y: dict[str, list[float]] = {"pmos": [], "nmos": []}
    active_x_by_y: dict[tuple, list[float]] = {}   # (type, y_rounded) -> [x values]

    for n in nodes:
        if _is_dummy(n):
            continue
        g = n.get("geometry", {})
        x = g.get("x", 0.0)
        y = g.get("y", 0.0)
        dt = _dev_type(n)
        active_y[dt].append(y)
        key = (dt, round(y, 3))
        active_x_by_y.setdefault(key, []).append(x)

    if not any(active_y.values()):
        return nodes   # nothing to snap to

    # Compute median Y per type and the set of "valid" row Ys
    valid_ys: dict[str, list[float]] = {}
    for dt, ys in active_y.items():
        if ys:
            # Cluster: round to nearest _ROW_HEIGHT grid
            rounded = sorted({round(y / _ROW_HEIGHT) * _ROW_HEIGHT for y in ys})
            valid_ys[dt] = rounded

    # Identify orphan threshold: a dummy is orphan if its Y is > 1.5 row-heights
    # away from ALL valid rows of its type
    result = []
    for n in nodes:
        if not _is_dummy(n):
            result.append(n)
            continue

        g = n.get("geometry", {})
        dx = g.get("x", 0.0)
        dy = g.get("y", 0.0)
        w  = g.get("width", 0.07)
        dt = _dev_type(n)

        rows = valid_ys.get(dt, [])
        if not rows:
            result.append(n)
            continue

        # Find nearest valid row Y
        nearest_y = min(rows, key=lambda ry: abs(ry - dy))
        gap = abs(nearest_y - dy)

        if gap > _ROW_HEIGHT * 1.5:
            # Orphan detected - snap
            key = (dt, round(nearest_y, 3))
            taken_xs = active_x_by_y.get(key, [])
            new_x = (max(taken_xs) + w + 0.01) if taken_xs else 0.0
            # Update node in place (shallow copy geometry)
            n = dict(n)
            n["geometry"] = dict(g)
            n["geometry"]["x"] = round(new_x, 4)
            n["geometry"]["y"] = round(nearest_y, 4)
            # Register in active_x_by_y so multiple orphans don't overlap
            active_x_by_y.setdefault(key, []).append(new_x)
            log_detail(
                f"[snap_orphan] {n['id']}: y={dy:.3f} -> {nearest_y:.3f} (gap={gap:.3f}um)"
            )

        result.append(n)
    return result


def _goals_to_prompt(goals: dict) -> str:
    """
    Convert a placement_goals dict (from the UI) into a plain-English
    priority block prepended to the LLM context.

    Crucially, this section OVERRIDES conflicting rules in the system prompt
    so all pipeline stages (deterministic + LLM) stay in sync.

    Returns an empty string when no goals are set (panel was closed).
    """
    if not goals:
        return ""

    area_p   = goals.get("area_priority",    "Medium")
    match_p  = goals.get("matching_priority", "Medium")
    sym_p    = goals.get("symmetry_priority", "High")
    max_area = goals.get("max_area_um2")

    # Area instructions
    _AREA_INSTR = {
        "Low":    "Area is NOT a priority - rows may grow; focus on matching quality.",
        "Medium": "Aim for a compact layout; avoid unnecessary empty space.",
        "High":   "MINIMISE area: pack devices into the FEWEST possible rows. "
                  "Each row should be as full as DRC rules allow before starting a new row.",
    }
    # Matching instructions (also overrides system-prompt ABBA rules)
    _MATCH_INSTR = {
        "Low":    "Apply ABBA interdigitation for differential pairs and current mirrors only. "
                  "Cross-coupled and load pairs are placed individually without interdigitation.",
        "Medium": "Apply ABBA for differential pairs, current mirrors, cross-coupled pairs, "
                  "and load pairs (all standard matching tiers).",
        "High":   "MANDATORY: apply ABBA or common-centroid for EVERY transistor pair the "
                  "engine can match - including any additional pairs not in the standard tiers. "
                  "No matchable pair should be left uninterdigitated.",
    }
    # Symmetry instructions
    _SYM_INSTR = {
        "Low":    "Global mirror symmetry is DISABLED for this run. "
                  "Do NOT enforce a shared vertical axis for left/right halves. "
                  "This OVERRIDES the TWO-HALF symmetry rules in the system prompt. "
                  "Place devices for best area packing without mirroring constraints.",
        "Medium": "Apply reflection symmetry for matched pairs where it does not cost area.",
        "High":   "MANDATORY: every matched group must be placed mirror-symmetrically "
                  "about the layout centre line. Sacrificing area for symmetry is acceptable.",
    }

    lines = [
        "=" * 62,
        "  PLACEMENT GOALS  (user-set - these OVERRIDE all system-prompt rules below)",
        "=" * 62,
        f"  Area priority     : {area_p:6s}  -> {_AREA_INSTR[area_p]}",
        f"  Matching priority : {match_p:6s}  -> {_MATCH_INSTR[match_p]}",
        f"  Symmetry priority : {sym_p:6s}  -> {_SYM_INSTR[sym_p]}",
    ]
    if max_area is not None:
        lines.append(
            f"  Max area          : {max_area} um2  "
            f"-> The total bounding-box area MUST NOT exceed this value."
        )
    lines += ["=" * 62, ""]
    return "\n".join(lines) + "\n"


# ── Shared helper ───────────────────────────────────────────────────────────

def _sync_group_geometry_from_members(group_nodes, finger_map):
    """Align each logical group's geometry to the current finger placements.

    Sets group x to the minimum finger x, group y to the modal finger y,
    and copies orientation from the first finger that declares one.
    No-ops silently when group_nodes or finger_map are empty.
    """
    if not group_nodes or not finger_map:
        return
    from collections import Counter

    for group in group_nodes:
        gid = group.get("id", "")
        members = finger_map.get(gid, [])
        if not members:
            continue

        xs, ys = [], []
        orientation = None
        for member in members:
            geo = member.get("geometry", {})
            if not isinstance(geo, dict):
                continue
            try:
                xs.append(float(geo.get("x", 0.0)))
            except (TypeError, ValueError):
                pass
            try:
                ys.append(float(geo.get("y", 0.0)))
            except (TypeError, ValueError):
                pass
            if orientation is None:
                orientation = geo.get("orientation")

        if not xs and not ys and orientation is None:
            continue

        group_geo = group.setdefault("geometry", {})
        if xs:
            group_geo["x"] = round(min(xs), 6)
        if ys:
            group_geo["y"] = Counter([round(v, 6) for v in ys]).most_common(1)[0][0]
        if orientation:
            group_geo["orientation"] = orientation

def node_placement_specialist(state):
    """Primary placement node.

    Uses ``_compute_matching_and_rows`` from the placement agent module to
    build a richer context that includes pre-computed row assignments and
    matching constraint strings.  The resulting ``finger_map`` and ``merged``
    block dict drive finger expansion and conservation checks.
    """
    t0 = time.time()
    stage_start(3, "Placement Specialist")

    nodes           = state.get("nodes", [])
    constraint_text = state.get("constraint_text", "")
    user_message    = state.get("user_message", "Optimize placement.")
    chat_history    = state.get("chat_history", [])
    edges           = state.get("edges", [])
    terminal_nets   = state.get("terminal_nets", {})
    strategy_result = state.get("strategy_result", "auto")
    selected_model  = state.get("selected_model", "Gemini")
    no_abutment_flag = state.get("no_abutment", False)

    working_nodes = state.get("placement_nodes", []) or copy.deepcopy(nodes)

    n_pmos = sum(1 for n in nodes if n.get("type") == "pmos")
    n_nmos = sum(1 for n in nodes if n.get("type") == "nmos")
    log_detail(f"Input: {len(nodes)} devices ({n_pmos} PMOS + {n_nmos} NMOS)")
    log_detail(f"Edges: {len(edges)} | Terminal nets: {len(terminal_nets)}")
    log_detail(f"Strategy: {strategy_result}")

    # ── Step 3a: Build context (matching + row assignment) ───────────────────
    log_section("Step 3a: Computing matching groups & row assignments")
    # placement_goals is None when the panel was collapsed -> original defaults
    raw_goals = state.get("placement_goals")   # None = panel not opened
    goals_for_context = raw_goals or {}        # {} -> all defaults in helpers
    goals_active = raw_goals is not None       # True only when panel was used

    if goals_active:
        match_priority = goals_for_context.get("matching_priority", "High")
        area_priority = goals_for_context.get("area_priority", "Medium")
        log_detail("Goals panel was OPEN - applying user priorities")
        log_detail(
            f"  Matching={match_priority}  Area={area_priority}  "
            f"Symmetry={goals_for_context.get('symmetry_priority','Medium')}"
        )
    else:
        match_priority = "High"
        area_priority = "Medium"
        log_detail("Goals panel was CLOSED - running with original pipeline defaults")

    context_text = build_placement_context(
        nodes,
        constraint_text,
        terminal_nets=terminal_nets,
        edges=edges,
        no_abutment=no_abutment_flag,
        placement_goals=goals_for_context if goals_active else None,
    )

    grp_nodes  = copy.deepcopy(nodes)
    finger_map = {}
    merged     = {}
    try:
        from ai_agent.agents.placement_specialist import _compute_matching_and_rows
        grp_nodes, finger_map, row_str, match_str, _, merged = _compute_matching_and_rows(
            nodes, edges, terminal_nets,
            no_abutment=no_abutment_flag,
            matching_priority=match_priority,
            area_priority=area_priority,
        )
        log_detail(
            f"Finger grouping: {len(nodes)} fingers -> {len(grp_nodes)} logical groups"
        )
        log_detail(
            f"Matching priority={match_priority}  area_priority={area_priority}"
        )
        log_detail(
            f"Matched blocks: {len(merged)} "
            f"({', '.join(merged.keys()) if merged else 'none'})"
        )
        if row_str:
            log_section("Pre-computed Row Assignments")
            for line in row_str.strip().split("\n"):
                log_detail(line.strip())
        if match_str:
            log_section("Matching Constraints")
            for line in match_str.strip().split("\n")[:20]:
                log_detail(line.strip())
    except Exception as exc:
        log_detail(f"WARNING: matching/row computation failed: {exc}")

    # ── Step 3b: Call LLM for placement commands ───────────────────────────
    log_section("Step 3b: Calling LLM for placement commands")

    goals = state.get("placement_goals") or {}
    goals_paragraph = _goals_to_prompt(goals)
    if goals_paragraph:
        log_detail(f"Goals injected: area={goals.get('area_priority','Medium')} "
                   f"matching={goals.get('matching_priority','Medium')} "
                   f"symmetry={goals.get('symmetry_priority','Medium')} "
                   f"max_area={goals.get('max_area_um2')}")

    placer_user = (
        f"{goals_paragraph}"
        f"User request: {user_message}\n\n"
        f"Selected Strategy: {strategy_result}\n\n"
        f"{context_text}"
    )

    chat_history = _update_and_save_chat_history(
        chat_history=chat_history,
        user_content="",
        node_role="System",
        node_content="Starting **Placement Specialist**...",
    )

    # _PLACEMENT_SYSTEM_PROMPT and _PLACEMENT_TOOLS are pre-built at import time.
    log_detail(f"Prompt size: {len(_PLACEMENT_SYSTEM_PROMPT)} chars (augmented)")
    log_detail(f"Tools available: {[t['name'] for t in _PLACEMENT_TOOLS]}")

    placement_text = ""
    stage2_cmds    = []
    try:
        llm_t0 = time.time()
        placement_msgs = _build_llm_messages(
            _PLACEMENT_SYSTEM_PROMPT,
            chat_history,
            placer_user,
        )
        placement_result = _invoke_with_retry(
            placement_msgs,
            selected_model,
            "heavy",
            "PLACEMENT",
        )
        llm_elapsed = time.time() - llm_t0

        placement_text, placement_thinking = _split_content_and_thinking(
            placement_result.content
        )
        placement_text = _strip_thinking_text(placement_text)
        stage2_cmds    = extract_cmd_blocks(placement_text)

        log_detail(f"LLM responded in {llm_elapsed:.1f}s")
        log_detail(f"LLM produced {len(stage2_cmds)} CMD block(s)")
        _print_thinking_block("PLACEMENT", placement_thinking)
    except Exception as exc:
        log_detail(f"ERROR: LLM failed: {exc}")
        placement_text = "[PLACEMENT] LLM failed."

    # ── Step 3c: Apply commands ──────────────────────────────────────────────
    log_section("Step 3c: Applying placement commands")
    if stage2_cmds:
        for i, cmd in enumerate(stage2_cmds):
            dev = cmd.get("device", cmd.get("device_id", cmd.get("id", "?")))
            log_detail(
                f"CMD[{i+1}]: {cmd.get('action', '?')} {dev} "
                f"→ x={cmd.get('x', '?')}, y={cmd.get('y', '?')}"
            )
    else:
        log_detail("No commands from LLM — using pre-computed positions")

    updated_chat_history = _update_and_save_chat_history(
        chat_history=chat_history,
        user_content=user_message,
        node_role="Placement Specialist Assistant",
        node_content=placement_text,
    )

    working_nodes = apply_cmds_to_nodes(grp_nodes, stage2_cmds)
    working_nodes = enforce_reflection_symmetry(working_nodes)

    # ── Step 3d: Expand to physical fingers ──────────────────────────────────
    log_section("Step 3d: Expanding to physical fingers")
    if finger_map:
        from ai_agent.placement.finger_grouper import expand_to_fingers
        orig_lookup = {n["id"]: n for n in grp_nodes}
        log_detail(
            f"Expanding {len(working_nodes)} groups via finger_map "
            f"({len(finger_map)} entries)"
        )
        working_nodes = expand_to_fingers(
            working_nodes, finger_map,
            no_abutment=no_abutment_flag,
            original_group_nodes=orig_lookup,
        )
        log_detail(f"Expanded to {len(working_nodes)} physical devices")
    else:
        from ai_agent.placement.finger_grouper import expand_logical_to_fingers
        working_nodes = expand_logical_to_fingers(working_nodes, nodes)
        log_detail(f"Legacy expansion → {len(working_nodes)} devices")

    # ── Step 3e: Post-expansion overlap resolution ───────────────────────────
    log_section("Step 3e: Post-expansion overlap resolution")
    moved_ids = resolve_overlaps(working_nodes)
    log_detail(
        f"Fixed overlaps for {len(moved_ids)} device(s)" if moved_ids
        else "No overlaps detected after expansion"
    )
    working_nodes = legalize_vertical_rows(working_nodes)

    # ── Snap orphan dummies (flying-transistor fix) ─────────────────────
    # When matching=Low (skip_matching), dummy devices (D-prefixed) that were
    # previously anchored inside ABBA blocks may end up at isolated Y coords.
    # Move any such device to the nearest valid active-device row.
    working_nodes = _snap_orphan_dummies(working_nodes)

    # ── Step 3f: Validate device conservation ────────────────────────────
    log_section("Step 3f: Device conservation check")
    conservation = validate_device_count(nodes, working_nodes)
    if not conservation["pass"]:
        log_detail(f"CONSERVATION FAILURE: missing={conservation.get('missing', [])}")
        log_detail("Falling back to original positions")
        working_nodes = copy.deepcopy(nodes)
        stage2_cmds   = []
    else:
        log_detail(f"Conservation OK: all {conservation['original_count']} devices present")

    log_device_positions(working_nodes, "Final Placement Positions")

    # ── Step 3g: Quality benchmark ───────────────────────────────────────
    log_section("Step 3g: Placement Quality Benchmark")
    try:
        quality_report = score_placement(
            working_nodes,
            matching_info=merged if merged else None,
            finger_map=finger_map if finger_map else None,
            verbose=True,
        )
        log_detail(quality_report["summary"])
        if "details" in quality_report:
            for metric, detail_text in quality_report["details"].items():
                if detail_text:
                    log_detail(f"[{metric}]\n{detail_text}")
        composite = quality_report["composite_score"]

        def _fmt(v):
            return f"{v:.1%}" if v is not None else "N/A"

        log_detail(
            f"Quality: Y={_fmt(quality_report.get('layout_y_score'))}  "
            f"X={_fmt(quality_report.get('matching_x_score'))}  "
            f"Interdig={_fmt(quality_report.get('interdigitation_score'))}  "
            f"Centroid={_fmt(quality_report.get('centroid_score'))}  "
            f"DRC={_fmt(quality_report.get('drc_score'))}  "
            f"-> COMPOSITE={composite:.1%}"
        )
    except Exception as _q_exc:
        log_detail(f"WARNING: quality benchmark failed: {_q_exc}")
        quality_report = {}
        composite = 0.0

    elapsed = time.time() - t0
    cons = "ok" if conservation["pass"] else "FAILED"
    q_str = f", quality={composite:.1%}" if quality_report else ""
    ip_step(
        "3/5 Placement Specialist",
        f"{len(stage2_cmds)} cmd(s), {elapsed:.1f}s, conservation={cons}{q_str}",
    )

    return {
        "placement_nodes":         working_nodes,
        "pending_cmds":            state.get("pending_cmds", []) + stage2_cmds,
        "original_placement_cmds": state.get("pending_cmds", []) + stage2_cmds,
        "chat_history":            updated_chat_history,
        "placement_quality":       quality_report,
    }


# ── Node: chatbot / interactive path ────────────────────────────────────────

def node_placement_specialist_chatbot(state):
    """Chat-mode placement node.

    Uses ``aggregate_to_logical_devices`` for lightweight grouping instead of
    the heavier ``_compute_matching_and_rows`` path.  Otherwise shares the
    same ReAct + SkillMiddleware flow as the primary node.
    """
    t0 = time.time()
    stage_start(3, "Placement Specialist (Chat)")

    nodes            = state.get("nodes", [])
    constraint_text  = state.get("constraint_text", "")
    user_message     = state.get("user_message", "Optimize placement.")
    chat_history     = state.get("chat_history", [])
    edges            = state.get("edges", [])
    terminal_nets    = state.get("terminal_nets", {})
    strategy_result  = state.get("strategy_result", "auto")
    selected_model   = state.get("selected_model", "Gemini")
    no_abutment_flag = state.get("no_abutment", False)

    working_nodes = state.get("placement_nodes", []) or copy.deepcopy(nodes)

    n_pmos = sum(1 for n in nodes if n.get("type") == "pmos")
    n_nmos = sum(1 for n in nodes if n.get("type") == "nmos")
    log_detail(f"Input: {len(nodes)} devices ({n_pmos} PMOS + {n_nmos} NMOS)")
    log_detail(f"Edges: {len(edges)} | Terminal nets: {len(terminal_nets)}")
    log_detail(f"Strategy: {strategy_result}")

    # ── Step 3a: Build placement context ─────────────────────────────────────
    log_section("Step 3a: Building placement context (chat mode)")
    context_text = build_placement_context_chatbot(
        nodes, constraint_text,
        terminal_nets=terminal_nets, edges=edges, no_abutment=no_abutment_flag,
    )

    # Lightweight finger grouping for chat path.
    grp_nodes  = copy.deepcopy(nodes)
    finger_map = {}
    try:
        grouped = aggregate_to_logical_devices(nodes, edges or [])
        if isinstance(grouped, tuple):
            grp_nodes, _, finger_map = grouped
        else:
            grp_nodes = grouped
        _sync_group_geometry_from_members(grp_nodes, finger_map)
        log_detail(
            f"Finger grouping: {len(nodes)} fingers -> {len(grp_nodes)} logical groups"
        )
    except Exception as exc:
        log_detail(f"WARNING: grouping failed: {exc}")

    # ── Step 3b: Call LLM via ReAct + SkillMiddleware ───────────────────────
    log_section("Step 3b: Calling LLM (ReAct + SkillMiddleware)")
    placer_user = (
        f"User request: {user_message}\n\n"
        f"Selected Strategy: {strategy_result}\n\n"
        f"{context_text}"
    )

    chat_history = _update_and_save_chat_history(
        chat_history=chat_history,
        user_content="",
        node_role="System",
        node_content="Starting **Placement Specialist**...",
    )

    log_detail(f"Prompt size: {len(_PLACEMENT_SYSTEM_PROMPT)} chars (augmented)")
    log_detail(f"Tools available: {[t['name'] for t in _PLACEMENT_TOOLS]}")

    placement_text = ""
    stage2_cmds    = []
    try:
        llm_t0 = time.time()
        placement_result = _invoke_react_agent_with_retry(
            system_prompt=_PLACEMENT_SYSTEM_PROMPT,
            chat_history=chat_history,
            user_prompt=placer_user,
            selected_model=selected_model,
            task_weight="heavy",
            stage_tag="PLACEMENT",
            tools=_PLACEMENT_TOOLS,
        )
        placement_content = _extract_agent_output_content(placement_result)
        llm_elapsed = time.time() - llm_t0

        placement_text, placement_thinking = _split_content_and_thinking(placement_content)
        placement_text = _strip_thinking_text(placement_text)
        stage2_cmds    = extract_cmd_blocks(placement_text)

        log_detail(f"LLM responded in {llm_elapsed:.1f}s")
        log_detail(f"LLM produced {len(stage2_cmds)} CMD block(s)")
        _print_thinking_block("PLACEMENT", placement_thinking)
    except Exception as exc:
        log_detail(f"ERROR: LLM failed: {exc}")
        placement_text = "[PLACEMENT] LLM failed."

    # ── Step 3c: Apply commands ──────────────────────────────────────────────
    log_section("Step 3c: Applying placement commands")
    if stage2_cmds:
        for i, cmd in enumerate(stage2_cmds):
            dev = cmd.get("device", cmd.get("device_id", cmd.get("id", "?")))
            log_detail(
                f"CMD[{i+1}]: {cmd.get('action', '?')} {dev} "
                f"-> x={cmd.get('x', '?')}, y={cmd.get('y', '?')}"
            )
    else:
        log_detail("No commands from LLM - using current positions")

    updated_chat_history = _update_and_save_chat_history(
        chat_history=chat_history,
        user_content=user_message,
        node_role="Placement Specialist Assistant",
        node_content=placement_text,
    )

    working_nodes = apply_cmds_to_nodes(grp_nodes, stage2_cmds)
    working_nodes = enforce_reflection_symmetry(working_nodes)

    # ── Step 3d: Expand to physical fingers ──────────────────────────────────
    log_section("Step 3d: Expanding to physical fingers")
    if finger_map:
        from ai_agent.placement.finger_grouper import expand_to_fingers
        orig_lookup = {n["id"]: n for n in grp_nodes}
        log_detail(
            f"Expanding {len(working_nodes)} groups via finger_map "
            f"({len(finger_map)} entries)"
        )
        working_nodes = expand_to_fingers(
            working_nodes, finger_map,
            no_abutment=no_abutment_flag,
            original_group_nodes=orig_lookup,
        )
        log_detail(f"Expanded to {len(working_nodes)} physical devices")
    else:
        from ai_agent.placement.finger_grouper import expand_logical_to_fingers
        working_nodes = expand_logical_to_fingers(working_nodes, nodes)
        log_detail(f"Legacy expansion -> {len(working_nodes)} devices")

    # ── Step 3e: Post-expansion overlap resolution ───────────────────────────
    log_section("Step 3e: Post-expansion overlap resolution")
    moved_ids = resolve_overlaps(working_nodes)
    log_detail(
        f"Fixed overlaps for {len(moved_ids)} device(s)" if moved_ids
        else "No overlaps detected after expansion"
    )
    working_nodes = legalize_vertical_rows(working_nodes)

    # ── Step 3f: Device conservation check ──────────────────────────────────
    log_section("Step 3f: Device conservation check")
    conservation = validate_device_count(nodes, working_nodes)
    if not conservation["pass"]:
        log_detail(f"CONSERVATION FAILURE: missing={conservation.get('missing', [])}")
        log_detail("Falling back to original positions")
        working_nodes = copy.deepcopy(nodes)
        stage2_cmds   = []
    else:
        log_detail(f"Conservation OK: all {conservation['original_count']} devices present")

    log_device_positions(working_nodes, "Final Placement Positions")

    elapsed = time.time() - t0
    cons = "ok" if conservation["pass"] else "FAILED"
    ip_step(
        "3/5 Placement Specialist",
        f"{len(stage2_cmds)} cmd(s), {elapsed:.1f}s, conservation={cons}",
    )

    return {
        "placement_nodes":         working_nodes,
        "pending_cmds":            state.get("pending_cmds", []) + stage2_cmds,
        "original_placement_cmds": state.get("pending_cmds", []) + stage2_cmds,
        "chat_history":            updated_chat_history,
    }
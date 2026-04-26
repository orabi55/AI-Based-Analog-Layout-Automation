"""
File Description:
This module implements Node 3 of the LangGraph pipeline: Placement Specialist. It calculates symmetrical matching groups and row assignments, invokes the Placement Specialist agent to generate positioning commands, and expands resulting groups into physical fingers while ensuring device conservation.

Functions:
- node_placement_specialist:
    - Role: Orchestrates the placement generation phase, handling context building, LLM invocation, command application, and finger expansion.
    - Inputs: 
        - state (dict): The current LangGraph state.
    - Outputs: (dict) A state update containing 'placement_nodes', accumulated 'pending_cmds', and updated 'chat_history'.
"""

import copy
import time
from ai_agent.agents.placement_specialist import (
    PLACEMENT_SPECIALIST_PROMPT,
    build_placement_context,
    create_placement_specialist_agent,
)
from ai_agent.knowledge.skill_injector import SkillMiddleware
from ai_agent.placement.finger_grouper import aggregate_to_logical_devices
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
    vprint,
    ip_step,
    steps_only,
)
from ai_agent.utils.logging import (
    log_section, log_detail, log_device_positions, stage_start, stage_end,
)
from ai_agent.tools.inventory import validate_device_count
from ai_agent.placement.symmetry import enforce_reflection_symmetry

_PLACEMENT_SKILL_MIDDLEWARE = SkillMiddleware()
_PLACEMENT_SPECIALIST_AGENT = create_placement_specialist_agent(
    middlewares=[_PLACEMENT_SKILL_MIDDLEWARE]
)


def node_placement_specialist(state):
    t0 = time.time()
    stage_start(3, "Placement Specialist")

    nodes = state.get("nodes", [])
    constraint_text = state.get("constraint_text", "")
    user_message = state.get("user_message", "Optimize placement.")
    chat_history = state.get("chat_history", [])
    edges = state.get("edges", [])
    terminal_nets = state.get("terminal_nets", {})
    strategy_result = state.get("strategy_result", "auto")
    pending_cmds = state.get("pending_cmds", [])
    selected_model = state.get("selected_model", "Gemini")

    working_nodes = state.get("placement_nodes", [])
    if not working_nodes:
        working_nodes = copy.deepcopy(nodes)

    # Count device types
    n_pmos = sum(1 for n in nodes if n.get("type") == "pmos")
    n_nmos = sum(1 for n in nodes if n.get("type") == "nmos")
    log_detail(f"Input: {len(nodes)} devices ({n_pmos} PMOS + {n_nmos} NMOS)")
    log_detail(f"Edges: {len(edges)} | Terminal nets: {len(terminal_nets)}")
    log_detail(f"Strategy: {strategy_result}")

    # Handle human-in-the-loop manual edits
    if pending_cmds:
        log_detail(f"Human loopback! Applying {len(pending_cmds)} manual edits.")
        updated_nodes = apply_cmds_to_nodes(working_nodes, pending_cmds)
        elapsed = time.time() - t0
        ip_step("3/5 Placement specialist", f"loopback: applied {len(pending_cmds)} manual edit(s) ({elapsed:.1f}s)")
        return {
            "placement_nodes": updated_nodes,
            "pending_cmds": [],
            "drc_retry_count": 0,
            "routing_pass_count": 0,
        }

    # ── Step 3a: Build context (matching + row assignment) ───────────────
    log_section("Step 3a: Computing matching groups & row assignments")
    no_abutment_flag = state.get("no_abutment", False)
    context_text = build_placement_context(
        nodes, constraint_text, terminal_nets=terminal_nets, edges=edges, no_abutment=no_abutment_flag
    )
    grp_nodes = copy.deepcopy(nodes)
    finger_map = {}
    merged = {}
    try:
        from ai_agent.agents.placement_specialist import _compute_matching_and_rows
        grp_nodes, finger_map, row_str, match_str, _, merged = _compute_matching_and_rows(
            nodes, edges, terminal_nets, no_abutment=no_abutment_flag
        )
        log_detail(f"Finger grouping: {len(nodes)} fingers → {len(grp_nodes)} logical groups")
        log_detail(f"Matched blocks: {len(merged)} ({', '.join(merged.keys()) if merged else 'none'})")
        if row_str:
            log_section("Pre-computed Row Assignments")
            for line in row_str.strip().split("\n"):
                log_detail(line.strip())
        if match_str:
            log_section("Matching Constraints")
            for line in match_str.strip().split("\n")[:20]:
                log_detail(line.strip())
    except Exception as _log_exc:
        log_detail(f"WARNING: matching/row computation failed: {_log_exc}")

    # ── Step 3b: LLM placement call ─────────────────────────────────────
    log_section("Step 3b: Calling LLM for placement commands")
    placer_user = (
        f"User request: {user_message}\n\n"
        f"Selected Strategy: {strategy_result}\n\n"
        f"{context_text}"
    )

    placement_agent = _PLACEMENT_SPECIALIST_AGENT
    placement_framework = str(placement_agent.get("framework", "plain")).strip().lower()
    placement_system_prompt = str(placement_agent.get("system_prompt", PLACEMENT_SPECIALIST_PROMPT))
    placement_tools = []
    for middleware in placement_agent.get("middlewares", []):
        if isinstance(middleware, SkillMiddleware):
            placement_system_prompt = middleware.augment_system_prompt(placement_system_prompt)
            placement_tools.extend(middleware.tools)

    chat_history = _update_and_save_chat_history(
        chat_history=chat_history, user_content="",
        node_role="System", node_content="Starting **Placement Specialist**..."
    )
    placer_msgs = _build_llm_messages(placement_system_prompt, chat_history, placer_user)
    log_detail(f"Framework: {placement_framework}")
    log_detail(f"Prompt size: {sum(len(m.get('content', '')) for m in placer_msgs)} chars")

    placement_text = ""
    stage2_cmds = []
    try:
        llm_t0 = time.time()
        if placement_framework == "react":
            placement_result = _invoke_react_agent_with_retry(
                system_prompt=placement_system_prompt, chat_history=chat_history,
                user_prompt=placer_user, selected_model=selected_model,
                task_weight="heavy", stage_tag="PLACEMENT", tools=placement_tools,
            )
            placement_content = _extract_agent_output_content(placement_result)
        else:
            placement_raw = _invoke_with_retry(placer_msgs, selected_model, "heavy", "PLACEMENT")
            placement_content = placement_raw.content
        llm_elapsed = time.time() - llm_t0
        placement_text, placement_thinking = _split_content_and_thinking(placement_content)
        placement_text = _strip_thinking_text(placement_text)
        stage2_cmds = extract_cmd_blocks(placement_text)
        log_detail(f"LLM responded in {llm_elapsed:.1f}s")
        log_detail(f"LLM produced {len(stage2_cmds)} CMD block(s)")
        _print_thinking_block("PLACEMENT", placement_thinking)
    except Exception as exc:
        log_detail(f"ERROR: LLM failed: {exc}")
        placement_text = "[PLACEMENT] LLM failed."

    # ── Step 3c: Apply commands ──────────────────────────────────────────
    log_section("Step 3c: Applying placement commands")
    if stage2_cmds:
        for i, cmd in enumerate(stage2_cmds):
            action = cmd.get("action", "?")
            dev = cmd.get("device", cmd.get("device_id", cmd.get("id", "?")))
            x = cmd.get("x", "?")
            y = cmd.get("y", "?")
            log_detail(f"CMD[{i+1}]: {action} {dev} → x={x}, y={y}")
    else:
        log_detail("No commands from LLM — using pre-computed positions")

    updated_chat_history = _update_and_save_chat_history(
        chat_history=chat_history, user_content=user_message,
        node_role="Placement Specialist Assistant", node_content=placement_text,
    )

    working_nodes = apply_cmds_to_nodes(grp_nodes, stage2_cmds)
    working_nodes = enforce_reflection_symmetry(working_nodes)

    # ── Step 3d: Expand to physical fingers ──────────────────────────────
    log_section("Step 3d: Expanding to physical fingers")
    if finger_map:
        from ai_agent.placement.finger_grouper import expand_to_fingers
        no_abutment_flag = state.get("no_abutment", False)
        orig_lookup = {n["id"]: n for n in grp_nodes}
        log_detail(f"Expanding {len(working_nodes)} groups via finger_map ({len(finger_map)} entries)")
        working_nodes = expand_to_fingers(
            working_nodes, finger_map, no_abutment=no_abutment_flag,
            original_group_nodes=orig_lookup,
        )
        log_detail(f"Expanded to {len(working_nodes)} physical devices")
    else:
        from ai_agent.placement.finger_grouper import expand_logical_to_fingers
        working_nodes = expand_logical_to_fingers(working_nodes, nodes)
        log_detail(f"Legacy expansion → {len(working_nodes)} devices")

    # ── Step 3e: Post-expansion overlap resolution ───────────────────────
    log_section("Step 3e: Post-expansion overlap resolution")
    moved_ids = resolve_overlaps(working_nodes)
    if moved_ids:
        log_detail(f"Fixed overlaps for {len(moved_ids)} device(s)")
    else:
        log_detail("No overlaps detected after expansion")

    # ── Step 3f: Validate device conservation ────────────────────────────
    log_section("Step 3f: Device conservation check")
    conservation = validate_device_count(nodes, working_nodes)
    if not conservation["pass"]:
        log_detail(f"CONSERVATION FAILURE: missing={conservation.get('missing', [])}")
        log_detail(f"Falling back to original positions")
        working_nodes = copy.deepcopy(nodes)
        stage2_cmds = []
    else:
        log_detail(f"Conservation OK: all {conservation['original_count']} devices present")

    # ── Final position summary ───────────────────────────────────────────
    log_device_positions(working_nodes, "Final Placement Positions")

    elapsed = time.time() - t0
    cons = 'ok' if conservation['pass'] else 'FAILED'
    ip_step("3/5 Placement Specialist", f"{len(stage2_cmds)} cmd(s), {elapsed:.1f}s, conservation={cons}")

    return {
        "placement_nodes": working_nodes,
        "pending_cmds": state.get("pending_cmds", []) + stage2_cmds,
        "original_placement_cmds": state.get("pending_cmds", []) + stage2_cmds,
        "chat_history": updated_chat_history,
    }

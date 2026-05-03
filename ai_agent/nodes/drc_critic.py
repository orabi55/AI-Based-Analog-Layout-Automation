"""
File Description:
This module implements Node 5 of the LangGraph pipeline: DRC Critic. It validates the final placement geometry against design rules (spacing, overlap), and uses both LLM reasoning and prescriptive mechanical engines to apply corrective layout fixes across multiple attempts.

Functions:
- node_drc_critic:
    - Role: Orchestrates the DRC validation and fix-it loop, merging AI-generated fixes with deterministic prescriptive adjustments to reach a clean layout.
    - Inputs: 
        - state (dict): The current LangGraph state.
    - Outputs: (dict) A state update containing 'placement_nodes', updated 'pending_cmds', and 'drc_pass' status.
"""

import json
import time
from ai_agent.placement.finger_grouper import aggregate_to_logical_devices, legalize_vertical_rows
from ai_agent.agents.placement_specialist import build_placement_context
from ai_agent.agents.drc_critic import (
    DRC_CRITIC_PROMPT, run_drc_check, format_drc_violations_for_llm, compute_prescriptive_fixes,
)
from ai_agent.tools.overlap_resolver import resolve_overlaps
from ai_agent.tools.cmd_parser import extract_cmd_blocks, apply_cmds_to_nodes
from ai_agent.placement.symmetry import enforce_reflection_symmetry
from ai_agent.nodes.symmetry_enforcer import parse_symmetry_block
from ai_agent.nodes._shared import (
    _build_llm_messages,
    _invoke_with_retry,
    _split_content_and_thinking,
    _strip_thinking_text,
    _print_thinking_block,
    _update_and_save_chat_history,
    vprint,
    ip_step,
    steps_only,
)
from ai_agent.utils.logging import (
    log_section, log_detail, log_device_positions, stage_start,
)


def _is_dummy_node(node: dict) -> bool:
    node_id = str(node.get("id", ""))
    return bool(
        node.get("is_dummy")
        or node_id.startswith(("FILLER_DUMMY_", "DUMMY_matrix_", "EDGE_DUMMY"))
    )


def node_drc_critic(state):
    t0 = time.time()
    retry_num = state.get("drc_retry_count", 0)
    stage_start(5, f"DRC Critic (attempt {retry_num + 1})")

    chat_history = state.get("chat_history", [])
    chat_history = _update_and_save_chat_history(
        chat_history=chat_history, user_content="",
        node_role="System",
        node_content=f"Starting **DRC Critic (Attempt {retry_num + 1})**...",
    )

    nodes = state.get("placement_nodes", [])
    pending_cmds = state.get("pending_cmds", [])
    gap_px = state.get("gap_px", 0.0)
    terminal_nets = state.get("terminal_nets", {})
    edges = state.get("edges", [])
    user_message = state.get("user_message", "")
    constraint_text = state.get("constraint_text", "")
    selected_model = state.get("selected_model", "Gemini")
    snapshot = state.get("deterministic_snapshot") or nodes

    PIXELS_PER_UM = 34.0
    gap_um = gap_px / PIXELS_PER_UM if gap_px > 0 else 0.0

    # ── Step 5a: Run DRC check ─────────────────────────────────────────
    log_section(f"Step 5a: DRC Check (attempt {retry_num + 1})")
    log_detail(f"Checking {len(nodes)} devices, gap={gap_um:.4f} um")

    drc_result = run_drc_check(nodes, gap_um)
    if drc_result["pass"]:
        elapsed = time.time() - t0
        log_detail("DRC PASSED — no violations!")
        ip_step("5/5 DRC critic", f"pass — attempt {retry_num + 1} ({elapsed:.1f}s)")
        updated_chat_history = _update_and_save_chat_history(
            chat_history=chat_history, user_content="",
            node_role="DRC Critic Assistant", node_content="Clean placement. No DRC violations found.",
        )
        return {
            "drc_pass": True, "drc_flags": [],
            "chat_history": updated_chat_history, "drc_retry_count": retry_num + 1,
            "last_agent": "drc_critic",
        }

    n_violations = len(drc_result['violations'])
    log_detail(f"DRC FAILED — {n_violations} violation(s) found:")
    for i, v in enumerate(drc_result['violations'][:20]):
        log_detail(f"  [{i+1}] {v[:120]}")
    if n_violations > 20:
        log_detail(f"  ... and {n_violations - 20} more")

    # ── Step 5b: LLM-based fixes ───────────────────────────────────────
    log_section("Step 5b: LLM-based DRC fixes")
    prior_cmds_text = "\n".join(f"[CMD]{json.dumps(c)}[/CMD]" for c in pending_cmds[-10:])
    violation_text = format_drc_violations_for_llm(drc_result, prior_cmds_text)
    active_nodes_for_context = [n for n in nodes if not _is_dummy_node(n)]
    logical_nodes = aggregate_to_logical_devices(active_nodes_for_context)
    current_placement_context = build_placement_context(
        logical_nodes, constraint_text, terminal_nets=terminal_nets, edges=edges,
    )
    critic_user = (
        f"User request: {user_message}\n\n"
        f"Please fix the following DRC violations:\n\n{violation_text}\n\n"
        f"=== CURRENT DEVICE POSITIONS ===\n{current_placement_context}"
    )
    critic_msgs = _build_llm_messages(DRC_CRITIC_PROMPT, chat_history, critic_user)
    log_detail(f"Calling LLM ({selected_model}, weight=heavy)...")

    critic_response = ""
    critic_cmds = []
    try:
        llm_t0 = time.time()
        critic_raw_response = _invoke_with_retry(critic_msgs, selected_model, "heavy", "DRC")
        llm_elapsed = time.time() - llm_t0
        critic_response, drc_thinking = _split_content_and_thinking(critic_raw_response.content)
        critic_response = _strip_thinking_text(critic_response)
        critic_cmds, _ = extract_cmd_blocks(critic_response)
        log_detail(f"LLM responded in {llm_elapsed:.1f}s with {len(critic_cmds)} fix(es)")
        _print_thinking_block("DRC", drc_thinking)
    except Exception as exc:
        log_detail(f"LLM Error: {exc}")
        critic_response = f"[DRC] LLM Error: {exc}"

    updated_chat_history = _update_and_save_chat_history(
        chat_history=chat_history, user_content="",
        node_role="DRC Critic Assistant", node_content=critic_response,
    )

    # ── Step 5c: Prescriptive (mechanical) fixes ───────────────────────
    log_section("Step 5c: Prescriptive mechanical fixes")
    prescriptive_cmds = compute_prescriptive_fixes(drc_result, gap_px, nodes=nodes)
    log_detail(f"Prescriptive engine generated {len(prescriptive_cmds)} fix(es)")
    for i, cmd in enumerate(prescriptive_cmds[:10]):
        dev = cmd.get("device", "?")
        x = cmd.get("x", "?")
        y = cmd.get("y", "?")
        log_detail(f"  [{i+1}] move {dev} → x={x}, y={y}")
    if len(prescriptive_cmds) > 10:
        log_detail(f"  ... and {len(prescriptive_cmds) - 10} more")

    # ── Step 5d: Merge LLM + prescriptive commands ─────────────────────
    log_section("Step 5d: Merging fix commands")
    if not critic_cmds:
        merged_cmds = prescriptive_cmds
        log_detail(f"Using {len(prescriptive_cmds)} prescriptive commands only (LLM gave none)")
    else:
        llm_dev_ids = {
            c.get("device") or c.get("device_id") or c.get("id") or c.get("device_a") or c.get("a", "")
            for c in critic_cmds
        }
        merged_cmds = list(critic_cmds) + [
            p for p in prescriptive_cmds
            if (p.get("device") or p.get("device_id") or p.get("id") or p.get("device_a") or p.get("a", ""))
            not in llm_dev_ids
        ]
        log_detail(f"Merged: {len(critic_cmds)} LLM + {len(merged_cmds) - len(critic_cmds)} prescriptive = {len(merged_cmds)} total")

    # ── Step 5e: Apply all fixes (symmetry-aware) ─────────────────────
    log_section("Step 5e: Applying all fixes to snapshot")

    # Build symmetry pair lookup from [SYMMETRY] block so that
    # if we move one side of a pair, we mirror the delta to the other side.
    sym_info = parse_symmetry_block(constraint_text)
    sym_pair_map: dict = {}  # device_id -> (partner_id, side: 'left'|'right')
    if sym_info:
        for left, right, _rank in sym_info.get("pairs", []):
            sym_pair_map[left] = (right, "left")
            sym_pair_map[right] = (left, "right")

    accumulated_cmds = list(merged_cmds)
    
    # Deduplicate — keep latest command per device
    deduped_dict = {}
    for cmd in accumulated_cmds:
        dev_id = cmd.get("device") or cmd.get("device_id") or cmd.get("id") or cmd.get("device_a") or cmd.get("a", "")
        action = cmd.get("action", "")
        if action and dev_id:
            deduped_dict[(action, dev_id)] = cmd
        else:
            sig = json.dumps(cmd, sort_keys=True)
            deduped_dict[sig] = cmd
    accumulated_cmds = list(deduped_dict.values())
    log_detail(f"Accumulated: {len(accumulated_cmds)} unique commands after dedup")

    # Symmetry mirror guard: if a move cmd targets one side of a [SYMMETRY] pair,
    # inject a mirrored delta for the partner so symmetry is preserved through DRC fixes.
    if sym_pair_map:
        node_x_map: dict = {str(n.get("id", "")): float(n.get("geometry", {}).get("x", 0.0))
                            for n in snapshot if n.get("geometry")}
        extra_cmds = []
        touched_by_guard = set()
        for cmd in accumulated_cmds:
            if cmd.get("action") != "move":
                continue
            dev_id = cmd.get("device", "")
            if dev_id not in sym_pair_map or dev_id in touched_by_guard:
                continue
            partner_id, side = sym_pair_map[dev_id]
            if partner_id in touched_by_guard:
                continue
            # Compute dx this fix applies to the moved device
            old_x = node_x_map.get(dev_id, cmd.get("x", 0.0))
            new_x = float(cmd.get("x", old_x))
            dx = new_x - old_x
            if abs(dx) < 1e-9:
                continue
            # Mirror dx: left moves right => partner (right) moves left by same dx
            partner_old_x = node_x_map.get(partner_id, 0.0)
            mirror_dx = -dx if side == "left" else -dx
            partner_new_x = round(partner_old_x + mirror_dx, 6)
            extra_cmds.append({
                "action": "move",
                "device": partner_id,
                "x": partner_new_x,
                "y": cmd.get("y", node_x_map.get(partner_id, 0.0)),
            })
            touched_by_guard.add(dev_id)
            touched_by_guard.add(partner_id)
            log_detail(f"[SYMM-GUARD] mirror fix: {dev_id} dx={dx:+.4f} → {partner_id} dx={mirror_dx:+.4f}")
        if extra_cmds:
            accumulated_cmds = accumulated_cmds + extra_cmds
            log_detail(f"[SYMM-GUARD] injected {len(extra_cmds)} mirror cmd(s) to preserve symmetry")

    fixed_nodes = apply_cmds_to_nodes(snapshot, accumulated_cmds)

    # ── Step 5f: Mechanical overlap resolution ─────────────────────────
    log_section("Step 5f: Mechanical overlap resolution")
    moved_ids = resolve_overlaps(fixed_nodes)
    if moved_ids:
        log_detail(f"Physics guard nudged {len(moved_ids)} device(s)")
    else:
        log_detail("No residual overlaps found")

    fixed_nodes = enforce_reflection_symmetry(fixed_nodes)
    fixed_nodes = legalize_vertical_rows(fixed_nodes)

    # ── Step 5g: Final DRC re-check ────────────────────────────────────
    log_section("Step 5g: Final DRC re-check")
    final_drc = run_drc_check(fixed_nodes, gap_um)
    remaining = len(final_drc.get('violations', []))
    if final_drc["pass"]:
        log_detail("ALL violations cleared!")
    else:
        log_detail(f"{remaining} violation(s) still remain:")
        for i, v in enumerate(final_drc['violations'][:10]):
            log_detail(f"  [{i+1}] {v[:120]}")

    # Show final positions
    log_device_positions(fixed_nodes, "Post-DRC Device Positions")

    structured_flags = []
    for v in final_drc.get("structured", []):
        if isinstance(v, dict):
            structured_flags.append(v)
        elif hasattr(v, "__slots__"):
            structured_flags.append({slot: getattr(v, slot, None) for slot in v.__slots__})
        elif hasattr(v, "__dict__"):
            structured_flags.append(dict(v.__dict__))
        else:
            structured_flags.append({"value": str(v)})

    elapsed = time.time() - t0
    if final_drc["pass"]:
        ip_step("5/5 DRC Critic", f"pass — attempt {retry_num + 1} ({elapsed:.1f}s)")
    else:
        retries_left = max(0, 2 - retry_num)
        ip_step("5/5 DRC Critic", f"attempt {retry_num + 1}, fail ({retries_left} left), {remaining} violations ({elapsed:.1f}s)")

    return {
        "placement_nodes": fixed_nodes,
        "pending_cmds": accumulated_cmds,
        "drc_pass": final_drc["pass"],
        "drc_flags": structured_flags,
        "chat_history": updated_chat_history,
        "drc_retry_count": retry_num + 1,
        "last_agent": "drc_critic",
    }

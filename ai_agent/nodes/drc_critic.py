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
from ai_agent.core.drc import run_drc_check, compute_prescriptive_fixes
from ai_agent.agents.drc_critic import DRC_CRITIC_PROMPT, format_drc_violations_for_llm
from ai_agent.tools.overlap_resolver import resolve_overlaps
from ai_agent.tools.cmd_parser import extract_cmd_blocks, apply_cmds_to_nodes
from ai_agent.placement.symmetry import enforce_reflection_symmetry
from ai_agent.nodes.symmetry_enforcer import parse_symmetry_block
from ai_agent.nodes._shared import (
    _update_and_save_chat_history,
    ip_step,
)
from ai_agent.utils.logging import (
    log_section, log_detail, stage_start,
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
    gap_px = state.get("gap_px", 0.0)
    terminal_nets = state.get("terminal_nets", {})

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

        intent = state.get("intent", "drc_critic")
        if intent == "placement_specialist":
            placement_text = state.get("placement_text", "")
        else:
            placement_text = ""

        return {
            "placement_nodes": nodes,
            "drc_pass": True, "drc_flags": [],
            "chat_history": updated_chat_history, "drc_retry_count": retry_num + 1,
            "last_agent": "drc_critic",
            "placement_text": placement_text,
        }

    n_violations = len(drc_result['violations'])
    log_detail(f"DRC FAILED — {n_violations} violation(s) found:")
    for i, v in enumerate(drc_result['violations'][:20]):
        log_detail(f"  [{i+1}] {v[:120]}")
    if n_violations > 20:
        log_detail(f"  ... and {n_violations - 20} more")

    # ── Step 5b: Apply prescriptive fixes ─────────────────────────────────────
    log_section(f"Step 5b: Applying prescriptive DRC fixes (attempt {retry_num + 1})")
    intent = state.get("intent", "drc_critic")
    if intent == "placement_specialist":
        placement_text = state.get("placement_text", "")
    else:
        placement_text = ""

    fixes = compute_prescriptive_fixes(
        drc_result=drc_result,
        gap_px=gap_px,
        nodes=nodes,
        geometric_tags=state.get("groups", {}),
        terminal_nets=terminal_nets,
    )

    if fixes:
        log_detail(f"Generated {len(fixes)} prescriptive DRC fix command(s).")
        fixed_nodes = apply_cmds_to_nodes(nodes, fixes)
        fixed_drc = run_drc_check(fixed_nodes, gap_um)
        if fixed_drc["pass"]:
            log_detail("Prescriptive fixes succeeded! DRC is now clean.")
            elapsed = time.time() - t0
            ip_step("5/5 DRC critic", f"pass with prescriptive fixes — attempt {retry_num + 1} ({elapsed:.1f}s)")
            updated_chat_history = _update_and_save_chat_history(
                chat_history=chat_history, user_content="",
                node_role="DRC Critic Assistant",
                node_content="DRC violations found and successfully resolved using prescriptive fixes.",
            )
            return {
                "placement_nodes": fixed_nodes,
                "drc_pass": True,
                "drc_flags": [],
                "pending_cmds": state.get("pending_cmds", []) + fixes,
                "chat_history": updated_chat_history,
                "drc_retry_count": retry_num + 1,
                "last_agent": "drc_critic",
                "placement_text": placement_text,
            }
        else:
            log_detail(f"Prescriptive fixes applied but {len(fixed_drc['violations'])} violation(s) remain.")
            nodes = fixed_nodes
            drc_result = fixed_drc
    else:
        log_detail("No prescriptive fixes could be calculated.")

    elapsed = time.time() - t0
    ip_step("5/5 DRC Critic", f"fail — attempt {retry_num + 1} ({elapsed:.1f}s)")

    return {
        "placement_nodes": nodes,
        "drc_pass": False,
        "drc_flags": list(drc_result.get("violations", [])),
        "chat_history": chat_history,
        "drc_retry_count": retry_num + 1,
        "last_agent": "drc_critic",
        "placement_text": placement_text,
    }

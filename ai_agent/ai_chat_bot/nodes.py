"""
LangGraph nodes
"""
import copy
import json
from pathlib import Path
from ai_agent.ai_chat_bot.run_llm import run_llm
from langgraph.types import interrupt
from ai_agent.ai_chat_bot.state import LayoutState
from ai_agent.ai_chat_bot.finger_grouping import aggregate_to_logical_devices
# Domain logic & Prompts
import ai_agent.ai_chat_bot.agents.topology_analyst as topology_analyst
import ai_agent.ai_chat_bot.agents.strategy_selector as strategy_selector
from ai_agent.ai_chat_bot.tools import tool_resolve_overlaps
from ai_agent.ai_chat_bot.agents.strategy_selector import parse_placement_mode
from ai_agent.ai_chat_bot.agents.placement_specialist import PLACEMENT_SPECIALIST_PROMPT, build_placement_context
from ai_agent.ai_chat_bot.agents.drc_critic import DRC_CRITIC_PROMPT, run_drc_check, format_drc_violations_for_llm, compute_prescriptive_fixes
from ai_agent.ai_chat_bot.agents.routing_previewer import ROUTING_PREVIEWER_PROMPT, score_routing, format_routing_for_llm
from ai_agent.ai_chat_bot.tools import tool_validate_device_count
from ai_agent.ai_chat_bot.finger_grouping import expand_logical_to_fingers, validate_finger_integrity
from ai_agent.ai_chat_bot.cmd_utils import _extract_cmd_blocks, _apply_cmds_to_nodes
# Optional: Import your RAG save function if you have it
# from ai_agent.rag_manager import save_run_as_example
from ai_agent.ai_chat_bot.rag_retriever import build_rag_context

CHAT_HISTORY_JSON_PATH = Path(__file__).resolve().parents[1] / "chat_history.json"


def _normalize_chat_history(chat_history):
    normalized = []
    if not isinstance(chat_history, list):
        return normalized

    for msg in chat_history:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "")).strip()
        content = str(msg.get("content", "")).strip()
        if not role or not content:
            continue
        normalized.append({"role": role, "content": content})

    return normalized


def _append_chat_message(chat_history, role, content, dedupe_last=False):
    if not content:
        return chat_history

    role_text = str(role).strip()
    content_text = str(content).strip()
    if not role_text or not content_text:
        return chat_history

    if dedupe_last and chat_history:
        last = chat_history[-1]
        if (
            isinstance(last, dict)
            and str(last.get("role", "")).strip() == role_text
            and str(last.get("content", "")).strip() == content_text
        ):
            return chat_history

    chat_history.append({"role": role_text, "content": content_text})
    return chat_history


def _save_chat_history_json(chat_history):
    serializable = _normalize_chat_history(chat_history)
    try:
        CHAT_HISTORY_JSON_PATH.write_text(
            json.dumps(serializable, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"[CHAT] Failed to save chat history: {exc}")


def _update_and_save_chat_history(chat_history, user_content, node_role=None, node_content=None):
    updated_chat_history = _normalize_chat_history(chat_history)
    if user_content:
        updated_chat_history = _append_chat_message(updated_chat_history, "user", user_content, dedupe_last=True)
    if node_role:
        _append_chat_message(updated_chat_history, node_role, node_content)
    _save_chat_history_json(updated_chat_history)
    return updated_chat_history


def _build_llm_messages(system_prompt, chat_history, user_prompt, max_history=8):
    messages = [{"role": "system", "content": str(system_prompt)}]
    for msg in _normalize_chat_history(chat_history)[-max_history:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": str(user_prompt).strip()})
    return messages

def node_topology_analyst(state: LayoutState):
    """
    Stage 1: Topology Analyst
    Extracts constraints from the topology and queries the LLM to formulate 
    a confirmation question.
    """
    print("[TOPO] Starting Topology Analyst...")
    nodes = state.get("nodes", [])
    terminal_nets = state.get("terminal_nets", {})
    sp_file_path = state.get("sp_file_path", "")
    user_message = state.get("user_message", "Please analyze the layout topology.")
    chat_history = state.get("chat_history", [])

    logical_nodes = aggregate_to_logical_devices(nodes)
    
    constraint_text = topology_analyst.analyze_json(
        logical_nodes, terminal_nets
    )
    print(f"[TOPO] Extracted Constraints:\n{constraint_text}")
    constraints = [line for line in constraint_text.splitlines() if line.strip()]

    constraint_warning = ""
    if not constraint_text.strip():
        sp_status = f"SPICE file not found: {sp_file_path!r}" if sp_file_path else "No SPICE file provided"
        print(f"[TOPO] Warning: No topology constraints extracted. ({sp_status})")
        constraint_warning = (
            "\n\n⚠️ **WARNING**: No topology constraints could be extracted.\n"
            f"({sp_status}, and no terminal_nets from layout canvas).\n"
            "Please load a SPICE netlist (.sp/.cir) before running AI placement."
        )

    analyst_user = (
        f"User request: {user_message}\n\n"
        f"Extracted Constraints:\n{constraint_text}\n\n"
        "Formulate a brief response confirming these constraints and "
        "ask the user if they look correct."
    )

    analyst_msgs = _build_llm_messages(
        topology_analyst.TOPOLOGY_ANALYST_PROMPT,
        chat_history,
        analyst_user,
    )

    try:
        analyst_response = run_llm(analyst_msgs, analyst_user)
        question = analyst_response.strip()
        if question.startswith("{") and question.endswith("}"):
            question = None
    except Exception as exc:
        print(f"[TOPO] Stage 1 LLM failed (using fallback): {exc}")
        question = None

    if not question:
        question = (
            f"🔬 **Topology Analysis Complete**\n\n"
            f"I identified the following structures:\n\n{constraint_text}\n\n"
            f"**Is this correct?** Reply 'Yes' to proceed, or let me know any corrections."
        )

    if constraint_warning:
        question += constraint_warning

    strategy_options = strategy_selector.generate_strategies(
        user_message,
        constraint_text,
        run_llm,
        chat_history=chat_history,
    )
    question = question + "\n\n---\n\n" + strategy_options

    updated_chat_history = _update_and_save_chat_history(
        chat_history=chat_history,
        user_content=user_message,
        node_role="Analyzer Asssistant",
        node_content=question,
    )

    return {
        "constraints": constraints,
        "constraint_text": constraint_text,
        "strategy_question": question,
        "chat_history": updated_chat_history,
    }


def node_strategy_selector(state: LayoutState):
    """
    Stage 1.5: Strategy Selector
    Presents the strategies generated by the Topology Analyst to the user,
    pauses execution to wait for their input, and parses their selection.
    """
    question = state.get("strategy_question", "Please select a placement strategy.")
    constraint_text = state.get("constraint_text", "")
    chat_history = state.get("chat_history", [])
    
    # Interrupt execution to prompt the user in the UI
    user_reply = interrupt({
        "type": "strategy_selection", 
        "question": question
    })
    
    if isinstance(user_reply, dict):
        user_message = user_reply.get("response", str(user_reply))
    else:
        user_message = str(user_reply)
        
    placement_mode = strategy_selector.parse_placement_mode(
        user_message=user_message,
        constraint_text=constraint_text
    )
    
    print(f"[STRATEGY] User selected raw input: {user_message!r}")
    print(f"[STRATEGY] Parsed placement mode: {placement_mode}")

    updated_chat_history = _update_and_save_chat_history(
        chat_history=chat_history,
        user_content=user_message,
    )
    
    return {
        "selected_strategy": user_message,
        "chat_history": updated_chat_history,
    }


def node_placement_specialist(state: LayoutState):
    """
    Stage 2: Placement Specialist
    Generates [CMD] blocks for device positioning while enforcing strict
    inventory conservation, row-based analog constraints, and routing quality.
    """
    print("[PLACEMENT] Starting Placement Specialist...")
    nodes = state.get("nodes", [])
    constraint_text = state.get("constraint_text", "")
    user_message = state.get("user_message", "Optimize placement.")
    chat_history = state.get("chat_history", [])
    edges = state.get("edges", [])
    terminal_nets = state.get("terminal_nets", {})
    selected_strategy = state.get("selected_strategy", "auto")
    pending_cmds = state.get("pending_cmds", [])
    
    # Safely get current placement, fallback to unplaced nodes if empty
    working_nodes = state.get("placement_nodes", [])
    if not working_nodes:
        working_nodes = copy.deepcopy(nodes)

    #  Handle Human-in-the-Loop Manual Edits(returning from final stage)
    # =====================================================================
    if pending_cmds:
        print(f"[PLACEMENT] Human loopback detected! Applying {len(pending_cmds)} manual edits directly.")
        updated_nodes = _apply_cmds_to_nodes(working_nodes, pending_cmds)
        
        return {
            "placement_nodes": updated_nodes,
            "pending_cmds": [],  #Clear cmds so we don't apply them twice
            "drc_retry_count": 0,    # Reset for DRC critic
            "routing_pass_count": 0  # NEW: Reset for Routing
        }
    # =====================================================================

    # If no pending manual edits, proceed with normal LLM generation
    #logical_nodes = aggregate_to_logical_devices(nodes)
    rag_context = build_rag_context(nodes, edges, terminal_nets, top_k=3)
    context_text = build_placement_context(
        nodes,
        constraint_text,
        terminal_nets=terminal_nets,
        edges=edges,
    )

    placer_user = (
        f"Initial User request: {user_message}\n\n"
        f"Selected Strategy: {selected_strategy}\n\n"
        f"{context_text}"
    )

    placer_msgs = _build_llm_messages(
        PLACEMENT_SPECIALIST_PROMPT,
        chat_history,
        placer_user,
    )

    placement_response = ""
    try:
        placement_response = run_llm(placer_msgs, placer_user)
        stage2_cmds = _extract_cmd_blocks(placement_response)
    except Exception as exc:
        print(f"[PLACEMENT] LLM failed: {exc}")
        placement_response = "[PLACEMENT] LLM failed to generate a response."
        stage2_cmds = []

    updated_chat_history = _update_and_save_chat_history(
        chat_history=chat_history,
        user_content=user_message,
        node_role="Placement Specialist Assistant",
        node_content=placement_response,
    )

    # Apply LLM commands to the base nodes
    working_nodes = _apply_cmds_to_nodes(nodes, stage2_cmds)

    # Validate Device Conservation
    conservation = tool_validate_device_count(nodes, working_nodes)
    if not conservation["pass"]:
        missing_ids = conservation.get("missing", [])
        print(f"[PLACEMENT] ⚠ CRITICAL CONSERVATION FAILURE: missing devices {missing_ids}")
        print("[PLACEMENT] Reverting placement to original nodes.")
        working_nodes = copy.deepcopy(nodes)
        stage2_cmds = []

    current_pending = state.get("pending_cmds", [])
    print("[PLACEMENT] Stage 2 complete. Generated commands:")
    for cmd in current_pending + stage2_cmds:
        print(f"  - {cmd}")

    return {
        "placement_nodes": working_nodes,
        "pending_cmds": current_pending + stage2_cmds,
        "chat_history": updated_chat_history,
    }


def node_finger_expansion(state: LayoutState):
    logical_nodes = state.get("placement_nodes", [])
    original_nodes = state.get("nodes", []) 
    
    physical_nodes = expand_logical_to_fingers(logical_nodes, original_nodes)
    
    validate_finger_integrity(original_nodes, physical_nodes)
    
    return {"placement_nodes": physical_nodes,
            "deterministic_snapshot": copy.deepcopy(physical_nodes),
            }


def node_drc_critic(state: LayoutState):
    """
    Stage 3: DRC Critic
    Validates placement geometry and applies LLM + prescriptive fixes,
    followed by a final physics guard.

    Corrections are always applied on top of the deterministic_snapshot
    (saved by node_deterministic_optimizer) so that each retry is idempotent
    and fixes never compound on already-mutated positions.
    """
    print("[DRC] Starting DRC Critic...")
    nodes           = state.get("placement_nodes", [])
    pending_cmds    = state.get("pending_cmds", [])
    chat_history    = state.get("chat_history", [])
    gap_px          = state.get("gap_px", 0.0)
    terminal_nets   = state.get("terminal_nets", {})
    edges           = state.get("edges", [])
    user_message    = state.get("user_message", "")
    constraint_text = state.get("constraint_text", "")

    # Use the post-deterministic snapshot as the stable correction baseline.
    # Falls back to current placement_nodes if the snapshot is not yet in state
    snapshot = state.get("deterministic_snapshot") or nodes

    PIXELS_PER_UM = 34.0
    gap_um = gap_px / PIXELS_PER_UM if gap_px > 0 else 0.0

    # ── Initial DRC check ────────────────────────────────────────────────────
    drc_result = run_drc_check(nodes, gap_um)

    if drc_result["pass"]:
        print("[DRC] Clean placement! No violations.")
        updated_chat_history = _update_and_save_chat_history(
            chat_history=chat_history,
            user_content="",
            node_role="DRC Critic Assistant",
            node_content="Clean placement. No DRC violations found.",
        )
        return {
            "drc_pass": True,
            "drc_flags": [],
            "chat_history": updated_chat_history,
            "drc_retry_count": state.get("drc_retry_count", 0) + 1,
        }

    print(f"[DRC] Found {len(drc_result['violations'])} violations. Attempting fix...")

    # ── Build LLM prompt ─────────────────────────────────────────────────────
    prior_cmds_text = "\n".join(
        f"[CMD]{json.dumps(c)}[/CMD]" for c in pending_cmds[-10:]
    )
    violation_text = format_drc_violations_for_llm(drc_result, prior_cmds_text)

    logical_nodes = aggregate_to_logical_devices(nodes)
    current_placement_context = build_placement_context(
        logical_nodes,
        constraint_text,
        terminal_nets=terminal_nets,
        edges=edges,
    )

    critic_user = (
        f"User request: {user_message}\n\n"
        f"Please fix the following DRC violations:\n\n{violation_text}\n\n"
        f"=== CURRENT DEVICE POSITIONS ===\n{current_placement_context}"
    )

    critic_msgs = _build_llm_messages(
        DRC_CRITIC_PROMPT,
        chat_history,
        critic_user,
    )

    # ── LLM correction pass ──────────────────────────────────────────────────
    critic_response = ""
    try:
        critic_response = run_llm(critic_msgs, critic_user)
        critic_cmds = _extract_cmd_blocks(critic_response)
    except Exception as exc:
        print(f"[DRC] LLM Error: {exc}")
        critic_response = f"[DRC] LLM Error: {exc}"
        critic_cmds = []

    updated_chat_history = _update_and_save_chat_history(
        chat_history=chat_history,
        user_content="",
        node_role="DRC Critic Assistant",
        node_content=critic_response,
    )

    prescriptive_cmds = compute_prescriptive_fixes(drc_result, gap_px, nodes=nodes)

    # ── Merge: LLM owns the devices it addressed; prescriptive fills the rest ─
    if not critic_cmds:
        print("[DRC] LLM provided no fixes — using prescriptive fixes entirely.")
        merged_cmds = prescriptive_cmds
    else:
        llm_dev_ids = {
            c.get("device") or c.get("device_id") or c.get("id") or
            c.get("device_a") or c.get("a", "")
            for c in critic_cmds
        }
        merged_cmds = list(critic_cmds) + [
            p for p in prescriptive_cmds
            if (p.get("device") or p.get("device_id") or p.get("id") or
                p.get("device_a") or p.get("a", "")) not in llm_dev_ids
        ]
        print(
            f"[DRC] Merged {len(critic_cmds)} LLM fix(es) + "
            f"{len(merged_cmds) - len(critic_cmds)} prescriptive fill-in(s)."
        )

    # ── Apply corrections on top of the deterministic snapshot ───────────────
    # Preserve original command sequence and avoid device-key overwrites.
    merged_all_cmds = list(pending_cmds) + list(merged_cmds)
    seen_cmds = set()
    accumulated_cmds = []
    for cmd in merged_all_cmds:
        sig = json.dumps(cmd, sort_keys=True)
        if sig in seen_cmds:
            continue
        seen_cmds.add(sig)
        accumulated_cmds.append(cmd)

    # Always recompute from snapshot so retries don't compound on mutated state
    fixed_nodes = _apply_cmds_to_nodes(snapshot, accumulated_cmds)

    # ── Final physics guard ───────────────────────────────────────────────────
    print("[DRC] Running final physics guard (overlap resolution)...")
    moved_ids = tool_resolve_overlaps(fixed_nodes)
    if moved_ids:
        print(f"[DRC] Physics guard nudged {len(moved_ids)} device(s).")
        # Sync physics-guard moves back into accumulated_cmds
        moved_map = {n["id"]: n for n in fixed_nodes if n["id"] in moved_ids}
        for dev_id, node in moved_map.items():
            accumulated_cmds.append({
                "action": "move",
                "device": dev_id,
                "x": float(node["geometry"]["x"]),
                "y": float(node["geometry"]["y"]),
            })

        # Keep sequence stable; remove only exact duplicate commands.
        seen_cmds = set()
        deduped_cmds = []
        for cmd in accumulated_cmds:
            sig = json.dumps(cmd, sort_keys=True)
            if sig in seen_cmds:
                continue
            seen_cmds.add(sig)
            deduped_cmds.append(cmd)
        accumulated_cmds = deduped_cmds

    # ── Post-guard DRC re-check ───────────────────────────────────────────────
    final_drc = run_drc_check(fixed_nodes, gap_um)
    if final_drc["pass"]:
        print("[DRC] All violations cleared.")
    else:
        print(f"[DRC] {len(final_drc['violations'])} violation(s) remain after fixes.")

    structured_flags = []
    for v in final_drc.get("structured", []):
        if isinstance(v, dict):
            structured_flags.append(v)
            continue

        if hasattr(v, "__slots__"):
            structured_flags.append(
                {slot: getattr(v, slot, None) for slot in v.__slots__}
            )
            continue

        if hasattr(v, "__dict__"):
            structured_flags.append(dict(v.__dict__))
            continue

        structured_flags.append({"value": str(v)})

    print("[DRC] New accumulated commands after LLM + prescriptive fixes + physics guard:")
    for cmd in accumulated_cmds:
        print(f"  - {cmd}")

    return {
        "placement_nodes":  fixed_nodes,
        "pending_cmds":     accumulated_cmds,
        "drc_pass":         final_drc["pass"],
        "drc_flags":        structured_flags,
        "chat_history":     updated_chat_history,
        "drc_retry_count":  state.get("drc_retry_count", 0) + 1,
    }

def node_routing_previewer(state: LayoutState):
    """
    Stage 4: Routing Pre-Viewer
    One LLM swap pass for routing improvement.
    """
    print("[ROUTING] Starting Routing Previewer...")
    current_passes = state.get("routing_pass_count", 0)
    nodes         = state.get("placement_nodes", [])
    edges         = state.get("edges", [])
    terminal_nets = state.get("terminal_nets", {})
    pending_cmds  = state.get("pending_cmds", [])
    chat_history  = state.get("chat_history", [])
    user_message  = state.get("user_message", "")

    working_nodes = [n for n in nodes]  # shallow copy — _apply_cmds deep copies internally

    initial_routing = score_routing(working_nodes, edges, terminal_nets)
    initial_cost    = initial_routing.get("placement_cost", float("inf"))
    print(f"[ROUTING] Pass {current_passes + 1} — initial cost: {initial_cost:.4f}")

    # Early exit if already optimal 
    if initial_routing.get("score", 0) < 3 and initial_routing.get("total_wire_length", 0) < 5.0:
        print("[ROUTING] Already optimal — skipping.")
        updated_chat_history = _update_and_save_chat_history(
            chat_history=chat_history,
            user_content="",
            node_role="Routing Previewer Assistant",
            node_content="Routing already optimal. No additional swaps were needed.",
        )
        return {
            "routing_result":    initial_routing,
            "chat_history":      updated_chat_history,
            "routing_pass_count": current_passes + 1,
        }

    routing_text = format_routing_for_llm(initial_routing, working_nodes, terminal_nets)
    current_positions = ", ".join(
        f"{n['id']}@({round(float(n['geometry']['x']), 3)},"
        f"{round(float(n['geometry']['y']), 3)})"
        for n in working_nodes if not n.get("is_dummy")
    )

    router_user = (
        f"User request: {user_message}\n\n"
        f"{routing_text}\n\n"
        f"Current positions: {current_positions}"
    )
    router_msgs = _build_llm_messages(
        ROUTING_PREVIEWER_PROMPT,
        chat_history,
        router_user,
    )

    # ── LLM swap pass (first pass only) ───────────────
    applied_cmds = []
    router_response = ""
    try:
        router_response = run_llm(router_msgs, router_user)
        router_cmds     = _extract_cmd_blocks(router_response)
    except Exception as exc:
        print(f"[ROUTING] LLM error: {exc}")
        router_response = f"[ROUTING] LLM Error: {exc}"
        router_cmds = []

    updated_chat_history = _update_and_save_chat_history(
        chat_history=chat_history,
        user_content="",
        node_role="Routing Previewer Assistant",
        node_content=router_response,
    )

    if router_cmds:
        trial_nodes = _apply_cmds_to_nodes(working_nodes, router_cmds)
        tool_resolve_overlaps(trial_nodes)
        new_routing = score_routing(trial_nodes, edges, terminal_nets)
        new_cost    = new_routing.get("placement_cost", float("inf"))

        if new_cost < initial_cost:
            print(f"[ROUTING] LLM improved cost: {initial_cost:.4f} → {new_cost:.4f}")
            working_nodes   = trial_nodes
            initial_routing = new_routing
            initial_cost    = new_cost
            applied_cmds    = router_cmds
        else:
            print(f"[ROUTING] LLM swaps rejected: {new_cost:.4f} >= {initial_cost:.4f}")

    # ── Accumulate commands (preserve existing order, never overwrite by device key) ──
    # Dict keying by device drops/replaces prior commands (e.g. move + swap on same device).
    # Keep all commands in sequence and only skip exact duplicates.
    merged_cmds = list(pending_cmds) + list(applied_cmds)
    seen_cmds = set()
    accumulated_cmds = []
    for cmd in merged_cmds:
        sig = json.dumps(cmd, sort_keys=True)
        if sig in seen_cmds:
            continue
        seen_cmds.add(sig)
        accumulated_cmds.append(cmd)

    print("[ROUTING] Completed new accumulated commands: ")
    for cmd in accumulated_cmds:
        print(f"  - {cmd}")

    return {
        "placement_nodes":    working_nodes,
        "routing_result":     initial_routing,
        "pending_cmds":       accumulated_cmds,
        "routing_pass_count": current_passes + 1,
        "chat_history":       updated_chat_history,
    }

def node_human_viewer(state: LayoutState):
    """
    Final review stage.
    Interrupts execution to send layout data to the UI.
    """
    ui_response = interrupt({
        "type": "visual_review",
        "placement": state.get("pending_cmds", []),
        "routing": state.get("routing_result", {})
    })
    
    chat_history = state.get("chat_history", [])
    if isinstance(ui_response, dict):
        if ui_response.get("approved"):
            user_content = "User approved the layout in visual review."
        else:
            edits = ui_response.get("edits", [])
            user_content = f"User requested visual review edits: {json.dumps(edits)}"
    else:
        user_content = str(ui_response)

    updated_chat_history = _update_and_save_chat_history(
        chat_history=chat_history,
        user_content=user_content,
    )

    if isinstance(ui_response, dict) and ui_response.get("approved"):
        return {"approved": True, "chat_history": updated_chat_history}
    else:
        return {
            "approved": False, 
            "pending_cmds": ui_response.get("edits", []) if isinstance(ui_response, dict) else [],
            "chat_history": updated_chat_history,
        }


def node_save_to_rag(state: LayoutState):
    """
    Final Stage: Saves the successful layout to RAG database if it passes quality checks.
    """
    # Uncomment and configure this block if you are using rag_manager
    """
    working_nodes = state.get("placement_nodes", [])
    edges = state.get("edges", [])
    terminal_nets = state.get("terminal_nets", {})
    drc_result = {"violations": state.get("drc_flags", []), "pass": state.get("drc_pass", True)}
    routing_result = state.get("routing_result", {})
    
    pending_cmds = state.get("pending_cmds", [])
    run_label = f"auto_{len(pending_cmds)}cmds_drc{len(drc_result['violations'])}"
    drc_passed = state.get("drc_pass", False)
    routing_cost = state.get("routing_result", {}).get("placement_cost", 9999)
    if drc_passed and routing_cost < 5.0:
        try:
            from ai_agent.rag_manager import save_run_as_example
            save_run_as_example(
                working_nodes,
                edges,
                terminal_nets,
                drc_result,
                routing_result,
                label=run_label,
            )
            print(f"[RAG] High-quality run saved as '{run_label}'")
        except ImportError:
            pass
        except Exception as rag_exc:
            print(f"[RAG] Save failed: {rag_exc}")
    """
    
    return {}
    
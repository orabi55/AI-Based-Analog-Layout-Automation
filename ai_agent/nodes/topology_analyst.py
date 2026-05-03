"""
Topology Analyst Node
=====================
A LangGraph node that extracts electrical constraints from the circuit topology 
using a combination of pure Python analysis and LLM processing.

Functions:
- node_topology_analyst: Performs topology extraction and LLM-based analysis.
  - Inputs: state (dict)
  - Outputs: constraint text, analysis result, and updated chat history.
"""

import time
import ai_agent.agents.topology_analyst as topology_analyst
from ai_agent.placement.finger_grouper import aggregate_to_logical_devices
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


def node_topology_analyst(state):
    t0 = time.time()
    vprint("\n" + "═" * 60, flush=True)
    vprint("  STAGE 1: TOPOLOGY ANALYST", flush=True)
    vprint("═" * 60, flush=True)

    nodes = state.get("nodes", [])
    terminal_nets = state.get("terminal_nets", {})
    user_message = state.get("user_message", "Please analyze the layout topology.")
    chat_history = state.get("chat_history", [])
    selected_model = state.get("selected_model", "Gemini")

    vprint(f"[TOPO] Devices: {len(nodes)} | Nets: {len(terminal_nets)} | Model: {selected_model}", flush=True)

    logical_nodes = aggregate_to_logical_devices(nodes)
    vprint(f"[TOPO] Aggregated {len(nodes)} fingers → {len(logical_nodes)} logical devices", flush=True)

    constraint_text = topology_analyst.analyze_json(logical_nodes, terminal_nets)
    vprint(f"[TOPO] Extracted {len(constraint_text.splitlines())} constraint lines", flush=True)

    analyst_user = (
        f"User request: {user_message}\n\n"
        f"Extracted Constraints:\n{constraint_text}\n\n"
    )
    analyst_msgs = _build_llm_messages(
        topology_analyst.TOPOLOGY_ANALYST_PROMPT, chat_history, analyst_user,
    )
    vprint(f"[TOPO] Calling LLM ({selected_model}, weight=light)...", flush=True)

    try:
        analyst_response = _invoke_with_retry(analyst_msgs, selected_model, "light", "TOPO")
        analysis_txt, analysis_thinking = _split_content_and_thinking(analyst_response.content)
        analysis_txt = _strip_thinking_text(analysis_txt)
        _print_thinking_block("TOPO", analysis_thinking)
    except Exception as exc:
        vprint(f"[TOPO] ✗ LLM failed: {exc}", flush=True)
        analysis_txt = None

    updated_chat_history = _update_and_save_chat_history(
        chat_history=chat_history,
        user_content=user_message,
        node_role="Analyzer Assistant",
        node_content=analysis_txt,
    )

    elapsed = time.time() - t0
    nchar = len(analysis_txt) if analysis_txt else 0
    n_constraints = len(constraint_text.splitlines()) if constraint_text else 0
    if analysis_txt:
        ip_step("1/5 Topology Analyst", f"ok ({elapsed:.1f}s, {nchar} chars, {n_constraints} constraints)")
    else:
        ip_step("1/5 Topology Analyst", f"no LLM text ({elapsed:.1f}s, {n_constraints} constraints)")

    return {
        "constraint_text": constraint_text,
        "Analysis_result": analysis_txt,
        "chat_history": updated_chat_history,
        "last_agent": "topology_analyst",
        "pending_cmds": [], 
    }

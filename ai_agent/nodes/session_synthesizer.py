"""
Session synthesizer for chat_v2 specialist/checker outputs.
"""

from __future__ import annotations

import re
from typing import Optional


def _logical_id(node: dict) -> str:
    return str(node.get("parent_id") or node.get("id") or node.get("device_id") or node.get("name") or "")


def infer_interdigitation_from_nodes(user_message: str, nodes: list) -> Optional[str]:
    """Infer interdigitation/common-centroid hints from row ordering."""
    if not user_message or not isinstance(nodes, list):
        return None

    found_ids: list[str] = []
    for token in re.findall(r"\b([A-Za-z]{1,5}\d+)\b", user_message):
        if token not in found_ids:
            found_ids.append(token)
    if len(found_ids) < 2:
        return None

    dev_a, dev_b = found_ids[0], found_ids[1]
    row_nodes: list[tuple[float, str]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        logical = _logical_id(node)
        if logical not in {dev_a, dev_b}:
            continue
        geom = node.get("geometry") if isinstance(node.get("geometry"), dict) else node
        x = geom.get("x")
        y = geom.get("y")
        if x is None:
            continue
        row_nodes.append((float(x), logical if y is not None else logical))

    if len(row_nodes) < 4:
        return None

    row_nodes.sort(key=lambda item: item[0])
    seq = [logical for _, logical in row_nodes]
    alternating = all(seq[i] != seq[i + 1] for i in range(len(seq) - 1))
    has_both = dev_a in seq and dev_b in seq
    if not (alternating and has_both):
        return None

    return (
        f"{dev_a} and {dev_b} appear interdigitated because their fingers alternate. "
        "If this alternating pattern is symmetric about the center, it also satisfies "
        "common-centroid-style matching."
    )


def _synth_check_drc(state: dict) -> str:
    drc_pass = bool(state.get("drc_pass", False))
    drc_flags = state.get("drc_flags") or []

    if drc_pass:
        return "DRC check passed: no violations found."
    if isinstance(drc_flags, list) and drc_flags:
        lines = [f"DRC check found {len(drc_flags)} issue(s):"]
        for flag in drc_flags[:8]:
            if isinstance(flag, dict):
                desc = flag.get("description") or flag.get("message") or str(flag)
            else:
                desc = str(flag)
            lines.append(f"- {desc}")
        return "\n".join(lines)
    return "DRC check completed, but no detailed result was returned."


def _synth_fix_drc(state: dict) -> str:
    drc_text = _synth_check_drc(state)
    pending_cmds = state.get("pending_cmds") or []
    if pending_cmds:
        return f"{drc_text}\nPrepared {len(pending_cmds)} DRC-fix command(s) for validation."
    return drc_text


def _routing_log_text(state: dict) -> str:
    routing = state.get("routing_result") or {}
    if isinstance(routing, dict):
        return str(routing.get("log_text") or routing.get("summary") or "").strip()
    return ""


def _synth_check_routing(state: dict) -> str:
    text = _routing_log_text(state)
    if text:
        return text
    return "Routing check completed, but no detailed result was returned."


def _synth_optimize_routing(state: dict) -> str:
    nets = state.get("layout_session_target_nets") or state.get("target_nets") or []
    net_text = ", ".join(str(n) for n in nets if str(n).strip())
    routing_text = _routing_log_text(state)

    lines: list[str] = []
    if net_text:
        lines.append(f"I analyzed {net_text} for routing/parasitic reduction.")
    else:
        lines.append("I analyzed routing/parasitic optimization opportunities.")
    if routing_text:
        lines.append(routing_text)
    lines.append("Recommendations:")
    lines.append("1. Keep connected devices closer to reduce HPWL.")
    lines.append("2. Keep matched/differential routes symmetric.")
    lines.append("3. Reduce local crossings near output/load devices.")
    lines.append("No layout changes were applied automatically.")
    return "\n".join(lines)


def _synth_strategy(state: dict) -> str:
    user_message = str(state.get("user_message") or "")
    nodes = state.get("nodes") or state.get("placement_nodes") or []
    inferred = infer_interdigitation_from_nodes(user_message, nodes if isinstance(nodes, list) else [])
    if inferred:
        return inferred

    strategy_result = state.get("strategy_result")
    if isinstance(strategy_result, str) and strategy_result.strip():
        return strategy_result.strip()
    if isinstance(strategy_result, dict):
        pattern = strategy_result.get("matching_pattern")
        if pattern:
            return f"Matching strategy appears to be {pattern}."
        return f"Strategy summary: {strategy_result}"

    trace = state.get("initial_agent_trace") or {}
    strategy_trace = trace.get("strategy") if isinstance(trace, dict) else None
    if isinstance(strategy_trace, dict):
        groups = strategy_trace.get("matching_groups") or strategy_trace.get("matched_pairs")
        if groups:
            return f"Matching groups from initial trace: {groups}"
    if isinstance(strategy_trace, str) and strategy_trace.strip():
        return strategy_trace.strip()

    return "I could not determine the matching strategy from the available data."


def _synth_topology(state: dict) -> str:
    analysis = state.get("Analysis_result")
    if isinstance(analysis, str) and analysis.strip():
        return analysis.strip()
    if analysis:
        return str(analysis)
    trace = state.get("initial_agent_trace") or {}
    topology = trace.get("topology") if isinstance(trace, dict) else None
    if topology:
        return str(topology)
    return "Topology analysis completed, but no detailed result was returned."


def _synth_placement(state: dict) -> str:
    pending_cmds = state.get("pending_cmds") or []
    if pending_cmds:
        return f"Placement specialist prepared {len(pending_cmds)} command(s) for validation."
    text = str(state.get("assistant_text") or "").strip()
    if text:
        return text
    return "Placement analysis completed."


def node_session_synthesizer(state: dict) -> dict:
    """Build final user-facing assistant_text after specialist/checker nodes."""
    decision = str(state.get("layout_session_decision") or "")
    specialist = str(state.get("layout_session_specialist") or "")
    text = ""

    if decision == "check_drc":
        text = _synth_check_drc(state)
    elif decision == "fix_drc":
        text = _synth_fix_drc(state)
    elif decision == "check_routing":
        text = _synth_check_routing(state)
    elif decision == "optimize_routing":
        text = _synth_optimize_routing(state)
    elif specialist == "strategy_selector":
        text = _synth_strategy(state)
    elif specialist == "topology_analyst":
        text = _synth_topology(state)
    elif specialist == "placement_specialist":
        text = _synth_placement(state)

    if not text:
        text = str(state.get("assistant_text") or "").strip()
    if not text:
        text = "I completed the analysis, but could not determine a detailed result from the available data."

    chat_history = list(state.get("chat_history") or [])
    user_message = str(state.get("user_message") or "").strip()
    if user_message:
        chat_history.append({"role": "user", "content": user_message})
    chat_history.append({"role": "assistant", "content": text})

    return {"assistant_text": text, "chat_history": chat_history}

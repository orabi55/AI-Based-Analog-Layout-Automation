"""
Graph State Utilities
=====================
Shared helpers for reading and transforming LangGraph state snapshots.

Functions:
- build_initial_agent_trace:
    - Role: Build a compact, serialisable record of what initial-placement
      agents decided, for use by the session chatbot in later chat turns.
    - Inputs:  state (dict) – any LayoutState snapshot (may be partial).
    - Outputs: dict with keys topology, strategy, placement, routing, drc.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def build_initial_agent_trace(state: Dict[str, Any]) -> Dict[str, Any]:
    """Return a compact trace of what the initial-placement agents decided.

    All fields are read with ``.get()`` so the function never crashes when
    the state is empty or only partially populated (e.g. if placement was
    interrupted before routing or DRC ran).

    The returned dict is intentionally lightweight: it references the same
    list/dict objects that are already in *state*, so callers that need an
    independent copy should call ``copy.deepcopy`` on the result.

    Args:
        state: A LayoutState dict (or any plain dict).  Missing keys are
               silently replaced with ``None`` / empty defaults.

    Returns:
        A dict with the following top-level keys:

        topology  – value of ``Analysis_result``
        strategy  – value of ``strategy_result``
        placement – dict with original_placement_cmds, placement_nodes,
                    and deterministic_snapshot
        routing   – value of ``routing_result``
        drc       – dict with pass, flags, and retry_count
    """
    return {
        "topology": state.get("Analysis_result"),
        "strategy": state.get("strategy_result"),
        "placement": {
            "original_placement_cmds": state.get("original_placement_cmds") or [],
            "placement_nodes":         state.get("placement_nodes") or [],
            "deterministic_snapshot":  state.get("deterministic_snapshot") or [],
        },
        "routing": state.get("routing_result") or {},
        "drc": {
            "pass":        state.get("drc_pass"),
            "flags":       state.get("drc_flags") or [],
            "retry_count": state.get("drc_retry_count") or 0,
        },
    }

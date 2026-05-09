"""
Layout State Persistence
========================
Shared on-disk artifact connecting the placement pipeline output to the
three downstream entry points: the chatbot (chat_panel), the MCP server,
and manual editing in Custom Compiler.

A single ``layout_state.json`` at the project root is the rendezvous point.

Functions
---------
save_layout_state   — write pipeline output to JSON; raises on non-serializable data
load_layout_state   — read JSON; returns {} on missing/malformed (logs a warning)
state_exists        — non-empty file check
clear_layout_state  — delete the file (called when starting a brand-new design)
"""

from __future__ import annotations

import json
import os
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("ai_agent")

# Constants pinned by the schema contract
PIPELINE_VERSION: str = "1.0"
DEFAULT_PATH: str     = "layout_state.json"
FLOAT_PRECISION: int  = 6


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _round_floats(obj: Any) -> Any:
    """Recursively round every float to FLOAT_PRECISION decimal places."""
    if isinstance(obj, float):
        return round(obj, FLOAT_PRECISION)
    if isinstance(obj, dict):
        return {k: _round_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_floats(item) for item in obj]
    if isinstance(obj, tuple):
        return [_round_floats(item) for item in obj]
    return obj


def _validate_node_serializable(node: dict) -> None:
    """Raise ValueError naming the offending field if any value is not JSON-safe.

    Walks every top-level field individually so the error message can identify
    the exact field that broke serialization.
    """
    nid = node.get("id", "?")
    for field, value in node.items():
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            raise ValueError(
                f"Node {nid} field {field!r} is not serializable"
            )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save_layout_state(state: dict, path: str = DEFAULT_PATH) -> None:
    """Serialize the placement pipeline state to a pretty-printed JSON file.

    Persists:
      pipeline_version, saved_at, pdk_name, nodes (sorted by id),
      groups, terminal_nets, violations, drc_pass,
      handoff_report (only when present in *state*).

    All floats are rounded to FLOAT_PRECISION decimal places.

    Raises:
        ValueError: if any node contains non-serializable data.
                    Message format:
                    "Node <id> field '<field>' is not serializable"
    """
    if state is None:
        state = {}

    # Prefer explicit "nodes"; fall back to LangGraph "placement_nodes" key
    raw_nodes = state.get("nodes")
    if not raw_nodes:
        raw_nodes = state.get("placement_nodes") or []
    if not isinstance(raw_nodes, list):
        raise ValueError(f"nodes must be a list, got {type(raw_nodes).__name__}")

    # Validate every node *before* writing — never silently drop data
    for node in raw_nodes:
        if not isinstance(node, dict):
            raise ValueError(f"Node entry is not a dict: {type(node).__name__}")
        _validate_node_serializable(node)

    sorted_nodes = sorted(raw_nodes, key=lambda n: str(n.get("id", "")))

    # The LangGraph state uses "drc_flags"; the on-disk schema uses "violations".
    raw_violations = state.get("violations")
    if raw_violations is None:
        raw_violations = state.get("drc_flags") or []

    payload = {
        "pipeline_version": PIPELINE_VERSION,
        "saved_at":         datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pdk_name":         state.get("pdk_name", "saed14nm"),
        "nodes":            _round_floats(sorted_nodes),
        "groups":           _round_floats(state.get("groups") or []),
        "terminal_nets":    _round_floats(state.get("terminal_nets") or {}),
        "violations":       _round_floats(raw_violations),
        "drc_pass":         bool(state.get("drc_pass", False)),
    }
    if "handoff_report" in state:
        payload["handoff_report"] = _round_floats(state["handoff_report"])

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


def load_layout_state(path: str = DEFAULT_PATH) -> dict:
    """Load and parse the JSON file at *path*.

    Returns:
        Parsed payload dict on success.
        Empty dict {} on missing or malformed file (a warning is logged).
        Never raises.
    """
    if not os.path.exists(path):
        logger.warning("[layout_state] file not found: %s", path)
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[layout_state] failed to load %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        logger.warning(
            "[layout_state] expected dict at top-level of %s, got %s",
            path, type(data).__name__,
        )
        return {}
    return data


def state_exists(path: str = DEFAULT_PATH) -> bool:
    """Return True iff *path* exists and is non-empty."""
    try:
        return os.path.exists(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def clear_layout_state(path: str = DEFAULT_PATH) -> None:
    """Delete the saved-state file. No-op if it does not exist."""
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as exc:
        logger.warning("[layout_state] failed to delete %s: %s", path, exc)

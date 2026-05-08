"""
DRC Checker Node (Read-Only)
============================
A lightweight LangGraph node that runs a DRC check without applying fixes or
mutating placement.  Used for the ``need_drc`` (read-only) session route.

For the ``fix_drc`` route (active fixing), see :mod:`ai_agent.nodes.drc_critic`.

Functions:
- node_drc_checker:
    - Role:  Run DRC validation and produce a summary; never mutates placement.
    - Inputs:  state (dict)
    - Outputs: state update with ``drc_pass``, ``drc_flags``, ``assistant_text``.
"""

from __future__ import annotations

import time
import re
from ai_agent.agents.drc_critic import run_drc_check
from ai_agent.utils.logging import vprint


# ---------------------------------------------------------------------------
# Shared DRC text formatter (also importable by drc_critic for Fix 4)
# ---------------------------------------------------------------------------

def _flag_text(flag) -> str:
    if isinstance(flag, dict):
        return str(
            flag.get("text")
            or flag.get("description")
            or flag.get("message")
            or flag.get("value")
            or ""
        )
    return str(getattr(flag, "text", "") or flag)


def _flag_value(flag, key: str):
    if isinstance(flag, dict):
        return flag.get(key)
    return getattr(flag, key, None)


def format_drc_flags(flags: list, max_items: int = 10) -> str:
    """Format structured DRC flags without leaking raw Python dicts."""
    if not flags:
        return "No DRC violations found."

    lines: list[str] = []
    for idx, flag in enumerate(flags[:max_items], start=1):
        text = _flag_text(flag)
        kind = str(_flag_value(flag, "kind") or "").strip()
        if not kind and text:
            kind = text.split(":", 1)[0].strip()
        kind = kind or "DRC"

        dev_a = str(_flag_value(flag, "dev_a") or "").strip()
        dev_b = str(_flag_value(flag, "dev_b") or "").strip()
        if not (dev_a and dev_b):
            m_pair = re.search(
                r"\b([A-Za-z]{1,5}\d+(?:_[A-Za-z0-9]+)?)\s+(?:vs|->|→)\s+([A-Za-z]{1,5}\d+(?:_[A-Za-z0-9]+)?)",
                text,
            )
            if m_pair:
                dev_a, dev_b = m_pair.group(1), m_pair.group(2)

        if dev_a and dev_b:
            verb = "overlaps" if kind.upper().startswith("OVERLAP") else "conflicts with"
            pair_text = f"{dev_a} {verb} {dev_b}"
        elif dev_a:
            pair_text = dev_a
        else:
            pair_text = text.split("  ", 1)[0].split(" MOVE ", 1)[0].strip() or "violation"

        nums: list[float] = []
        for key in ("x1_a", "x2_a", "x1_b", "x2_b"):
            try:
                nums.append(float(_flag_value(flag, key)))
            except (TypeError, ValueError):
                pass
        location = f" near x={min(nums):.3f}-{max(nums):.3f}" if nums else ""

        suggestion = ""
        m_move = re.search(
            r"MOVE\s+([A-Za-z]{1,5}\d+(?:_[A-Za-z0-9]+)?)\s+to\s+x=([-+]?\d+(?:\.\d+)?),\s*y=([-+]?\d+(?:\.\d+)?)",
            text,
            flags=re.IGNORECASE,
        )
        if m_move:
            suggestion = (
                f" Suggested fix: move {m_move.group(1)} to "
                f"x={float(m_move.group(2)):.3f}, y={float(m_move.group(3)):.3f}."
            )

        lines.append(f"{idx}. {kind}: {pair_text}{location}.{suggestion}")

    if len(flags) > max_items:
        lines.append(f"... and {len(flags) - max_items} more.")
    return "\n".join(lines)

def _format_drc_assistant_text(
    drc_pass: bool,
    drc_flags: list,
    *,
    prefix: str = "",
) -> str:
    """Build a human-readable assistant_text from DRC results.

    Args:
        drc_pass:  True if the DRC check passed with no violations.
        drc_flags: List of structured violation dicts (or DRCViolation objects).
        prefix:    Optional leading text (e.g. ``"Read-only "``).

    Returns:
        A markdown-formatted string suitable for the chat panel.
    """
    if drc_pass:
        return f"{prefix}DRC check passed — no violations found."

    if drc_flags:
        return (
            f"{prefix}DRC check found {len(drc_flags)} violation(s):\n"
            + format_drc_flags(drc_flags, max_items=10)
        )

    return f"{prefix}DRC check completed."


# ---------------------------------------------------------------------------
# Node implementation
# ---------------------------------------------------------------------------

def node_drc_checker(state: dict) -> dict:
    """Read-only DRC check — reports violations, never fixes them.

    This node:
      * Calls :func:`run_drc_check` to detect overlaps, gaps, and row errors.
      * Produces ``drc_pass``, ``drc_flags``, and ``assistant_text``.
      * Does **not** modify ``placement_nodes`` or ``pending_cmds``.
      * Does **not** invoke the LLM.

    Used by the ``need_drc`` session route.
    """
    t0 = time.time()
    vprint("[DRC-CHECKER] Running read-only DRC check…", flush=True)

    nodes       = state.get("placement_nodes", [])
    gap_px      = state.get("gap_px", 0.0)
    PIXELS_PER_UM = 34.0
    gap_um      = gap_px / PIXELS_PER_UM if gap_px > 0 else 0.0

    drc_result = run_drc_check(nodes, gap_um)

    # Build structured flags (same normalisation as node_drc_critic)
    structured_flags: list[dict] = []
    for v in drc_result.get("structured", []):
        if isinstance(v, dict):
            structured_flags.append(v)
        elif hasattr(v, "__slots__"):
            structured_flags.append(
                {slot: getattr(v, slot, None) for slot in v.__slots__}
            )
        elif hasattr(v, "__dict__"):
            structured_flags.append(dict(v.__dict__))
        else:
            structured_flags.append({"value": str(v)})

    assistant_text = _format_drc_assistant_text(
        drc_result["pass"], structured_flags,
    )

    elapsed = time.time() - t0
    vprint(
        f"[DRC-CHECKER] Done in {elapsed:.2f}s — pass={drc_result['pass']}  "
        f"violations={len(structured_flags)}",
        flush=True,
    )

    return {
        "drc_pass":       drc_result["pass"],
        "drc_flags":      structured_flags,
        "assistant_text": assistant_text,
        "last_agent":     "drc_checker",
    }

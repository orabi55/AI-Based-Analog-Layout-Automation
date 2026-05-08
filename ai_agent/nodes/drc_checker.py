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
from ai_agent.agents.drc_critic import run_drc_check
from ai_agent.utils.logging import vprint


# ---------------------------------------------------------------------------
# Shared DRC text formatter (also importable by drc_critic for Fix 4)
# ---------------------------------------------------------------------------

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
        lines: list[str] = []
        for v in drc_flags[:10]:
            if isinstance(v, dict):
                desc = (
                    v.get("description")
                    or v.get("message")
                    or v.get("value")
                    or str(v)
                )
            elif hasattr(v, "__slots__"):
                desc = getattr(v, "text", None) or str(v)
            else:
                desc = str(v)
            lines.append(f"- {desc}")
        if len(drc_flags) > 10:
            lines.append(f"- … and {len(drc_flags) - 10} more.")
        return (
            f"{prefix}DRC check found {len(drc_flags)} violation(s):\n"
            + "\n".join(lines)
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

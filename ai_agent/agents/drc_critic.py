"""
DRC Critic Agent — Advanced Analog Layout Verification Engine
=============================================================
Implements three major algorithmic upgrades over the original O(N²) baseline:

1. SWEEP-LINE OVERLAP DETECTION  O(N log N + R)
2. DYNAMIC GAP COMPUTATION  (Yield-Limiting Constraints)
3. COST-DRIVEN LEGALIZER WITH SYMMETRY PRESERVATION

Functions:
- _shared_potential: Checks if two devices share an equipotential net.
- _effective_gap: Calculates the required gap between two devices based on connectivity.
- _group_of: Retrieves the matched-group for a given device ID.
- _sweep_line_overlaps: Detects overlapping bounding boxes using a sweep-line algorithm.
- run_drc_check: Main entry point for DRC validation of a placement.
- _move_cost: Calculates the cost of moving a device to a candidate position.
- compute_prescriptive_fixes: Generates corrective move commands for DRC violations.
- format_drc_violations_for_llm: Formats DRC results for inclusion in an LLM prompt.

NOTE: run_drc_check and compute_prescriptive_fixes now live in ai_agent.core.drc.
      This module re-exports them for backwards compatibility.
"""

from __future__ import annotations

from typing import Dict

# Re-export canonical implementations from core
from ai_agent.core.drc import (
    DRCViolation,
    run_drc_check,
    compute_prescriptive_fixes,
)

__all__ = [
    "DRC_CRITIC_PROMPT",
    "DRCViolation",
    "run_drc_check",
    "compute_prescriptive_fixes",
    "format_drc_violations_for_llm",
]

# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------
DRC_CRITIC_PROMPT = """\
ROLE:
You are a DRC (Design Rule Check) Critic for 14nm FinFET analog layout.
Your job is to fix ALL placement violations by emitting corrected [CMD] blocks.

CONTEXT:
You are Stage 3 in a pipeline:
1) Topology Analyst
2) Placement Specialist
3) DRC Critic (YOU)
4) Routing Pre-Viewer

INPUT:
You receive a violation report.
Each violation includes a PRESCRIPTIVE FIX using exact coordinates (after "→").
Matched-pair group moves are labelled [GROUP MOVE] — apply the SAME delta to all.

CORE RULES (STRICT):
- NEVER invent coordinates.
- ALWAYS use the exact x/y values provided in the violation report.
- Fix EVERY violation (no skipping).
- Do NOT introduce new violations.
- PMOS must stay in PMOS rows (higher y).
- NMOS must stay in NMOS rows (lower y).
- If a violation lists a GROUP MOVE, emit one [CMD] per device in the group.

PROCEDURE:
For each violation:
1) OVERLAP  → Move the nominated device (or group) to the prescribed x.
2) GAP      → Move device B to the prescribed x.
3) ROW_ERROR→ Move the device to the prescribed y.
4) CASCADE  → If a move causes a new overlap, fix that device too.

OUTPUT FORMAT (STRICT):
- Output ONLY [CMD] blocks, then ONE summary line.
- No explanations. No markdown. No extra text.
- Each block must be valid JSON on ONE line.

FORMAT:
[CMD]{"action":"move","device":"MM1","x":0.588,"y":-0.823}[/CMD]

CONSTRAINTS:
- Max commands = 3 × number of violations
- Do NOT repeat unchanged commands
- Do NOT ask questions

FINAL CHECK (before output):
- Every violation is fixed
- All coordinates match the report exactly
- No new overlaps introduced
- Device types remain in correct rows
- Symmetry within matched groups preserved

OUTPUT:
[CMD] blocks first
Then one-line summary
"""


# ---------------------------------------------------------------------------
# LLM formatting helper
# ---------------------------------------------------------------------------

def format_drc_violations_for_llm(drc_result: Dict, prior_cmds_text: str = "") -> str:
    """Format run_drc_check output into an LLM prompt snippet.

    Includes prescriptive geometry hints (with GROUP MOVE annotations)
    and the prior failed CMDs for context-preserving retry.
    """
    if drc_result["pass"]:
        return "DRC: All clear – no violations detected."

    lines = [
        f"═══ DRC VIOLATIONS ({len(drc_result['violations'])} found) ═══",
        "Each entry includes a PRESCRIPTIVE FIX with exact coordinates.",
        "Entries marked [GROUP MOVE] require the same Δ applied to all listed devices.",
        "",
    ]
    lines.extend(f"  [{i}] {v}" for i, v in enumerate(drc_result["violations"], 1))

    if prior_cmds_text.strip():
        lines.append("")
        lines.append("═══ PRIOR FAILED [CMD] BLOCKS (context — do NOT repeat unchanged) ═══")
        lines.append(prior_cmds_text.strip()[:2000])

    lines.append("")
    lines.append("Use the exact x/y values from the prescriptive hints above in your [CMD] blocks.")
    return "\n".join(lines)

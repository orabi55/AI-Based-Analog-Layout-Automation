"""
abutment_engine.py — Transistor abutment candidate finder.

Purpose
-------
When the user presses the "Abut" button, this engine:
  1.  Scans ALL transistor pairs in the netlist (regardless of position).
  2.  Finds pairs that share the same Source or Drain net (same-type only:
      NMOS-NMOS or PMOS-PMOS).
  3.  Reports each candidate pair with the matching terminal on each side,
      and whether the right device needs to be H-flipped so the matching
      terminal faces the shared edge.

The candidates are then used for:
  - Visual highlighting (green glow on compatible terminal edges in the GUI).
  - AI placement constraints (the AI is told to place abutment candidates
    adjacent to each other so diffusion can be shared).

PDK note (SAED 14nm)
---------------------
Abutment is encoded as leftAbut / rightAbut flags on the PCell — the x/y
positions do NOT change.  The PCell internally removes the end-cap diffusion
on the flagged side so two adjacent cells share one diffusion strip.

Candidate data format
---------------------
Each candidate is a dict:
{
    "dev_a":        str,        # device id
    "term_a":       "S"|"D",   # which terminal of dev_a is shared
    "dev_b":        str,        # device id
    "term_b":       "S"|"D",   # which terminal of dev_b is shared
    "shared_net":   str,        # the net name connecting them
    "type":         "nmos"|"pmos",
    "needs_flip":   bool,       # True => dev_b should be H-flipped to align
}
"""

from __future__ import annotations
from itertools import combinations


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _sd_nets(dev_id: str, terminal_nets: dict) -> tuple[str | None, str | None]:
    """Return (source_net, drain_net) for dev_id."""
    nets = terminal_nets.get(dev_id, {})
    return nets.get("S") or None, nets.get("D") or None


# ──────────────────────────────────────────────────────────────────────────────
# Main API
# ──────────────────────────────────────────────────────────────────────────────

def find_abutment_candidates(nodes: list, terminal_nets: dict) -> list:
    """Find all transistor pairs that can share a diffusion (abutment candidates).

    Checks every same-type (NMOS-NMOS or PMOS-PMOS) pair for a shared S/D net.

    Shared terminal cases:
      dev_a.S == dev_b.S  → flip dev_b  (both Sources align if dev_b mirrored)
      dev_a.S == dev_b.D  → no flip     (dev_a.S left edge ↔ dev_b.D right edge)
      dev_a.D == dev_b.S  → no flip     (dev_a.D right edge ↔ dev_b.S left edge)
      dev_a.D == dev_b.D  → flip dev_b  (Drains align if dev_b mirrored)

    Args:
        nodes:         list of node dicts [{\"id\", \"type\", ...}, ...]
        terminal_nets: {dev_id: {\"S\": net, \"D\": net, \"G\": net}}

    Returns:
        list of candidate dicts (see module docstring).
    """
    candidates = []

    transistors = [n for n in nodes
                   if n.get("type") in ("nmos", "pmos")]

    for node_a, node_b in combinations(transistors, 2):
        id_a = node_a["id"]
        id_b = node_b["id"]
        type_a = node_a["type"]
        type_b = node_b["type"]

        # Only same-type pairs can share diffusion
        if type_a != type_b:
            continue

        s_a, d_a = _sd_nets(id_a, terminal_nets)
        s_b, d_b = _sd_nets(id_b, terminal_nets)

        if not (s_a or d_a) or not (s_b or d_b):
            continue  # missing net info

        found = []

        # Case 1: dev_a.S == dev_b.S  → flip dev_b
        if s_a and s_b and s_a == s_b:
            # After flip: dev_b left becomes Drain, right becomes Source
            # So dev_a's left Source can abut with dev_b's flipped right Source
            found.append({
                "dev_a": id_a, "term_a": "S",
                "dev_b": id_b, "term_b": "S",
                "shared_net": s_a,
                "type": type_a,
                "needs_flip": True,
            })

        # Case 2: dev_a.S == dev_b.D  → no flip
        if s_a and d_b and s_a == d_b:
            # dev_b's Drain (right) abuts dev_a's Source (left)
            # In layout: place dev_b LEFT of dev_a
            found.append({
                "dev_a": id_b, "term_a": "D",
                "dev_b": id_a, "term_b": "S",
                "shared_net": s_a,
                "type": type_a,
                "needs_flip": False,
            })

        # Case 3: dev_a.D == dev_b.S  → no flip
        if d_a and s_b and d_a == s_b:
            # dev_a's Drain (right) abuts dev_b's Source (left)
            # In layout: place dev_a LEFT of dev_b
            found.append({
                "dev_a": id_a, "term_a": "D",
                "dev_b": id_b, "term_b": "S",
                "shared_net": d_a,
                "type": type_a,
                "needs_flip": False,
            })

        # Case 4: dev_a.D == dev_b.D  → flip dev_b
        if d_a and d_b and d_a == d_b:
            found.append({
                "dev_a": id_a, "term_a": "D",
                "dev_b": id_b, "term_b": "D",
                "shared_net": d_a,
                "type": type_a,
                "needs_flip": True,
            })

        # Deduplicate (same pair, same net can appear in multiple cases)
        for c in found:
            duplicate = any(
                e["dev_a"] == c["dev_a"] and e["dev_b"] == c["dev_b"]
                and e["shared_net"] == c["shared_net"]
                for e in candidates
            )
            if not duplicate:
                candidates.append(c)

    return candidates


def format_candidates_for_prompt(candidates: list) -> str:
    """Format abutment candidates as a human-readable block for the AI prompt."""
    if not candidates:
        return "None detected."

    lines = []
    for c in candidates:
        flip_note = " [flip B]" if c["needs_flip"] else ""
        lines.append(
            f"  - {c['dev_a']} ({c['term_a']}) abutts {c['dev_b']} ({c['term_b']})"
            f"  via net '{c['shared_net']}'{flip_note}"
        )
    return "\n".join(lines)


def build_edge_highlight_map(candidates: list) -> dict:
    """Build a per-device highlight map: {dev_id: {side: net}} where side is
    'left' or 'right'.

    Used by the editor to know which edge of each device to glow.

    Convention:
      - term 'S' on a normal device maps to left edge
      - term 'D' on a normal device maps to right edge
      - If needs_flip=True, the edges are reversed for dev_b
    """
    highlights: dict = {}   # {dev_id: {"left": net, "right": net}}

    for c in candidates:
        def _add(dev_id, term, flipped, net):
            # Determine which physical edge this terminal is on
            if not flipped:
                edge = "left" if term == "S" else "right"
            else:
                edge = "right" if term == "S" else "left"
            highlights.setdefault(dev_id, {})
            highlights[dev_id][edge] = net

        _add(c["dev_a"], c["term_a"], False,             c["shared_net"])
        _add(c["dev_b"], c["term_b"], c["needs_flip"],   c["shared_net"])

    return highlights

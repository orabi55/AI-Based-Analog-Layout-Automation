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

_POWER_NETS = frozenset({"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS"})

def _sd_nets(dev_id: str, terminal_nets: dict) -> tuple[str | None, str | None]:
    """Return (source_net, drain_net) for dev_id, ignoring power nets."""
    nets = terminal_nets.get(dev_id, {})
    s = nets.get("S")
    d = nets.get("D")
    
    # Filter out power nets
    s_ret = s if (s and s.upper() not in _POWER_NETS) else None
    d_ret = d if (d and d.upper() not in _POWER_NETS) else None
    
    return s_ret, d_ret


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

        parent_a = node_a.get("electrical", {}).get("parent") or id_a.split("_f")[0]
        parent_b = node_b.get("electrical", {}).get("parent") or id_b.split("_f")[0]

        found = []
        is_same_parent = (parent_a == parent_b)

        if is_same_parent:
            # Multi-finger sequential chain: only abut strictly consecutive fingers
            idx_a = int(id_a.split("_f")[-1]) if "_f" in id_a else 1
            idx_b = int(id_b.split("_f")[-1]) if "_f" in id_b else 1
            
            if abs(idx_a - idx_b) != 1:
                continue  # Skip non-consecutive fingers to avoid overlapping/O(N^2)

            # Order so that lo is the lower-index finger, hi is the higher-index
            if idx_a < idx_b:
                lo_id, hi_id = id_a, id_b
            else:
                lo_id, hi_id = id_b, id_a

            # Fetch actual terminal nets for both fingers (including power nets)
            lo_nets = terminal_nets.get(lo_id, {})
            hi_nets = terminal_nets.get(hi_id, {})
            lo_s, lo_d = lo_nets.get("S"), lo_nets.get("D")
            hi_s, hi_d = hi_nets.get("S"), hi_nets.get("D")

            # Check all four terminal combinations for a shared net
            # The right edge of the lo finger faces the left edge of the hi finger
            if lo_d and hi_s and lo_d == hi_s:
                found.append({"dev_a": lo_id, "term_a": "D", "dev_b": hi_id, "term_b": "S", "shared_net": lo_d, "type": type_a, "needs_flip": False})
            elif lo_d and hi_d and lo_d == hi_d:
                found.append({"dev_a": lo_id, "term_a": "D", "dev_b": hi_id, "term_b": "D", "shared_net": lo_d, "type": type_a, "needs_flip": True})
            elif lo_s and hi_s and lo_s == hi_s:
                found.append({"dev_a": lo_id, "term_a": "S", "dev_b": hi_id, "term_b": "S", "shared_net": lo_s, "type": type_a, "needs_flip": True})
            elif lo_s and hi_d and lo_s == hi_d:
                found.append({"dev_a": lo_id, "term_a": "S", "dev_b": hi_id, "term_b": "D", "shared_net": lo_s, "type": type_a, "needs_flip": False})
        else:
            # Cross-parent checks. To prevent massive identical arrays for cross-parent (e.g. all 16 fingers sharing VDD),
            # we only attempt to abut the LAST finger of dev_a with the FIRST finger of dev_b
            # (or vice-versa, assuming a simple block-to-block layout sequence).
            idx_a = int(id_a.split("_f")[-1]) if "_f" in id_a else 1
            idx_b = int(id_b.split("_f")[-1]) if "_f" in id_b else 1
            
            # Allow cross-parent connection roughly between boundary fingers
            # For simplicity, if both are f1, or if we want them to link end-to-end, we just take the first matching case.
            # Here we enforce a strict 1 valid case selection to prevent prompt conflicts.
            if s_a and s_b and s_a == s_b:
                found.append({"dev_a": id_a, "term_a": "S", "dev_b": id_b, "term_b": "S", "shared_net": s_a, "type": type_a, "needs_flip": True})
            elif s_a and d_b and s_a == d_b:
                found.append({"dev_a": id_b, "term_a": "D", "dev_b": id_a, "term_b": "S", "shared_net": s_a, "type": type_a, "needs_flip": False})
            elif d_a and s_b and d_a == s_b:
                found.append({"dev_a": id_a, "term_a": "D", "dev_b": id_b, "term_b": "S", "shared_net": d_a, "type": type_a, "needs_flip": False})
            elif d_a and d_b and d_a == d_b:
                found.append({"dev_a": id_a, "term_a": "D", "dev_b": id_b, "term_b": "D", "shared_net": d_a, "type": type_a, "needs_flip": True})
                
            # If cross-parent, we randomly got ONE hit. To prevent the permutation explosion
            # (16x16 = 256 hits between MM1 and MM2), we only keep it if the AI hasn't already been given an abutment for this parent pair!
            if found:
                already_linked = any(
                    (c["dev_a"].split("_f")[0] == parent_a and c["dev_b"].split("_f")[0] == parent_b) or
                    (c["dev_b"].split("_f")[0] == parent_a and c["dev_a"].split("_f")[0] == parent_b)
                    for c in candidates
                )
                if already_linked:
                    found = [] # Discard extra cross-parent links

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

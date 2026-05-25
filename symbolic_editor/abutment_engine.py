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
    """Return (source_net, drain_net) for dev_id, including power nets."""
    nets = terminal_nets.get(dev_id, {})
    s = nets.get("S")
    d = nets.get("D")
    
    return s if s else None, d if d else None


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
        nodes:         list of node dicts [{"id", "type", ...}, ...]
        terminal_nets: {dev_id: {"S": net, "D": net, "G": net}}

    Returns:
        list of candidate dicts (see module docstring).
    """
    candidates = []

    transistors = [n for n in nodes if n.get("type") in ("nmos", "pmos")]
    
    # 1. Build a net index: net_name -> dict(type -> list of (dev_id, parent, f_idx, terminal))
    net_index = {}
    for node in transistors:
        dev_id = node["id"]
        dev_type = node["type"]
        parent = node.get("electrical", {}).get("parent") or dev_id.split("_f")[0]
        
        try:
            f_idx = int(dev_id.split("_f")[-1]) if "_f" in dev_id else 1
        except ValueError:
            f_idx = 1

        # Check node's current nets first (which may be logically swapped)
        s_net = node.get("net_s")
        d_net = node.get("net_d")
        if not s_net or not d_net:
            # Fallback to default schematic terminal nets
            s_net, d_net = _sd_nets(dev_id, terminal_nets)
        
        if s_net:
            type_dict = net_index.setdefault(s_net, {})
            type_list = type_dict.setdefault(dev_type, [])
            type_list.append((dev_id, parent, f_idx, "S"))
            
        if d_net:
            type_dict = net_index.setdefault(d_net, {})
            type_list = type_dict.setdefault(dev_type, [])
            type_list.append((dev_id, parent, f_idx, "D"))

    # 2. Find pairs within each net group
    seen_cross_parent = set()
    seen_candidates = set()

    for net_name, type_dict in net_index.items():
        for dev_type, devices in type_dict.items():
            # Check all pairs in this small list
            for i in range(len(devices)):
                for j in range(i + 1, len(devices)):
                    id_a, p_a, idx_a, term_a = devices[i]
                    id_b, p_b, idx_b, term_b = devices[j]
                    
                    if id_a == id_b:
                        continue # Same device, different terminals (e.g. S and D shorted)
                        
                    is_same_parent = (p_a == p_b)
                    
                    if is_same_parent:
                        if abs(idx_a - idx_b) != 1:
                            continue # Only abut strictly consecutive fingers
                        
                        # Order so lo_id is lower index
                        if idx_a < idx_b:
                            lo_id, hi_id = id_a, id_b
                            t_lo, t_hi = term_a, term_b
                        else:
                            lo_id, hi_id = id_b, id_a
                            t_lo, t_hi = term_b, term_a
                            
                        # Determine if flip is needed
                        # The right edge of lo finger faces left edge of hi finger
                        if t_lo == "D" and t_hi == "S":
                            needs_flip = False
                        elif t_lo == "D" and t_hi == "D":
                            needs_flip = True
                        elif t_lo == "S" and t_hi == "S":
                            needs_flip = True
                        elif t_lo == "S" and t_hi == "D":
                            needs_flip = False
                        else:
                            continue # Should not happen

                        cand_key = (lo_id, hi_id, net_name)
                        if cand_key not in seen_candidates:
                            seen_candidates.add(cand_key)
                            candidates.append({
                                "dev_a": lo_id, "term_a": t_lo, 
                                "dev_b": hi_id, "term_b": t_hi, 
                                "shared_net": net_name, "type": dev_type, "needs_flip": needs_flip
                            })
                            
                    else:
                        # Cross parent check
                        parent_pair = tuple(sorted([p_a, p_b]))
                        if parent_pair in seen_cross_parent:
                            continue # Already have a link for these two parents
                            
                        seen_cross_parent.add(parent_pair)
                        
                        if term_a == "S" and term_b == "S":
                            needs_flip = True
                        elif term_a == "S" and term_b == "D":
                            # swap order so D is term_a and S is term_b as per original logic preference
                            id_a, id_b = id_b, id_a
                            term_a, term_b = term_b, term_a
                            needs_flip = False
                        elif term_a == "D" and term_b == "S":
                            needs_flip = False
                        elif term_a == "D" and term_b == "D":
                            needs_flip = True
                        else:
                            continue

                        cand_key = (id_a, id_b, net_name)
                        if cand_key not in seen_candidates:
                            seen_candidates.add(cand_key)
                            candidates.append({
                                "dev_a": id_a, "term_a": term_a, 
                                "dev_b": id_b, "term_b": term_b, 
                                "shared_net": net_name, "type": dev_type, "needs_flip": needs_flip
                            })

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

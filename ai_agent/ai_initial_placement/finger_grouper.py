"""
finger_grouper.py
=================
Pre- and post-processing stage for the AI placer that collapses individual
finger/multiplier nodes into compact transistor-level representations before
sending to the LLM, then expands them back to finger-level coordinates
afterward.

Why this matters
----------------
A comparator with nf=6, m=8 produces 6×8 = 48 individual finger nodes for
ONE logical transistor bus.  Sending ~190 finger nodes to the LLM causes:
  • Token-limit truncation → missing devices in the output
  • No abutment chain knowledge → fingers scattered randomly
  • No differential-pair / matching awareness

The fix is:
  1. ``group_fingers``   : 190 nodes → ~12 transistor groups
  2. LLM places 12 groups (transistor level)
  3. ``expand_groups``   : 12 groups → 190 finger nodes with correct 0.070 µm
                           abutment spacing, PMOS/NMOS rows, and matching layout
"""

from __future__ import annotations

import re
import copy
from collections import defaultdict
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PMOS_Y       = 0.668   # µm — default initial PMOS row (multi-row: any multiple of ROW_PITCH above NMOS rows)
NMOS_Y       = 0.000   # µm — default initial NMOS row (multi-row: any multiple of ROW_PITCH)
ROW_PITCH    = 0.668   # µm — standard row-to-row pitch
FINGER_PITCH = 0.070   # µm — abutted finger-to-finger pitch
STD_PITCH    = 0.294   # µm — non-abutted device pitch (diffusion break)

# Regex: split "MM9<3>_f4" → ("MM9", "3", "4")
_BUS_RE   = re.compile(r'^(.+?)<(\d+)>(?:_f(\d+))?$')
# Regex: split "MM5_f2"    → ("MM5", None, "2")
_PLAIN_RE = re.compile(r'^(.+?)_f(\d+)$')

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_id(node_id: str) -> Tuple[str, int | None, int | None]:
    """
    Parse a node id into (parent_name, bus_index, finger_index).

    Examples
    --------
    "MM9<3>_f4"  → ("MM9", 3, 4)
    "MM9<3>"     → ("MM9", 3, None)   ← single-finger bus member
    "MM5_f2"     → ("MM5", None, 2)
    "MM5"        → ("MM5", None, None) ← single-device, no fingers
    """
    m = _BUS_RE.match(node_id)
    if m:
        parent = m.group(1)
        bus_idx = int(m.group(2))
        finger_idx = int(m.group(3)) if m.group(3) else None
        return parent, bus_idx, finger_idx

    m = _PLAIN_RE.match(node_id)
    if m:
        return m.group(1), None, int(m.group(2))

    return node_id, None, None


def _transistor_key(node_id: str) -> str:
    """
    Return the logical transistor name for a finger node.

    "MM9<3>_f4" → "MM9"   (all multiplier copies + all fingers share one key)
    "MM5_f2"    → "MM5"
    "MM6_f1"    → "MM6"
    """
    parent, _, _ = _parse_id(node_id)
    return parent


# ---------------------------------------------------------------------------
# Public API — Step 1: GROUP
# ---------------------------------------------------------------------------

def group_fingers(nodes: list, edges: list) -> Tuple[list, dict]:
    """
    Collapse finger-level nodes into transistor-level group nodes.

    Parameters
    ----------
    nodes : list of node dicts (from graph JSON)
    edges : list of edge dicts (from graph JSON)

    Returns
    -------
    group_nodes : list of ~N compact group dicts (one per logical transistor)
    finger_map  : dict mapping group_id → [original node dicts], preserving
                  insertion order (bus index ascending, finger index ascending)
    """
    # --- 1. Bucket every finger node under its logical transistor key --------
    buckets: Dict[str, List[dict]] = defaultdict(list)
    for n in nodes:
        key = _transistor_key(n["id"])
        buckets[key].append(n)

    # Sort each bucket: bus index asc, finger index asc
    def _sort_key(n):
        _, bus_idx, fin_idx = _parse_id(n["id"])
        return (bus_idx if bus_idx is not None else -1,
                fin_idx if fin_idx is not None else 0)

    for key in buckets:
        buckets[key].sort(key=_sort_key)

    # --- 2. Build a representative group node for the LLM --------------------
    group_nodes = []
    finger_map: Dict[str, List[dict]] = {}

    for group_id, members in buckets.items():
        rep = members[0]                       # representative finger
        rep_elec = rep.get("electrical", {})
        dev_type = rep.get("type", "nmos")

        # Count true fingers (nf per transistor instance) and multiplier copies
        # by looking at how many unique bus indices exist
        bus_indices: set[int] = set()
        fin_indices: set[int] = set()
        for n in members:
            _, bi, fi = _parse_id(n["id"])
            if bi is not None:
                bus_indices.add(bi)
            if fi is not None:
                fin_indices.add(fi)

        num_multiplier = len(bus_indices) if bus_indices else 1
        num_fingers    = len(fin_indices) if fin_indices else len(members)
        total_fingers  = len(members)     # = num_multiplier × num_fingers

        group_node = {
            "id":   group_id,
            "type": dev_type,
            "electrical": {
                "l":              rep_elec.get("l",    1.4e-8),
                "nf_per_device":  num_fingers,
                "multiplier":     num_multiplier,
                "total_fingers":  total_fingers,
                "nfin":           rep_elec.get("nfin", 2),
                "w":              rep_elec.get("w",    0),
            },
            # Width = total fingers × STD_PITCH (real non-abutted layout footprint).
            # Using FINGER_PITCH here would massively underestimate the footprint
            # (e.g. 8 fingers → 0.560 vs 2.352 µm), causing bin-packing to never
            # trigger multi-row splitting and making LLM X estimates too small.
            "geometry": {
                "x":           0.0,
                "y":           PMOS_Y if dev_type == "pmos" else NMOS_Y,
                "width":       round(total_fingers * STD_PITCH, 6),
                "height":      rep.get("geometry", {}).get("height", 0.568),
                "orientation": "R0",
            },
        }

        # Copy block membership from rep if present
        if "block" in rep:
            group_node["block"] = rep["block"]

        group_nodes.append(group_node)
        finger_map[group_id] = members

    # --- 3. Build compact edges (group → group) ------------------------------
    # We need to keep an edge if the two endpoint nodes belong to different
    # transistor groups and the net is not a power rail.
    _POWER = frozenset({"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS"})

    seen_edges: set[tuple] = set()
    group_edges = []
    id_to_group = {n["id"]: _transistor_key(n["id"]) for n in nodes}

    for e in edges:
        src_grp = id_to_group.get(e.get("source", ""))
        tgt_grp = id_to_group.get(e.get("target", ""))
        net     = e.get("net", "")
        if not src_grp or not tgt_grp or src_grp == tgt_grp:
            continue
        if net.upper() in _POWER:
            continue
        edge_key = (min(src_grp, tgt_grp), max(src_grp, tgt_grp), net)
        if edge_key in seen_edges:
            continue
        seen_edges.add(edge_key)
        group_edges.append({"source": src_grp, "target": tgt_grp, "net": net})

    return group_nodes, group_edges, finger_map


# ---------------------------------------------------------------------------
# Matching / symmetry analysis
# ---------------------------------------------------------------------------

def _electrical_signature(group_node: dict) -> tuple:
    """
    Build a hashable signature from a group node's electrical parameters.
    Two transistors with the same signature are structurally identical
    and are candidates for matched / symmetric placement.
    """
    elec = group_node.get("electrical", {})
    return (
        group_node.get("type", "nmos"),
        elec.get("nf_per_device", 1),
        elec.get("multiplier", 1),
        elec.get("total_fingers", 1),
        elec.get("l", 0),
        elec.get("nfin", 1),
    )


def detect_matching_groups(group_nodes: list, group_edges: list) -> dict:
    """
    Detect structurally matched transistor groups, differential pairs,
    and cross-coupled pairs from the compact transistor-level graph.

    Structural matching: two transistor groups are a *matched pair* if they
    share the same device type, number of fingers (nf), multiplier (m),
    gate length (L), and fin count (nfin).  This is critical for analog
    layout where matched transistors must be placed symmetrically.

    Returns a dict with:
      "matched_pairs" : list of (grpA, grpB) — structurally identical params
      "matched_clusters": list of [grpA, grpB, ...] — groups of 3+ identical
      "diff_pairs"    : list of (grpA, grpB) — reserved for net-based detection
      "cross_coupled" : list of (grpA, grpB) — reserved for net-based detection
      "tail_sources"  : list of grp_ids
    """
    # --- Structural matching: group by electrical signature ---------------
    sig_buckets: Dict[tuple, List[str]] = defaultdict(list)
    for gn in group_nodes:
        sig = _electrical_signature(gn)
        sig_buckets[sig].append(gn["id"])

    matched_pairs: List[Tuple[str, str]] = []
    matched_clusters: List[List[str]] = []

    for sig, members in sig_buckets.items():
        if len(members) < 2:
            continue
        # Store the full cluster
        matched_clusters.append(sorted(members))
        # Also emit all unique pairs for backward compatibility
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                matched_pairs.append((a, b))

    return {
        "matched_pairs":    matched_pairs,
        "matched_clusters": matched_clusters,
        "diff_pairs":       [],
        "cross_coupled":    [],
        "tail_sources":     [],
    }


def _enrich_matching_info(
        matching_info: dict,
        group_terminal_nets: dict,
        group_nodes: list,
) -> None:
    """
    Fill in the net-based ``diff_pairs``, ``cross_coupled``, and
    ``tail_sources`` fields of *matching_info* (in-place) using terminal
    net analysis.

    This is the same logic as ``build_matching_section`` but written to
    populate the data structure that ``_symmetry_order`` consumes.
    """
    grp_lookup = {n["id"]: n for n in group_nodes}
    _POWER = {"VDD", "VSS", "GND", ""}

    # --- Diff pairs (VINP / VINN) ----------------------------------------
    vinp_ids: List[str] = []
    vinn_ids: List[str] = []
    for gid, t in group_terminal_nets.items():
        g = t.get("G", "")
        if "VINP" in g.upper():
            vinp_ids.append(gid)
        elif "VINN" in g.upper():
            vinn_ids.append(gid)

    diff_pairs = []
    for a in vinp_ids:
        for b in vinn_ids:
            diff_pairs.append((a, b))
    matching_info["diff_pairs"] = diff_pairs

    # --- Cross-coupled pairs (D of A == G of B and vice versa) -----------
    cross_pairs: List[Tuple[str, str]] = []
    gids = list(group_terminal_nets.keys())
    for i, ga in enumerate(gids):
        for gb in gids[i + 1:]:
            ta = group_terminal_nets[ga]
            tb = group_terminal_nets[gb]
            if (ta.get("D") and ta.get("D") == tb.get("G") and
                    tb.get("D") and tb.get("D") == ta.get("G")):
                cross_pairs.append((ga, gb))
    matching_info["cross_coupled"] = cross_pairs

    # --- Tail sources: share a non-power S/D net with the diff pair ------
    diff_all = set(vinp_ids + vinn_ids)
    diff_source_nets = set()
    for did in diff_all:
        t = group_terminal_nets.get(did, {})
        s = t.get("S", "")
        if s and s.upper() not in _POWER:
            diff_source_nets.add(s)

    tail_ids = []
    for gid, t in group_terminal_nets.items():
        if gid in diff_all:
            continue
        d_net = t.get("D", "")
        s_net = t.get("S", "")
        if d_net in diff_source_nets or s_net in diff_source_nets:
            tail_ids.append(gid)
    matching_info["tail_sources"] = tail_ids


def build_matching_section(
        group_nodes: list,
        group_edges: list,
        group_terminal_nets: dict,
) -> str:
    """
    Build a human-readable matching/symmetry section for the LLM prompt.

    Parameters
    ----------
    group_nodes       : list of compact group node dicts
    group_edges       : list of compact group edge dicts (unused, kept for API
                        compatibility — may be used for future net analysis)
    group_terminal_nets : dict mapping group_id → {D, G, S} terminal nets
                          (pre-resolved by the caller using first-finger lookup)

    Detects:
      • Structurally matched transistors (same type, nf, m, L, nfin)
      • Differential pairs (VINP/VINN gate nets)
      • Cross-coupled latch pairs (D of A == G of B and vice-versa)
      • Known topologies (Strong-ARM latch, current mirror, etc.)
    """
    grp_terminals: Dict[str, dict] = group_terminal_nets

    lines: List[str] = []

    # --- Structural matching via detect_matching_groups() ----------------
    matching_info = detect_matching_groups(group_nodes, group_edges)
    matched_clusters = matching_info.get("matched_clusters", [])

    grp_lookup = {n["id"]: n for n in group_nodes}

    if matched_clusters:
        lines.append("STRUCTURALLY MATCHED TRANSISTORS (must be placed symmetrically):")
        lines.append("(These groups have identical type, nf, m, L, and nfin — they")
        lines.append(" represent matched transistors for symmetric analog layout.)")
        lines.append("")
        for cluster in matched_clusters:
            rep = grp_lookup.get(cluster[0], {})
            elec = rep.get("electrical", {})
            dev_type = rep.get("type", "?")
            nf = elec.get("nf_per_device", 1)
            m  = elec.get("multiplier", 1)
            lines.append(
                f"  MATCHED GROUP: {', '.join(cluster)}"
                f"  (type={dev_type}, nf={nf}, m={m})"
            )
        lines.append("")
        lines.append("  RULE: Matched transistors MUST be placed in the SAME row.")
        lines.append("  RULE: Matched transistors SHOULD be placed adjacent or")
        lines.append("        symmetrically about a vertical axis of symmetry.")
        lines.append("")

    # --- Find diff pairs: NMOS groups with same |D|, diff |G| ---
    # More reliable: gate of one = a signal net that also appears as gate of
    # its partner (VINP ↔ VINN is the classic diff pair).
    gate_net_to_groups: Dict[str, List[str]] = defaultdict(list)
    for grp_id, t in grp_terminals.items():
        g_net = t.get("G", "")
        if g_net and g_net.upper() not in {"VDD", "GND", "VSS", "CLK"}:
            gate_net_to_groups[g_net].append(grp_id)

    # Diff pair: two groups where one's gate = other's gate (simple mirror)
    # OR: two groups with VINP / VINN (classic names)
    vinp_groups: List[str] = []
    vinn_groups: List[str] = []
    for grp_id, t in grp_terminals.items():
        g = t.get("G", "")
        if "VINP" in g.upper():
            vinp_groups.append(grp_id)
        elif "VINN" in g.upper():
            vinn_groups.append(grp_id)

    if vinp_groups and vinn_groups:
        lines.append("DIFFERENTIAL PAIR (CRITICAL — must be placed symmetrically):")
        lines.append(f"  VINP side: {', '.join(sorted(vinp_groups))}")
        lines.append(f"  VINN side: {', '.join(sorted(vinn_groups))}")
        lines.append("  RULE: Place VINP group and VINN group adjacent to each other,")
        lines.append("        mirrored about a vertical axis of symmetry.")
        lines.append("  RULE: Use interdigitation (ABBA pattern) across bus copies.")
        lines.append("")

    # Cross-coupled: drain of A = gate of B AND drain of B = gate of A
    cross_pairs: List[Tuple[str, str]] = []
    pmos_cross: List[Tuple[str, str]] = []
    nmos_cross: List[Tuple[str, str]] = []
    grp_ids = list(grp_terminals.keys())
    for i, ga in enumerate(grp_ids):
        for gb in grp_ids[i+1:]:
            ta = grp_terminals[ga]
            tb = grp_terminals[gb]
            # Cross-coupled: D of A == G of B AND D of B == G of A
            if (ta.get("D") and ta.get("D") == tb.get("G") and
                    tb.get("D") and tb.get("D") == ta.get("G")):
                cross_pairs.append((ga, gb))
                ga_type = grp_lookup.get(ga, {}).get("type", "")
                if ga_type == "pmos":
                    pmos_cross.append((ga, gb))
                else:
                    nmos_cross.append((ga, gb))

    if cross_pairs:
        lines.append("CROSS-COUPLED / LATCH PAIRS (must be placed symmetrically):")
        for ga, gb in cross_pairs:
            lines.append(f"  {ga} <-> {gb}  (D of each = G of the other)")
        lines.append("  RULE: Place these pairs immediately adjacent, centered on the layout.")
        lines.append("")

    # --- CLK-gated switches: groups whose gate is CLK ---
    clk_switches: List[str] = []
    for grp_id, t in grp_terminals.items():
        g = t.get("G", "")
        if g.upper() == "CLK":
            clk_switches.append(grp_id)

    # --- Topology detection: Strong-ARM latch comparator ----------------
    has_diff_pair = bool(vinp_groups and vinn_groups)
    has_pmos_cross = len(pmos_cross) > 0
    has_nmos_cross = len(nmos_cross) > 0
    has_clk_switches = len(clk_switches) >= 2

    if has_diff_pair and (has_pmos_cross or has_nmos_cross) and has_clk_switches:
        lines.append("DETECTED TOPOLOGY: Strong-ARM Latch Comparator")
        lines.append("")
        lines.append("OPTIMAL PLACEMENT STRATEGY (follow these rules for best area/performance):")
        lines.append("")
        lines.append("  1. SYMMETRY AXIS: ALL placement must be symmetric about a vertical center line.")
        lines.append("")
        lines.append("  2. CENTER CORE: Place the cross-coupled latch pairs at the CENTER of each row:")
        if pmos_cross:
            for ga, gb in pmos_cross:
                lines.append(f"     PMOS latch: {ga} | {gb}  (center of PMOS row)")
        if nmos_cross:
            for ga, gb in nmos_cross:
                lines.append(f"     NMOS latch: {ga} | {gb}  (center of NMOS row)")
        lines.append("")
        lines.append("  3. DIFF PAIR: Place the differential input pair FLANKING the NMOS latch:")
        lines.append(f"     Left side:  {', '.join(sorted(vinp_groups))}")
        lines.append(f"     Right side: {', '.join(sorted(vinn_groups))}")
        lines.append("")
        lines.append("  4. CLK SWITCHES: Place CLK-gated devices at the OUTER EDGES, split symmetrically:")
        # Split CLK switches by type
        pmos_clk = [c for c in clk_switches if grp_lookup.get(c, {}).get("type") == "pmos"]
        nmos_clk = [c for c in clk_switches if grp_lookup.get(c, {}).get("type") == "nmos"]
        if pmos_clk:
            half = len(pmos_clk) // 2
            left_clk = pmos_clk[:half] if half > 0 else []
            right_clk = pmos_clk[half:] if half > 0 else pmos_clk
            lines.append(f"     PMOS CLK switches: {', '.join(pmos_clk)}")
            if half > 0:
                lines.append(f"       Split: {', '.join(left_clk)} on LEFT, {', '.join(right_clk)} on RIGHT")
        if nmos_clk:
            lines.append(f"     NMOS CLK switches: {', '.join(nmos_clk)}")
        lines.append("")

        # Identify tail current sources (share source nets with diff pair)
        _POWER = {"VDD", "VSS", "GND", ""}
        diff_ids = set(vinp_groups + vinn_groups)
        diff_source_nets = set()
        for did in diff_ids:
            t = grp_terminals.get(did, {})
            s = t.get("S", "")
            if s and s.upper() not in _POWER:
                diff_source_nets.add(s)
        # Also check drain nets of diff pair for shared connections
        diff_drain_nets = set()
        for did in diff_ids:
            t = grp_terminals.get(did, {})
            d = t.get("D", "")
            if d and d.upper() not in _POWER:
                diff_drain_nets.add(d)

        tail_ids = []
        for grp_id, t in grp_terminals.items():
            if grp_id in diff_ids:
                continue
            d_net = t.get("D", "")
            s_net = t.get("S", "")
            if (d_net in diff_source_nets or s_net in diff_source_nets):
                tail_ids.append(grp_id)

        if tail_ids:
            lines.append(f"  5. TAIL CURRENT: Place tail devices ({', '.join(tail_ids)}) ADJACENT to the diff pair.")
            lines.append(f"     These share internal nets with the diff pair and benefit from proximity.")
            lines.append("")

    if not lines:
        return ""

    return "\n".join(lines)


def build_finger_group_section(finger_map: dict, group_nodes: list) -> str:
    """
    Build a human-readable finger-group section for the LLM prompt.
    Explains the transistor groupings so the LLM knows what each group node
    represents and what constraints govern its placement.

    Fixed matched blocks (from ``merge_matched_groups``) are clearly labeled
    so the LLM knows they are pre-interdigitated and cannot be separated.
    """
    lines: List[str] = [
        "TRANSISTOR FINGER GROUPS:",
        "(Each entry below is ONE logical unit. Fingers are placed",
        " deterministically after you specify the group's origin X.)",
        "",
    ]

    grp_info = {n["id"]: n for n in group_nodes}

    for grp_id, members in finger_map.items():
            gn = grp_info.get(grp_id, {})
            elec = gn.get("electrical", {})
            tot  = elec.get("total_fingers", len(members))
            w    = gn.get("geometry", {}).get("width", round(tot * FINGER_PITCH, 6))

            if gn.get("_matched_block"):
                # This is a pre-interdigitated fixed block
                member_ids = gn.get("_members", [])
                technique  = gn.get("_technique", "matched")
                tech_label = {
                    "ABBA_diff_pair":         "ABBA interdigitated diff pair",
                    "ABBA_current_mirror":    "ABBA interdigitated current mirror",
                    "symmetric_cross_coupled": "symmetric cross-coupled pair",
                }.get(technique, technique)
                lines.append(
                    f"  {grp_id:<30}  [FIXED MATCHED BLOCK]"
                )
                lines.append(
                    f"    Contains: {' + '.join(member_ids)}  ({tech_label})"
                )
                lines.append(
                    f"    total_fingers={tot}  footprint={w:.3f} um"
                )
                lines.append(
                    f"    ** DO NOT separate — internal finger order is pre-set **"
                )
            else:
                nf   = elec.get("nf_per_device", 1)
                m    = elec.get("multiplier", 1)
                lines.append(
                    f"  {grp_id:<12}  nf={nf}  m={m}  "
                    f"total_fingers={tot}  "
                    f"footprint={w:.3f} um  "
                    f"(place fingers at X, X+0.070, X+0.140, ...)"
                )

    lines.append("")
    lines.append(
        "LLM TASK: Assign an origin X and use the pre-assigned Y from the "
        "ROW ASSIGNMENT table for EACH GROUP/BLOCK. Multiple PMOS rows and "
        "multiple NMOS rows are allowed. Matched blocks are FIXED — just "
        "assign their origin X, do NOT re-order their internal fingers."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Matching techniques: ABBA interdigitation + fixed block merging
# ---------------------------------------------------------------------------

def _generate_abba_pattern(
        fingers_a: List[dict],
        fingers_b: List[dict],
) -> List[dict]:
    """
    Generate an ABBA interdigitation pattern from two finger lists.

    For equal lengths (na == nb):
        A₁ B₁ B₂ A₂ | A₃ B₃ B₄ A₄ | ...  (true ABBA motifs of 4)

    For na > nb (A has more fingers):
        Distributes B fingers evenly among A fingers. Each B is inserted
        at the position nearest to ``(b_idx + 0.5) * na / nb`` so that B
        fingers are spread uniformly through the A sequence.

    Each returned dict is a *shallow copy* of the original finger node
    with an added ``"_match_owner"`` key indicating which group it
    originally belonged to (``"A"`` or ``"B"``).
    """
    # Make shallow copies so we don't mutate originals
    a_list = [dict(n, _match_owner="A") for n in fingers_a]
    b_list = [dict(n, _match_owner="B") for n in fingers_b]

    na, nb = len(a_list), len(b_list)
    if na == 0 and nb == 0:
        return []
    if na == 0:
        return b_list
    if nb == 0:
        return a_list

    # Equal count → true ABBA (motifs of 4: A B B A)
    if na == nb:
        result: List[dict] = []
        i = 0
        while i < na:
            # Prefer 4-finger ABBA motif
            if i + 1 < na:
                result.append(a_list[i])
                result.append(b_list[i])
                result.append(b_list[i + 1])
                result.append(a_list[i + 1])
                i += 2
            else:
                # Single remaining finger from each (odd na)
                result.append(a_list[i])
                result.append(b_list[i])
                i += 1
        return result

    # Unequal → ensure A is the longer list
    if nb > na:
        a_list, b_list = b_list, a_list
        na, nb = nb, na

    # Distribute B fingers evenly within A fingers
    # Insert b_idx at position round((b_idx + 0.5) * na / nb)
    insert_positions: set = set()
    for b_idx in range(nb):
        pos = int(round((b_idx + 0.5) * na / nb))
        pos = min(pos, na)  # clamp to valid range
        insert_positions.add((pos, b_idx))

    # Build result: walk A fingers and inject B at computed positions
    b_at: Dict[int, List[dict]] = defaultdict(list)
    for pos, b_idx in sorted(insert_positions):
        b_at[pos].append(b_list[b_idx])

    result = []
    b_used = set()
    for i, a_node in enumerate(a_list):
        # Inject any B fingers scheduled before this A finger
        for b_node in b_at.get(i, []):
            result.append(b_node)
        result.append(a_node)
    # Append any B fingers scheduled at or after the last A position
    for i in range(na, na + nb + 1):
        for b_node in b_at.get(i, []):
            result.append(b_node)

    return result



def _detect_current_mirrors(
        group_nodes: list,
        group_terminal_nets: dict,
) -> List[Tuple[str, str]]:
    """
    Detect current mirror pairs.

    A current mirror is identified when:
      - Two same-type groups share the same Gate net
      - That gate net is also the Drain of one of them (diode-connected)
      - They are NOT power/ground gated (no VDD/GND/CLK on gate)
    """
    _POWER = {"VDD", "VSS", "GND", "VCC", "CLK", ""}
    grp_lookup = {n["id"]: n for n in group_nodes}

    # Group by gate net
    gate_groups: Dict[str, List[str]] = defaultdict(list)
    for gid, t in group_terminal_nets.items():
        g_net = t.get("G", "")
        if g_net and g_net.upper() not in _POWER:
            gate_groups[g_net].append(gid)

    mirrors: List[Tuple[str, str]] = []
    for g_net, members in gate_groups.items():
        if len(members) < 2:
            continue
        # Check if same type
        types = set(grp_lookup.get(m, {}).get("type", "") for m in members)
        if len(types) != 1:
            continue
        # Check if any member is diode-connected (D == G)
        has_diode = any(
            group_terminal_nets.get(m, {}).get("D", "") == g_net
            for m in members
        )
        if has_diode:
            # Pair the diode-connected one with each non-diode one
            diode_ids = [m for m in members
                         if group_terminal_nets.get(m, {}).get("D", "") == g_net]
            other_ids = [m for m in members if m not in diode_ids]
            for d in diode_ids:
                for o in other_ids:
                    mirrors.append((d, o))
    return mirrors


def merge_matched_groups(
        group_nodes: list,
        group_edges: list,
        finger_map: dict,
        matching_info: dict,
        group_terminal_nets: dict,
        terminal_nets: dict,
) -> Tuple[list, list, dict, dict]:
    """
    Merge matched transistor pairs into fixed interdigitated blocks
    BEFORE the LLM sees the groups.

    This function:
      1. Identifies diff pairs and current mirrors
      2. Interdigitates their fingers using the ABBA pattern
      3. Creates a single "merged block" group node with the combined footprint
      4. Returns updated group_nodes, group_edges, finger_map, and a
         ``merged_blocks`` dict mapping block_id → {members, pattern}

    The LLM sees the merged block as a single device with one X origin.
    It cannot separate or reorder the internal fingers.

    Returns
    -------
    merged_group_nodes  : updated group node list (merged pairs replaced by single blocks)
    merged_group_edges  : updated edge list
    merged_finger_map   : updated finger_map (merged block → interleaved fingers)
    merged_blocks       : dict of block_id → {"members": [grpA, grpB], "technique": "ABBA"}
    """
    _POWER = frozenset({"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS", ""})

    # Collect all pairs to merge (diff pairs + current mirrors + cross-coupled)
    pairs_to_merge: List[Tuple[str, str, str]] = []  # (grpA, grpB, technique)
    used: set = set()

    # 1. Diff pairs (highest priority)
    for a, b in matching_info.get("diff_pairs", []):
        if a not in used and b not in used:
            pairs_to_merge.append((a, b, "ABBA_diff_pair"))
            used.update([a, b])

    # 2. Current mirrors
    cm_pairs = _detect_current_mirrors(group_nodes, group_terminal_nets)
    for a, b in cm_pairs:
        if a not in used and b not in used:
            pairs_to_merge.append((a, b, "ABBA_current_mirror"))
            used.update([a, b])

    # 3. Cross-coupled pairs (symmetric mirror AB|BA)
    for a, b in matching_info.get("cross_coupled", []):
        if a not in used and b not in used:
            pairs_to_merge.append((a, b, "symmetric_cross_coupled"))
            used.update([a, b])

    if not pairs_to_merge:
        return group_nodes, group_edges, finger_map, {}

    # --- Build merged blocks ------------------------------------------------
    grp_lookup = {n["id"]: n for n in group_nodes}
    new_group_nodes = []
    new_finger_map: dict = {}
    merged_blocks: dict = {}

    merged_ids: set = set()

    for grp_a_id, grp_b_id, technique in pairs_to_merge:
        if grp_a_id not in grp_lookup or grp_b_id not in grp_lookup:
            continue

        grp_a = grp_lookup[grp_a_id]
        grp_b = grp_lookup[grp_b_id]
        fingers_a = finger_map.get(grp_a_id, [])
        fingers_b = finger_map.get(grp_b_id, [])

        if not fingers_a or not fingers_b:
            continue

        # Generate interleaved pattern
        if technique.startswith("ABBA"):
            interleaved = _generate_abba_pattern(fingers_a, fingers_b)
        else:
            # Symmetric mirror: A fingers then B fingers (AB|BA not interleaved)
            interleaved = (
                [dict(n, _match_owner="A") for n in fingers_a] +
                [dict(n, _match_owner="B") for n in fingers_b]
            )

        # Determine abutment within interdigitation:
        # Check if the pairs share any non-power S/D net
        a_sd_nets = set()
        b_sd_nets = set()
        for f in fingers_a:
            tn = terminal_nets.get(f.get("id", ""), {})
            s, d = tn.get("S", ""), tn.get("D", "")
            if s and s.upper() not in _POWER:
                a_sd_nets.add(s)
            if d and d.upper() not in _POWER:
                a_sd_nets.add(d)
        for f in fingers_b:
            tn = terminal_nets.get(f.get("id", ""), {})
            s, d = tn.get("S", ""), tn.get("D", "")
            if s and s.upper() not in _POWER:
                b_sd_nets.add(s)
            if d and d.upper() not in _POWER:
                b_sd_nets.add(d)
        shared_sd = a_sd_nets & b_sd_nets
        # If they share S/D nets → use FINGER_PITCH (abutted), else STD_PITCH
        use_abutment = len(shared_sd) > 0

        block_pitch = FINGER_PITCH if use_abutment else STD_PITCH
        total_fingers = len(interleaved)
        block_width = round(total_fingers * block_pitch, 6)

        # Create a merged block ID
        block_id = f"{grp_a_id}_{grp_b_id}_matched"

        # Build a synthetic group node for the merged block
        elec_a = grp_a.get("electrical", {})
        block_node = {
            "id":   block_id,
            "type": grp_a.get("type", "nmos"),
            "electrical": {
                "l":              elec_a.get("l", 1.4e-8),
                "nf_per_device":  total_fingers,
                "multiplier":     1,
                "total_fingers":  total_fingers,
                "nfin":           elec_a.get("nfin", 2),
                "w":              elec_a.get("w", 0),
            },
            "geometry": {
                "x":           0.0,
                "y":           grp_a.get("geometry", {}).get("y", 0.0),
                "width":       block_width,
                "height":      grp_a.get("geometry", {}).get("height", 0.568),
                "orientation": "R0",
            },
            # Mark this as a fixed matched block
            "_matched_block": True,
            "_block_pitch":   block_pitch,
            "_members":       [grp_a_id, grp_b_id],
            "_technique":     technique,
        }

        if "block" in grp_a:
            block_node["block"] = grp_a["block"]

        new_group_nodes.append(block_node)
        new_finger_map[block_id] = interleaved
        merged_blocks[block_id] = {
            "members": [grp_a_id, grp_b_id],
            "technique": technique,
            "use_abutment": use_abutment,
            "total_fingers": total_fingers,
            "block_pitch": block_pitch,
        }
        merged_ids.update([grp_a_id, grp_b_id])

        print(f"[merge_matched] {grp_a_id} + {grp_b_id} -> {block_id} "
              f"({technique}, {total_fingers} fingers, "
              f"pitch={'abut' if use_abutment else 'std'}, "
              f"width={block_width:.3f} um)")

    # Add unmerged groups as-is
    for gn in group_nodes:
        if gn["id"] not in merged_ids:
            new_group_nodes.append(gn)
            new_finger_map[gn["id"]] = finger_map.get(gn["id"], [])

    # Update edges: replace references to merged members with block IDs
    member_to_block = {}
    for bid, info in merged_blocks.items():
        for m in info["members"]:
            member_to_block[m] = bid

    new_edges = []
    seen_edge_keys: set = set()
    for e in group_edges:
        src = member_to_block.get(e["source"], e["source"])
        tgt = member_to_block.get(e["target"], e["target"])
        if src == tgt:
            continue  # internal edge within merged block
        net = e.get("net", "")
        edge_key = (min(src, tgt), max(src, tgt), net)
        if edge_key not in seen_edge_keys:
            seen_edge_keys.add(edge_key)
            new_edges.append({"source": src, "target": tgt, "net": net})

    return new_group_nodes, new_edges, new_finger_map, merged_blocks


# ---------------------------------------------------------------------------
# Inter-group abutment detection
# ---------------------------------------------------------------------------

def detect_inter_group_abutment(
        group_nodes: list,
        finger_map: dict,
        terminal_nets: dict,
) -> List[dict]:
    """
    Detect abutment opportunities *between* different transistor groups.

    Two groups are candidates for inter-group abutment if:
      - They are the same device type (both NMOS or both PMOS)
      - They share a non-power Source or Drain net

    This catches real layout optimizations like:
      - MM5 <-> MM2  (share VOUTP drain)
      - MM8 <-> MM10 (share net2<> source/drain)

    Parameters
    ----------
    group_nodes   : compact group node dicts from ``group_fingers``
    finger_map    : group_id -> [finger node dicts]
    terminal_nets : device_id -> {D, G, S} net mapping

    Returns
    -------
    list of abutment candidate dicts:
        {"dev_a": grpA_id, "dev_b": grpB_id, "shared_net": net_name, "terminal": "S/D"}
    """
    _POWER = frozenset({"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS", ""})

    # Build group-level terminal net summary
    grp_nets: Dict[str, Dict[str, set]] = {}   # grp_id -> {"S": {nets}, "D": {nets}}
    grp_type: Dict[str, str] = {}

    for gn in group_nodes:
        gid = gn["id"]
        grp_type[gid] = gn.get("type", "nmos")
        s_nets = set()
        d_nets = set()
        for member in finger_map.get(gid, []):
            mid = member.get("id", "")
            tn = terminal_nets.get(mid, {})
            s = tn.get("S", "")
            d = tn.get("D", "")
            if s and s.upper() not in _POWER:
                s_nets.add(s)
            if d and d.upper() not in _POWER:
                d_nets.add(d)
        grp_nets[gid] = {"S": s_nets, "D": d_nets}

    # Find pairs with shared S/D nets (same type only)
    candidates: List[dict] = []
    seen: set = set()
    gids = list(grp_nets.keys())

    for i, ga in enumerate(gids):
        for gb in gids[i + 1:]:
            if grp_type.get(ga) != grp_type.get(gb):
                continue
            na = grp_nets[ga]
            nb = grp_nets[gb]
            # Check all S/D combinations
            for ta, nets_a in na.items():
                for tb, nets_b in nb.items():
                    shared = nets_a & nets_b
                    for net in shared:
                        key = (min(ga, gb), max(ga, gb), net)
                        if key not in seen:
                            seen.add(key)
                            candidates.append({
                                "dev_a": ga,
                                "dev_b": gb,
                                "shared_net": net,
                                "terminal": f"{ta}/{tb}",
                            })

    return candidates


# ---------------------------------------------------------------------------
# Public API — Step 1b: PRE-ASSIGN ROWS (deterministic bin-packing)
# ---------------------------------------------------------------------------

MAX_ROW_WIDTH = 8.0   # µm — trigger a new row when a type's total footprint exceeds this


def _symmetry_order(
        groups: List[dict],
        matching_info: dict,
        group_terminal_nets: dict,
) -> List[dict]:
    """
    Reorder groups within a single row for symmetric placement.

    Priority order (center outward):
      1. Cross-coupled latch pairs -> center
      2. Diff pair halves -> flank the latch
      3. Tail current sources -> adjacent to diff pair
      4. Everything else (CLK switches, etc.) -> outer edges, split symmetrically

    Returns the reordered list: [left_flank ... center ... right_flank]
    """
    if not groups or not matching_info:
        return groups

    gid_set = {g["id"] for g in groups}
    gid_to_node = {g["id"]: g for g in groups}

    # Identify cross-coupled pairs in this row
    cross_pairs = matching_info.get("cross_coupled", [])
    row_cross = [(a, b) for a, b in cross_pairs if a in gid_set and b in gid_set]

    # Identify diff pair halves in this row
    diff_pairs = matching_info.get("diff_pairs", [])
    row_diff_left = []
    row_diff_right = []
    for a, b in diff_pairs:
        if a in gid_set:
            row_diff_left.append(a)
        if b in gid_set:
            row_diff_right.append(b)

    # Identify tail sources
    tail_ids = [t for t in matching_info.get("tail_sources", []) if t in gid_set]

    # Collect center, near-center, and flanks
    used = set()
    center_ids = []
    for a, b in row_cross:
        center_ids.extend([a, b])
        used.update([a, b])

    near_left = [x for x in row_diff_left if x not in used]
    near_right = [x for x in row_diff_right if x not in used]
    used.update(near_left + near_right)

    tail_left = []
    tail_right = []
    for t in tail_ids:
        if t not in used:
            # Put half tails on each side
            if len(tail_left) <= len(tail_right):
                tail_left.append(t)
            else:
                tail_right.append(t)
            used.add(t)

    # Everything else is a flank device; split symmetrically
    remaining = [g["id"] for g in groups if g["id"] not in used]
    half = len(remaining) // 2
    flank_left = remaining[:half]
    flank_right = remaining[half:]

    # Assemble: left_flank | tail_left | diff_left | center | diff_right | tail_right | right_flank
    ordered_ids = flank_left + tail_left + near_left + center_ids + near_right + tail_right + flank_right

    # Return in order, falling back to original position for any we missed
    result = []
    for gid in ordered_ids:
        if gid in gid_to_node:
            result.append(gid_to_node[gid])
    # Safety: add any groups we somehow missed
    for g in groups:
        if g["id"] not in set(ordered_ids):
            result.append(g)

    return result


def pre_assign_rows(
        group_nodes: list,
        max_row_width: float = MAX_ROW_WIDTH,
        matching_info: dict | None = None,
        group_terminal_nets: dict | None = None,
) -> Tuple[list, str]:
    """
    Deterministically assign transistor groups to rows BEFORE calling the LLM.

    Uses a greedy bin-packing approach (largest-first):
      - Separate groups by type (NMOS / PMOS / passive)
      - Assign groups to rows until the accumulated footprint exceeds
        `max_row_width`; then start a new row
      - NMOS rows get the lowest Y values (starting at 0)
      - PMOS rows get Y values immediately above all NMOS rows
      - Passive rows are placed above all transistor rows

    Symmetry-aware ordering
    -----------------------
    When ``matching_info`` is provided (from ``detect_matching_groups`` +
    ``build_matching_section``), each row's groups are reordered so that:
      - Cross-coupled latch pairs sit at the center
      - Diff pair halves flank the latch
      - CLK switches are split symmetrically to the outer edges

    Returns
    -------
    updated_nodes  : deep-copy of group_nodes with geometry.y updated
    row_summary_str: human-readable string for the LLM prompt
    """
    import copy as _copy

    pmos_groups    = [n for n in group_nodes if n.get("type") == "pmos"]
    nmos_groups    = [n for n in group_nodes if n.get("type") == "nmos"]
    passive_groups = [n for n in group_nodes
                      if n.get("type") not in ("pmos", "nmos")]

    # --- Rectangular balancing: equalise PMOS / NMOS row widths ----------
    # Compute total footprint per type (sum of group widths + inter-group gaps)
    def _total_footprint(groups: list) -> float:
        if not groups:
            return 0.0
        total = sum(g.get("geometry", {}).get("width", 0.0) for g in groups)
        total += STD_PITCH * max(0, len(groups) - 1)   # gaps between groups
        return total

    pmos_total = _total_footprint(pmos_groups)
    nmos_total = _total_footprint(nmos_groups)

    # If one type is significantly wider than the other (>1.3x), shrink
    # the WIDER type's row limit so it splits into multiple rows that are
    # close to the narrower type's width → rectangular layout.
    # The narrower type keeps its original max_row_width (stays in 1 row).
    pmos_max = max_row_width
    nmos_max = max_row_width

    if pmos_total > 0 and nmos_total > 0:
        wider  = max(pmos_total, nmos_total)
        narrow = min(pmos_total, nmos_total)
        # Lower threshold to 1.15 so even moderate imbalances trigger row splitting.
        # For a comparator: 6 PMOS groups * 8 fingers * STD_PITCH ≈ 14.1 µm PMOS
        # vs NMOS total. The ratio is often 1.2-1.5 so 1.3 was too conservative.
        if wider > narrow * 1.15:
            import math
            num_rows_needed = math.ceil(wider / narrow)
            target_width = wider / num_rows_needed
            # Don't go below the single widest device (otherwise it can't fit)
            widest_device = max(
                (g.get("geometry", {}).get("width", 0.0) for g in group_nodes),
                default=0.0,
            )
            balanced_max = max(target_width, widest_device + STD_PITCH)

            # Only constrain the wider type
            if pmos_total >= nmos_total:
                pmos_max = balanced_max
                print(f"[pre_assign_rows] Rectangular balancing: "
                      f"PMOS={pmos_total:.3f} > NMOS={nmos_total:.3f} "
                      f"-> splitting PMOS rows (max={pmos_max:.3f} um)")
            else:
                nmos_max = balanced_max
                print(f"[pre_assign_rows] Rectangular balancing: "
                      f"NMOS={nmos_total:.3f} > PMOS={pmos_total:.3f} "
                      f"-> splitting NMOS rows (max={nmos_max:.3f} um)")

    def _bin_pack(groups: list, max_w: float) -> List[List[dict]]:
        """Greedy largest-first bin-pack.  Returns list-of-rows (each = list of nodes)."""
        if not groups:
            return []
        # Largest total_fingers (widest) first
        sorted_g = sorted(groups,
                          key=lambda n: n.get("electrical", {}).get("total_fingers", 1),
                          reverse=True)
        rows:  List[List[dict]] = [[]]
        widths: List[float]     = [0.0]

        for g in sorted_g:
            w = g.get("geometry", {}).get("width", 0.0)
            placed = False
            for i, (row, rw) in enumerate(zip(rows, widths)):
                gap = STD_PITCH if rw > 0 else 0.0
                if rw + gap + w <= max_w:
                    rows[i].append(g)
                    widths[i] = rw + gap + w
                    placed = True
                    break
            if not placed:
                rows.append([g])
                widths.append(w)
        return rows

    nmos_rows    = _bin_pack(nmos_groups,    nmos_max)
    pmos_rows    = _bin_pack(pmos_groups,    pmos_max)
    num_nmos     = len(nmos_rows)

    # --- Symmetry-aware reordering within each row -----------------------
    if matching_info and group_terminal_nets:
        for i, row in enumerate(nmos_rows):
            nmos_rows[i] = _symmetry_order(row, matching_info, group_terminal_nets)
        for i, row in enumerate(pmos_rows):
            pmos_rows[i] = _symmetry_order(row, matching_info, group_terminal_nets)

    # Build group_id -> Y map
    y_map: Dict[str, float] = {}
    for row_idx, row in enumerate(nmos_rows):
        y = row_idx * ROW_PITCH
        for g in row:
            y_map[g["id"]] = y

    for row_idx, row in enumerate(pmos_rows):
        y = (num_nmos + row_idx) * ROW_PITCH
        for g in row:
            y_map[g["id"]] = y

    passive_y = (num_nmos + len(pmos_rows)) * ROW_PITCH
    for g in passive_groups:
        y_map[g["id"]] = passive_y

    # Clone nodes and update geometry.y
    updated: List[dict] = []
    for n in group_nodes:
        nc = _copy.deepcopy(n)
        if nc["id"] in y_map:
            nc["geometry"]["y"] = y_map[nc["id"]]
        updated.append(nc)

    # Build human-readable summary for the LLM prompt
    lines: List[str] = []
    for row_idx, row in enumerate(nmos_rows):
        y = row_idx * ROW_PITCH
        ids    = ", ".join(g["id"] for g in row)
        widths = " + ".join(f"{g['geometry']['width']:.3f}" for g in row)
        lines.append(f"   NMOS Row {row_idx}  y={y:.3f} um : {ids}  (footprints: {widths} um)")

    for row_idx, row in enumerate(pmos_rows):
        y = (num_nmos + row_idx) * ROW_PITCH
        ids    = ", ".join(g["id"] for g in row)
        widths = " + ".join(f"{g['geometry']['width']:.3f}" for g in row)
        lines.append(f"   PMOS Row {row_idx}  y={y:.3f} um : {ids}  (footprints: {widths} um)")

    if passive_groups:
        ids = ", ".join(g["id"] for g in passive_groups)
        lines.append(f"   Passive Row      y={passive_y:.3f} um : {ids}")

    row_summary_str = "\n".join(lines)

    total_nmos = len(nmos_rows)
    total_pmos = len(pmos_rows)
    print(f"[pre_assign_rows] {total_nmos} NMOS row(s), {total_pmos} PMOS row(s)")
    for line in lines:
        print(f"  {line.strip()}")

    return updated, row_summary_str



def _snap_to_row_grid(y: float) -> float:
    """
    Snap a Y coordinate to the nearest multiple of ROW_PITCH (0.668 µm).
    Ensures all rows land exactly on the row grid regardless of LLM rounding.
    Negative values are clamped to 0.
    """
    if y < 0:
        y = 0.0
    row_index = round(y / ROW_PITCH)   # nearest integer row index
    return round(row_index * ROW_PITCH, 6)


def expand_groups(
        group_placement: list,
        finger_map: dict,
        matching_info: dict | None = None,
        no_abutment: bool = False,
) -> list:
    """
    Expand group-level LLM placement back to individual finger nodes.

    Multi-row support
    -----------------
    The LLM may assign any valid row Y (a non-negative multiple of ROW_PITCH).
    This function:
      1. Snaps the LLM's Y to the nearest row-grid position (fixes float drift).
      2. Enforces PMOS/NMOS type correctness.
      3. Places each finger with correct pitch and abutment flags.

    Matched block support
    ---------------------
    Groups marked with ``_matched_block=True`` (created by
    ``merge_matched_groups``) use their own ``_block_pitch`` for internal
    finger spacing.  The interleaved finger order was established pre-LLM
    and is preserved exactly.

    Parameters
    ----------
    group_placement : list of group node dicts with updated geometry from LLM
    finger_map      : finger_map from ``group_fingers`` or ``merge_matched_groups``
    matching_info   : matching info dict (currently informational)
    no_abutment     : if True, use STD_PITCH (0.294 um) between fingers and
                      clear all abutment flags.  The user chose not to abut
                      any devices.

    Returns
    -------
    expanded_nodes : list of individual finger node dicts
    """
    expanded: List[dict] = []

    # Default pitch based on abutment mode
    default_pitch = STD_PITCH if no_abutment else FINGER_PITCH

    # Build lookup: group_id -> placed geometry (from LLM output)
    placed = {n["id"]: n for n in group_placement}

    # --- Pass 1: collect snapped Y per group and determine PMOS/NMOS row sets
    group_ys: Dict[str, float] = {}   # group_id -> snapped Y
    for grp_id, members in finger_map.items():
        grp_geom = placed.get(grp_id, {}).get("geometry", {})
        raw_y = grp_geom.get("y", NMOS_Y)
        group_ys[grp_id] = _snap_to_row_grid(raw_y)

    nmos_ys_used = {y for grp_id, y in group_ys.items()
                    if finger_map[grp_id][0].get("type", "nmos") == "nmos"}

    # Safety: compute the minimum valid Y for a PMOS row
    max_nmos_y = max(nmos_ys_used) if nmos_ys_used else -ROW_PITCH
    min_pmos_y = max_nmos_y + ROW_PITCH

    # --- Pass 2: fix any PMOS group that ended up at or below the NMOS zone
    for grp_id, members in finger_map.items():
        dev_type = members[0].get("type", "nmos")
        if dev_type == "pmos":
            if group_ys[grp_id] <= max_nmos_y:
                group_ys[grp_id] = min_pmos_y

    # --- Pass 3: expand each group to individual fingers --------------------
    for grp_id, members in finger_map.items():
        grp_placed = placed.get(grp_id, {})
        grp_geom = grp_placed.get("geometry", {})
        origin_x = grp_geom.get("x", 0.0)
        final_y  = group_ys[grp_id]
        orient   = grp_geom.get("orientation", "R0")

        # Determine pitch for this group
        is_matched_block = grp_placed.get("_matched_block", False)
        if is_matched_block and not no_abutment:
            pitch = grp_placed.get("_block_pitch", default_pitch)
        else:
            pitch = default_pitch

        total = len(members)

        for finger_idx, orig_node in enumerate(members):
            node = copy.deepcopy(orig_node)
            fx = round(origin_x + finger_idx * pitch, 6)
            node["geometry"].update({
                "x":           fx,
                "y":           final_y,
                "orientation": orient,
            })

            # Clean up internal metadata
            node.pop("_match_owner", None)

            if no_abutment:
                # No abutment requested — clear all flags
                node["abutment"] = {
                    "abut_left":  False,
                    "abut_right": False,
                }
            else:
                # Abutment flags: first finger has no left neighbor; last has no right
                node["abutment"] = {
                    "abut_left":  finger_idx > 0,
                    "abut_right": finger_idx < (total - 1),
                }

            expanded.append(node)

    # --- Pass 4: resolve inter-group overlaps per row ----------------------
    expanded = _resolve_row_overlaps(expanded, no_abutment)

    return expanded


def _resolve_row_overlaps(nodes: List[dict], no_abutment: bool = False) -> List[dict]:
    """
    Guarantee no two devices in the same row overlap.

    Groups all nodes by their snapped Y coordinate, sorts each row by X,
    and pushes any overlapping device to the right of its left neighbor.

    Spacing used:
      - If two adjacent fingers share an abutment bond -> FINGER_PITCH (0.070)
      - Otherwise -> device width (typically STD_PITCH = 0.294)
    """
    if not nodes:
        return nodes

    pitch_abut = FINGER_PITCH  # 0.070
    pitch_std  = STD_PITCH     # 0.294

    # Group by Y (rounded to avoid float drift)
    rows: Dict[float, List[dict]] = defaultdict(list)
    for n in nodes:
        y = round(n.get("geometry", {}).get("y", 0.0), 6)
        rows[y].append(n)

    for y_key, row_nodes in rows.items():
        # Sort by X (use original X for initial ordering)
        row_nodes.sort(key=lambda n: n.get("geometry", {}).get("x", 0.0))

        for i in range(1, len(row_nodes)):
            prev = row_nodes[i - 1]
            curr = row_nodes[i]
            prev_geo = prev.get("geometry", {})
            curr_geo = curr.get("geometry", {})

            # Always read the already-updated (pushed) X from prev
            prev_x = prev_geo.get("x", 0.0)
            curr_x = curr_geo.get("x", 0.0)

            # Determine required spacing
            if no_abutment:
                min_spacing = pitch_std
            else:
                # Check if these two are abutted (right of prev, left of curr)
                prev_abut = prev.get("abutment", {}).get("abut_right", False)
                curr_abut = curr.get("abutment", {}).get("abut_left", False)
                if prev_abut and curr_abut:
                    min_spacing = pitch_abut
                else:
                    # Use the device width as spacing, or STD_PITCH as fallback
                    min_spacing = prev_geo.get("width", pitch_std)

            # Push current device right if it overlaps with the previous one
            min_x = round(prev_x + min_spacing, 6)
            if curr_x < min_x - 1e-9:
                curr_geo["x"] = min_x

    return nodes


# ---------------------------------------------------------------------------
# Utility: build a trimmed prompt graph (no edges, compact nodes)
# ---------------------------------------------------------------------------

def build_compact_graph(
        group_nodes: list,
        group_edges: list,
        graph_data: dict,
) -> dict:
    """Return a compact graph dict suitable for LLM consumption."""
    compact = {
        "nodes":         group_nodes,
        "edges":         group_edges,
        "terminal_nets": graph_data.get("terminal_nets", {}),
        "blocks":        graph_data.get("blocks", {}),
        "abutment_candidates": graph_data.get("abutment_candidates", []),
    }
    return compact

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
from ai_agent.ai_chat_bot.pipeline_log import vprint

# ---------------------------------------------------------------------------
# Constants — sourced from centralized design rules config
# ---------------------------------------------------------------------------
from config.design_rules import (
    PMOS_Y, NMOS_Y, ROW_PITCH, FINGER_PITCH, PITCH_UM as STD_PITCH,
)

# Regex: split "MM9<3>_f4" → ("MM9", "3", "4")  (legacy array-bus)
_BUS_RE   = re.compile(r'^(.+?)<(\d+)>(?:_f(\d+))?$')
# Regex: split "MM6_m2_f3" → ("MM6", "2", "3")  (multiplier + finger)
_MULTI_FINGER_RE = re.compile(r'^(.+?)_m(\d+)_f(\d+)$')
# Regex: split "MM6_m3"    → ("MM6", "3", None)  (multiplier/array only)
_MULTI_ONLY_RE = re.compile(r'^(.+?)_m(\d+)$')
# Regex: split "MM5_f2"    → ("MM5", None, "2")  (finger-only, legacy)
_PLAIN_FINGER_RE = re.compile(r'^(.+?)_f(\d+)$')

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_id(node_id: str) -> Tuple[str, int | None, int | None]:
    """
    Parse a node id into (parent_name, multiplier_index, finger_index).

    Handles multiple naming conventions:

    New naming (from updated parse_mos):
        "MM6_m2_f3"  → ("MM6", 2, 3)    ← multiplier 2, finger 3
        "MM6_m3"     → ("MM6", 3, None)  ← multiplier/array child 3
        "MM9_m8"     → ("MM9", 8, None)  ← array copy 8
        "MM5_f2"     → ("MM5", None, 2)  ← finger 2 (finger-only)
        "MM1"        → ("MM1", None, None) ← single device

    Legacy array-bus naming (from old data / layout files):
        "MM9<3>_f4"  → ("MM9", 3, 4)
        "MM9<3>"     → ("MM9", 3, None)

    Returns
    -------
    (parent_name, multiplier_index, finger_index)
    """
    # Try new multi+finger pattern first: "MM6_m2_f3"
    m = _MULTI_FINGER_RE.match(node_id)
    if m:
        return m.group(1), int(m.group(2)), int(m.group(3))

    # Try new multi-only pattern: "MM6_m3"
    m = _MULTI_ONLY_RE.match(node_id)
    if m:
        return m.group(1), int(m.group(2)), None

    # Try legacy array-bus pattern: "MM9<3>_f4"
    m = _BUS_RE.match(node_id)
    if m:
        parent = m.group(1)
        bus_idx = int(m.group(2))
        finger_idx = int(m.group(3)) if m.group(3) else None
        return parent, bus_idx, finger_idx

    # Try legacy finger-only pattern: "MM5_f2"
    m = _PLAIN_FINGER_RE.match(node_id)
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

def group_fingers(nodes: list, edges: list) -> Tuple[list, list, dict]:
    """
    Collapse multiple finger-level node dictionaries into single transistor-level groups.

    This function prevents context truncation by the LLM. If an NMOS has 8 fingers 
    and multiplier=4, it collapses 32 raw nodes into a single logical "group node".

    Parameters
    ----------
    nodes : list
        The raw graph extracted nodes array from the UI schematic.
    edges : list
        The raw connection edge array.

    Returns
    -------
    Tuple[list, list, dict]
        A 3-tuple containing:
        - `group_nodes` (list): Compact lists of merged entities for the LLM.
        - `group_edges` (list): Pruned and remapped edges for the merged entities.
        - `finger_map` (dict): A mapping of `{ group_id -> [original_node1, original_node2, ...] }` 
          used to restore the architecture in `expand_groups`.
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
    Detect structurally matched transistor groups using electrical signatures.

    Identifies matched pairs (L, W, nf, nfin are identical), differential pairs,
    and cross-coupled topologies purely from topological netlist signatures.

    Parameters
    ----------
    group_nodes : list
        List of compacted group-level nodes.
    group_edges : list
        List of compacted group-level edges.

    Returns
    -------
    dict
        A mapping defining symmetrical groups, including:
        - `matched_pairs`: list of tuples `(grpA, grpB)`
        - `matched_clusters`: list of lists `[grpA, grpB, grpC]`
        - `diff_pairs`: list of tuples `(grpA, grpB)`
        - `cross_coupled`: list of tuples `(grpA, grpB)`
        - `tail_sources`: list of string `grp_id`s
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
    Construct a human-readable text block summarizing transistor symmetry constraints.

    Generates the "Matched Transistors / Symmetry Constraints" text section
    injected into the prompt. It enforces strict placement symmetry rules for
    cross-coupled systems and warns about predefined matched blocks.

    Parameters
    ----------
    group_nodes : list
        List of compact group node dicts.
    group_edges : list
        List of compacted edge dicts (unused but provided for API expansion).
    group_terminal_nets : dict
        Mapping of `group_id -> {D, G, S}` net topology.

    Returns
    -------
    str
        A multi-line formatted string describing all symmetrical rules.
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
    Construct the human-readable transistor inventory defining all placement groups.

    Warns the model about groups that are permanently pre-interdigitated 
    (Fixed Matched Blocks) and details footprint sizes.

    Parameters
    ----------
    finger_map : dict
        Mapping of `group_id -> list of constituent fingers`.
    group_nodes : list
        List of compacted group-level nodes.

    Returns
    -------
    str
        A multi-line formatted string defining transistor finger group footprints.
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
            w    = gn.get("geometry", {}).get("width", round(tot * STD_PITCH, 6))

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
    Generate an ABBA interdigitation pattern from two identical-device finger lists.

    For equal lengths (na == nb):
        A1 B1 B2 A2 | A3 B3 B4 A4 | ...  (true ABBA motifs of 4)

    For unequal lengths:
        Distributes B fingers evenly among A fingers for maximum thermal symmetry.

    Parameters
    ----------
    fingers_a : List[dict]
        List of raw finger nodes for Device A.
    fingers_b : List[dict]
        List of raw finger nodes for Device B.

    Returns
    -------
    List[dict]
        An ordered, interleaved list of shallow-copied finger dictionaries.
        Each dictionary gets a injected `_match_owner` key ("A" or "B")
        for debug tracing later.
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
    # Re-tag _match_owner after swap so tags reflect the ORIGINAL device
    # identity, not the list position.
    if nb > na:
        a_list, b_list = b_list, a_list
        na, nb = nb, na
        # Fix tags: a_list items now carry "B" but should be "A" (and vice versa)
        for node in a_list:
            node["_match_owner"] = "A"
        for node in b_list:
            node["_match_owner"] = "B"

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
    Merge symmetrical transistor pairs into fixed interdigitated monolithic blocks.

    Executes BEFORE the LLM sees the topology. If it detects a differential
    pair or current mirror, it permanently interdigitates their fingers (ABBA)
    into one super-group block node. The LLM only assigns an X origin to the block
    and cannot accidentally dismember the matched pair.

    Parameters
    ----------
    group_nodes : list
        The current list of compacted transistor groups.
    group_edges : list
        The current graph edges.
    finger_map : dict
        Mapping of `group_id -> [fingers]`.
    matching_info : dict
        Dictionary of detected structural matches (from `detect_matching_groups`).
    group_terminal_nets : dict
        Dictionary of resolved Gate/Source/Drain terminal nets for groups.
    terminal_nets : dict
        Raw finger-level terminal nest (unused natively here but needed if deeper
        finger-level routing checks get added).

    Returns
    -------
    Tuple[list, list, dict, dict]
        A 4-tuple containing:
        - `merged_group_nodes` (list): Group list with pairs swallowed into blocks.
        - `merged_group_edges` (list): Edges updated to point to block IDs.
        - `merged_finger_map` (dict): `group_id -> interleaved [A, B, B, A] fingers`.
        - `merged_blocks` (dict): Audit trail mapping `block_id -> {"members": [grpA, grpB], "technique": "ABBA"}`.
    """
    _POWER = frozenset({"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS", ""})

    if group_terminal_nets:
        _enrich_matching_info(matching_info, group_terminal_nets, group_nodes)

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

        vprint(f"[merge_matched] {grp_a_id} + {grp_b_id} -> {block_id} "
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
    Deterministically allocate transistor groups into physical Y-rows via bin packing.

    Runs BEFORE calling the LLM. It calculates row break points to ensure layout
    aspect ratios stay relatively rectangular rather than infinitely wide. 
    It forces strict isolation of NMOS, PMOS, and Passives.

    Parameters
    ----------
    group_nodes : list
        List of compacted group-level nodes.
    max_row_width : float, optional
        Heuristic absolute maximum width for any single geometrical row before wrapping.
    matching_info : dict, optional
        Detected symmetric relationships. Used to arrange devices laterally to satisfy
        structural constraints. Defaults to None.
    group_terminal_nets : dict, optional
        Precalculated grouped D/G/S nets. Used to deduce topologies like latches 
        during structural placement. Defaults to None.

    Returns
    -------
    Tuple[list, str]
        A 2-tuple containing:
        - `updated_nodes` (list): Modified group nodes with fixed `y` geometries.
        - `row_summary_str` (str): Text table injected into the LLM prompt.
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
                vprint(f"[pre_assign_rows] Rectangular balancing: "
                      f"PMOS={pmos_total:.3f} > NMOS={nmos_total:.3f} "
                      f"-> splitting PMOS rows (max={pmos_max:.3f} um)")
            else:
                nmos_max = balanced_max
                vprint(f"[pre_assign_rows] Rectangular balancing: "
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
    vprint(f"[pre_assign_rows] {total_nmos} NMOS row(s), {total_pmos} PMOS row(s)")
    for line in lines:
        vprint(f"  {line.strip()}")

    return updated, row_summary_str



def _snap_to_row_grid(y: float, pitch: float | None = None) -> float:
    """Quantize Y to a row grid. Uses *pitch* if given, else ROW_PITCH."""
    p = pitch if pitch and pitch > 0 else ROW_PITCH
    if y < 0:
        y = 0.0
    row_index = round(y / p)
    return round(row_index * p, 6)


def expand_groups(
        group_placement: list,
        finger_map: dict,
        matching_info: dict | None = None,
        no_abutment: bool = False,
        original_group_nodes: dict[str, dict] | None = None,
) -> list:
    """
    Explode optimized LLM groupings back into physical multi-finger atomic elements.

    Restores `1 LLM output -> 40 actual fingers` while rigorously applying
    precision layout math (abutment spacing, matched patterns).

    Parameters
    ----------
    group_placement : list
        The fully processed AI placement response array (nodes).
    finger_map : dict
        Mapping recorded during `group_fingers` dictating which atomic nodes
        belong to which AI group.
    matching_info : dict, optional
        Precomputed symmetry definitions used to determine finger-level orderings.
        Defaults to None.
    no_abutment : bool, optional
        Force diffusion breaks everywhere by reverting standard FINGER_PITCH to
        STD_PITCH arrays. Defaults to False.
    original_group_nodes : dict, optional
        Mapping of group_id -> original group node dict (from ``group_fingers``).
        Used to retrieve private metadata (e.g. ``_matched_block``, ``_block_pitch``)
        that LLMs typically strip from their output. Defaults to None.

    Returns
    -------
    list
        Fully unrolled and geometrically sound list of elemental transistor dicts.
    """
    expanded: List[dict] = []

    # Default pitch based on abutment mode
    default_pitch = STD_PITCH if no_abutment else FINGER_PITCH

    # Build lookup: group_id -> placed geometry (from LLM output)
    placed = {n["id"]: n for n in group_placement}

    # --- Pass 1: collect snapped Y per group and enforce PMOS/NMOS separation
    #
    # Row separation rules:
    #   - NMOS rows: Y = 0, ROW_PITCH, 2*ROW_PITCH, ...
    #   - PMOS rows: Y must be STRICTLY above all NMOS bounding boxes
    #   - No overlap allowed: PMOS bottom edge >= max(NMOS top edge)
    #   - Device heights vary with fin count (nfin) — taller devices need more space
    #   - Height is computed from nfin: height ≈ 0.5 + nfin × 0.04 µm
    group_ys: Dict[str, float] = {}   # group_id -> snapped Y
    group_types: Dict[str, str] = {}   # group_id -> device type
    group_heights: Dict[str, float] = {}  # group_id -> computed device height in µm

    for grp_id, members in finger_map.items():
        grp_geom = placed.get(grp_id, {}).get("geometry", {})
        raw_y = grp_geom.get("y", NMOS_Y)
        dev_type = members[0].get("type", "nmos")
        rep_elec = members[0].get("electrical", {})

        # Compute height from nfin (number of fins in the vertical direction)
        # FinFET device height scales linearly with nfin.
        # Conservative estimate: base=0.55 µm, fin_pitch=0.05 µm
        # This accounts for the actual OAS PCell height which is often larger
        # than the nominal geometric bounding box.
        nfin = rep_elec.get("nfin", 2)
        computed_height = 0.55 + nfin * 0.05
        # Also check the LLM-provided height — use the larger of the two
        llm_height = grp_geom.get("height", 0.0)
        # Add a safety margin of 0.05 µm to prevent any edge overlap
        group_heights[grp_id] = max(computed_height, llm_height, 0.5) + 0.05

        group_types[grp_id] = dev_type
        # DON'T re-snap — trust the Y from the geometry engine which already
        # used a dynamic row pitch based on actual device heights.
        group_ys[grp_id] = round(float(raw_y), 6)

    # --- Pass 2: enforce strict PMOS/NMOS bounding box separation --------
    # Compute the maximum top edge of any NMOS device: max(nmos_y + nmos_height)
    nmos_top_edges = []
    for grp_id, y in group_ys.items():
        if group_types.get(grp_id) == "nmos":
            h = group_heights.get(grp_id, 0.668)
            # The top edge of the NMOS bounding box is y + height
            nmos_top_edges.append(y + h)

    max_nmos_top = max(nmos_top_edges) if nmos_top_edges else 0.0

    # PMOS bottom edge must be at or above max NMOS top edge
    min_pmos_y = max_nmos_top

    # Also ensure NMOS and PMOS are on different row grid levels
    # (prevents same-row overlap even with height check)
    nmos_ys_used = {y for grp_id, y in group_ys.items()
                    if group_types.get(grp_id) == "nmos"}
    max_nmos_y = max(nmos_ys_used) if nmos_ys_used else 0.0
    # PMOS must be on a row strictly above the highest NMOS row
    min_pmos_row = max_nmos_y + ROW_PITCH
    # Take the larger of the two constraints
    min_pmos_y = max(min_pmos_y, min_pmos_row)

    for grp_id, y in group_ys.items():
        if group_types.get(grp_id) == "pmos":
            if y < min_pmos_y:
                group_ys[grp_id] = min_pmos_y

    # If PMOS ended up below NMOS (LLM gave it a lower Y), shift ALL PMOS up
    pmos_ys = [y for grp_id, y in group_ys.items()
               if group_types.get(grp_id) == "pmos"]
    if pmos_ys and nmos_ys_used:
        current_min_pmos = min(pmos_ys)
        if current_min_pmos < min_pmos_y:
            shift = min_pmos_y - current_min_pmos
            for grp_id, y in group_ys.items():
                if group_types.get(grp_id) == "pmos":
                    group_ys[grp_id] = round(y + shift, 6)

    # --- Pass 3: expand each group to individual fingers --------------------
    #
    # Abutment behavior for hierarchy siblings:
    #   All members within a single group are siblings at the same hierarchy
    #   level (or at the leaf level of a two-level hierarchy).  They MUST be
    #   abutted — placed directly adjacent with no gap (FINGER_PITCH = 0.070 µm).
    #
    #   For a two-level hierarchy (e.g. m=3, nf=5 = 15 leaves), the members
    #   list is sorted so that multiplier child 1's fingers come first,
    #   followed by multiplier child 2's fingers, etc.  All 15 are placed
    #   consecutively with abutment spacing.
    #
    #   The parent device's bounding box (set during group placement) spans
    #   from the origin X to origin X + total_fingers × pitch.
    for grp_id, members in finger_map.items():
        grp_placed = placed.get(grp_id, {})
        grp_geom = grp_placed.get("geometry", {})
        origin_x = grp_geom.get("x", 0.0)
        final_y  = group_ys[grp_id]
        orient   = grp_geom.get("orientation", "R0")

        # Determine pitch for this group
        # Prefer original group metadata (LLMs strip private fields)
        orig_meta = original_group_nodes.get(grp_id, {}) if original_group_nodes else {}
        is_matched_block = orig_meta.get("_matched_block", grp_placed.get("_matched_block", False))
        if is_matched_block and not no_abutment:
            pitch = orig_meta.get("_block_pitch", grp_placed.get("_block_pitch", default_pitch))
        else:
            pitch = default_pitch

        total = len(members)

        for finger_idx, orig_node in enumerate(members):
            node = copy.deepcopy(orig_node)
            # Place each sibling at consecutive positions with the group's pitch
            # This ensures abutment for all hierarchy leaves within the group
            fx = round(origin_x + finger_idx * pitch, 6)
            node["geometry"].update({
                "x":           fx,
                "y":           final_y,
                "orientation": orient,
                "width":       pitch,
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
                # Abutment flags for hierarchy siblings:
                #   First leaf:  no left neighbor, has right neighbor
                #   Middle leaf: has both left and right neighbors
                #   Last leaf:   has left neighbor, no right neighbor
                # This creates a continuous abutment chain across all siblings
                node["abutment"] = {
                    "abut_left":  finger_idx > 0,
                    "abut_right": finger_idx < (total - 1),
                }

            expanded.append(node)

    # --- Pass 4: resolve inter-group overlaps per row ----------------------
    from ai_agent.ai_chat_bot.pipeline_log import vprint as _vp
    _vp(f"[expand_groups] Before overlap resolution: {len(expanded)} devices expanded")
    expanded = _resolve_row_overlaps(expanded, no_abutment)
    _vp(f"[expand_groups] After overlap resolution: returning {len(expanded)} devices")
    
    # POST-EXPANSION VALIDATION: Check for duplicate positions
    pos_check = defaultdict(list)
    for n in expanded:
        x = n.get("geometry", {}).get("x", -1)
        y = n.get("geometry", {}).get("y", -1)
        pos_check[(x, y)].append(n.get("id", "?"))
    
    duplicates = {pos: ids for pos, ids in pos_check.items() if len(ids) > 1}
    if duplicates:
        _vp(f"[expand_groups] WARNING: {len(duplicates)} position(s) have multiple devices:")
        for pos, ids in list(duplicates.items())[:5]:
            _vp(f"  Position {pos}: {ids}")

    return expanded


def _resolve_row_overlaps(nodes: List[dict], no_abutment: bool = False) -> List[dict]:
    """
    Guarantee no two devices in the same row overlap while preserving hierarchy abutment.

    Performs a deterministic bucket sort on the final expanded transistor rows
    and aggressively forces the delta-x of different component clusters to be
    at least standard width apart.

    Parameters
    ----------
    nodes : List[dict]
        List of expanded atomic hardware element dictionaries with float coordinates.
    no_abutment : bool, optional
        Flag dictating whether intra-device overlaps can use tight diffusion sharing
        pitches. Defaults to False.

    Returns
    -------
    List[dict]
        Mutated non-overlapping device geometry array.
    """
    if not nodes:
        return nodes

    pitch_abut = STD_PITCH if no_abutment else FINGER_PITCH  # 0.294 or 0.070
    pitch_std  = STD_PITCH     # 0.294

    from ai_agent.ai_chat_bot.pipeline_log import vprint as _vp
    _vp(f"[resolve_overlaps] Starting with {len(nodes)} devices")

    # Group by (Y, type) — PMOS and NMOS are always in separate buckets
    type_rows: Dict[Tuple[float, str], List[dict]] = defaultdict(list)
    for n in nodes:
        y = round(n.get("geometry", {}).get("y", 0.0), 6)
        dev_type = n.get("type", "nmos")
        type_rows[(y, dev_type)].append(n)

    _vp(f"[resolve_overlaps] Found {len(type_rows)} type-rows")

    for (y_key, _dev_type), row_nodes in type_rows.items():
        # --- Step 1: Identify chains by parent name -----------------------
        chains: Dict[str, List[dict]] = defaultdict(list)
        for node in row_nodes:
            nid = node.get("id", "")
            parent = _transistor_key(nid)
            chains[parent].append(node)

        _vp(f"[resolve_overlaps] Row y={y_key} ({_dev_type}): {len(chains)} chains")

        # --- Step 2: Sort each chain's devices by their internal index ------
        for parent, chain_devices in chains.items():
            chain_devices.sort(key=lambda n: n.get("geometry", {}).get("x", 0.0))

        # --- Step 3: Sort chains by their leftmost device's original X ------
        sorted_chains = sorted(
            chains.values(),
            key=lambda ch: ch[0].get("geometry", {}).get("x", 0.0),
        )

        # --- Step 4: Place chains left-to-right, gap-preserving -----------
        if not sorted_chains:
            continue

        # Compute original chain footprints (first x → last x + width)
        # so we can detect overlaps and preserve intentional LLM gaps.
        chain_footprints: list[Tuple[float, float]] = []
        for chain in sorted_chains:
            xs = [d.get("geometry", {}).get("x", 0.0) for d in chain]
            widths = [d.get("geometry", {}).get("width", pitch_std) for d in chain]
            first_x = min(xs) if xs else 0.0
            last_x = max(xs) if xs else 0.0
            last_w = widths[xs.index(last_x)] if xs else pitch_std
            chain_footprints.append((first_x, last_x + last_w))

        min_inter_chain_gap = pitch_std
        cursor = chain_footprints[0][0]  # start at first chain's original X

        for chain_idx, chain in enumerate(sorted_chains):
            chain_first_orig, chain_last_orig = chain_footprints[chain_idx]

            # If this chain starts before the cursor (overlap), shift it right.
            # Otherwise preserve its original position (gap-preserving).
            if chain_idx > 0 and chain_first_orig < cursor:
                shift = round(cursor - chain_first_orig, 6)
                _vp(f"[resolve_overlaps] Shifting chain by +{shift:.4f} to resolve overlap")
                chain_start = cursor
            else:
                shift = 0.0
                chain_start = chain_first_orig

            for dev_idx, dev in enumerate(chain):
                geo = dev.get("geometry", {})
                geo["x"] = round(chain_start, 6)
                geo["y"] = round(float(y_key), 6)

                is_last = (dev_idx == len(chain) - 1)

                if not is_last:
                    # Within chain: abut spacing
                    chain_start = round(chain_start + pitch_abut, 6)
                    dev.setdefault("abutment", {})["abut_right"] = True
                    next_dev = chain[dev_idx + 1]
                    next_dev.setdefault("abutment", {})["abut_left"] = True
                else:
                    # End of chain: advance by device width for next chain
                    dev_w = geo.get("width", pitch_std)
                    if dev_w < pitch_std * 0.5:
                        dev_w = pitch_std
                    chain_start = round(chain_start + dev_w, 6)

            # Update cursor for next chain: enforce minimum gap
            cursor = round(max(chain_start, chain_last_orig + shift) + min_inter_chain_gap, 6)

        # Clean abutment flags for standalone (single-device) chains
        for chain in chains.values():
            if len(chain) == 1:
                dev = chain[0]
                abut = dev.get("abutment", {})
                dev["abutment"] = {
                    "abut_left":  abut.get("abut_left", False),
                    "abut_right": abut.get("abut_right", False),
                }

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

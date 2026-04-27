"""
File Description:
This module handles pre- and post-processing for the AI placer by collapsing individual finger/multiplier nodes into compact transistor-level representations for the LLM, and expanding them back to finger-level coordinates with correct abutment and matching rules.

Functions:
- _parse_id:
    - Role: Parses a node ID into (parent_name, multiplier_index, finger_index) supporting various naming conventions.
    - Inputs: 
        - node_id (str): The ID of the node to parse.
    - Outputs: (Tuple[str, int | None, int | None]) Parsed components.
- _transistor_key:
    - Role: Returns the logical transistor name (parent ID) for a finger node.
    - Inputs: 
        - node_id (str): The ID of the finger node.
    - Outputs: (str) The parent transistor ID.
- aggregate_to_logical_devices:
    - Role: Collapses multiple finger-level node dictionaries into single transistor-level groups.
    - Inputs: 
        - nodes (list): Raw graph nodes array.
        - edges (list, optional): Raw connection edge array.
    - Outputs: (list | tuple) Compacted nodes or a tuple of (nodes, edges, finger_map).
- _electrical_signature:
    - Role: Builds a hashable signature from a group node's electrical parameters to identify structurally identical devices.
    - Inputs: 
        - group_node (dict): The compacted transistor group node.
    - Outputs: (tuple) Electrical signature components.
- detect_matching_groups:
    - Role: Detects structurally matched transistor groups (pairs, clusters) using electrical signatures.
    - Inputs: 
        - group_nodes (list): Compacted group-level nodes.
        - group_edges (list): Compacted group-level edges.
    - Outputs: (dict) Mapping of matched pairs, clusters, and other topological features.
- _enrich_matching_info:
    - Role: Identifies differential pairs, cross-coupled latches, and tail sources using net topology.
    - Inputs: 
        - matching_info (dict): Dictionary to be updated with matching results.
        - group_terminal_nets (dict): Mapping of group ID to terminal nets.
        - group_nodes (list): List of group nodes.
    - Outputs: None
- build_matching_section:
    - Role: Constructs a human-readable summary of symmetry constraints for inclusion in the AI prompt.
    - Inputs: 
        - group_nodes (list): List of compact group nodes.
        - group_edges (list): List of compact edges.
        - group_terminal_nets (dict): Mapping of group IDs to terminal nets.
    - Outputs: (str) Formatted string describing symmetrical rules.
- build_finger_group_section:
    - Role: Constructs a human-readable inventory of transistor finger groups and their footprints for the AI prompt.
    - Inputs: 
        - finger_map (dict): Mapping of group ID to constituent fingers.
        - group_nodes (list): List of compact group nodes.
    - Outputs: (str) Formatted string describing transistor footprints.
- interdigitate_fingers:
    - Role: Generates an interleaved pattern (ABBA, ABAB, AABB) from two device finger lists.
    - Inputs: 
        - fingers_a (List[dict]): Fingers for device A.
        - fingers_b (List[dict]): Fingers for device B.
        - pattern (str): Interdigitation pattern name.
        - edge_dummies (bool): Whether to insert shielding dummies at edges.
    - Outputs: (List[dict]) Ordered, interleaved finger list.
- _generate_abba_pattern:
    - Role: Backward-compatible wrapper for ABBA interdigitation.
    - Inputs: 
        - fingers_a (List[dict]): Fingers for device A.
        - fingers_b (List[dict]): Fingers for device B.
    - Outputs: (List[dict]) Ordered ABBA finger list.
- _detect_current_mirrors:
    - Role: Identifies current mirror clusters sharing the same gate net where at least one is diode-connected.
    - Inputs: 
        - group_nodes (list): List of group nodes.
        - group_terminal_nets (dict): Mapping of group ID to terminal nets.
    - Outputs: (List[List[str]]) List of mirror cluster groups.
- merge_matched_groups:
    - Role: Merges symmetrical pairs or mirrors into fixed interdigitated blocks before AI placement.
    - Inputs: 
        - group_nodes (list), group_edges (list), finger_map (dict), matching_info (dict), group_terminal_nets (dict), terminal_nets (dict), no_abutment (bool).
    - Outputs: (Tuple[list, list, dict, dict]) Updated graph and finger data with merged blocks.
- detect_inter_group_abutment:
    - Role: Detects opportunities for abutment between different transistor groups sharing a non-power net.
    - Inputs: 
        - group_nodes (list), finger_map (dict), terminal_nets (dict).
    - Outputs: (List[dict]) List of abutment candidate pairs.
- _symmetry_order:
    - Role: Reorders groups within a row to center symmetrical structures (latches, diff pairs).
    - Inputs: 
        - groups (List[dict]), matching_info (dict), group_terminal_nets (dict).
    - Outputs: (List[dict]) Reordered list of groups.
- pre_assign_rows:
    - Role: Deterministically packs transistor groups into Y-rows and computes row heights.
    - Inputs: 
        - group_nodes (list), max_row_width (float), matching_info (dict), group_terminal_nets (dict).
    - Outputs: (Tuple[list, str]) Nodes with fixed Y-coordinates and a summary string for the prompt.
- _snap_to_row_grid:
    - Role: Quantizes a Y-coordinate to the nearest valid row grid level.
    - Inputs: 
        - y (float): Input Y-coordinate.
        - pitch (float | None): Row pitch.
    - Outputs: (float) Snapped Y-coordinate.
- expand_to_fingers:
    - Role: Expands compacted AI placement groups back into individual finger nodes with precise coordinates.
    - Inputs: 
        - group_placement (list), finger_map (dict), matching_info (dict), no_abutment (bool), original_group_nodes (dict).
    - Outputs: (list) Fully expanded list of finger nodes.
- _resolve_row_overlaps:
    - Role: Resolves horizontal overlaps within rows by shifting device chains while preserving abutment.
    - Inputs: 
        - nodes (List[dict]): Expanded finger nodes.
        - no_abutment (bool): If true, forces standard spacing between all devices.
    - Outputs: (List[dict]) Non-overlapping finger nodes.
"""

from __future__ import annotations

import re
import copy
from collections import defaultdict
from typing import Dict, List, Tuple

from ai_agent.utils.logging import vprint

# ---------------------------------------------------------------------------
# Constants — sourced from centralized design rules config
# ---------------------------------------------------------------------------
from config.design_rules import (
    PMOS_Y, NMOS_Y, ROW_PITCH, ROW_HEIGHT_UM, ROW_GAP_UM,
    FINGER_PITCH, PITCH_UM as STD_PITCH,
)

# Regex: split "MM9<3>_f4" -> ("MM9", "3", "4")  (legacy array-bus)
_BUS_RE   = re.compile(r'^(.+?)<(\d+)>(?:_f(\d+))?$')
# Regex: split "MM6_m2_f3" -> ("MM6", "2", "3")  (multiplier + finger)
_MULTI_FINGER_RE = re.compile(r'^(.+?)_m(\d+)_f(\d+)$')
# Regex: split "MM6_m3"    -> ("MM6", "3", None)  (multiplier/array only)
_MULTI_ONLY_RE = re.compile(r'^(.+?)_m(\d+)$')
# Regex: split "MM5_f2"    -> ("MM5", None, "2")  (finger-only, legacy)
_PLAIN_FINGER_RE = re.compile(r'^(.+?)_f(\d+)$')

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _node_height_um(node: dict, fallback: float = ROW_HEIGHT_UM) -> float:
    """Return the physical device height from geometry, with a PDK-safe fallback."""
    try:
        height = float(node.get("geometry", {}).get("height", 0.0))
    except (TypeError, ValueError):
        height = 0.0
    return height if height > 0 else fallback


def _row_step_um(row_or_nodes: List[dict]) -> float:
    """Origin-to-origin spacing needed after a row of devices.

    Uses the tallest device's height + ROW_GAP_UM.  When ROW_GAP_UM is
    0 the rows touch edge-to-edge with no gap.
    """
    if not row_or_nodes:
        return ROW_HEIGHT_UM
    row_height = max(_node_height_um(n) for n in row_or_nodes)
    return row_height + ROW_GAP_UM


def _is_dummy_node(node: dict) -> bool:
    """Return True for generated or explicit filler/dummy devices."""
    node_id = str(node.get("id", ""))
    return bool(
        node.get("is_dummy")
        or node_id.startswith(("FILLER_DUMMY_", "DUMMY_matrix_", "EDGE_DUMMY"))
    )


def _is_regenerated_filler_dummy(node: dict) -> bool:
    """Return True for non-structural fillers that can be regenerated safely."""
    return str(node.get("id", "")).startswith("FILLER_DUMMY_")


def legalize_vertical_rows(nodes: List[dict], row_gap: float | None = None) -> List[dict]:
    """Restack physical rows so their bounding boxes cannot overlap vertically.

    Earlier deterministic stages may mix fixed row pitches with real device
    heights.  This pass keeps each row's relative order and x placement, but
    shifts whole rows upward when the previous row's actual height would
    otherwise collide with it.
    """
    if not nodes:
        return nodes

    gap = ROW_GAP_UM if row_gap is None else float(row_gap)
    touch_epsilon = 0.000001
    rows: Dict[Tuple[float, str], List[dict]] = defaultdict(list)
    for node in nodes:
        geo = node.get("geometry", {})
        if not isinstance(geo, dict):
            continue
        try:
            y = round(float(geo.get("y", 0.0)), 6)
        except (TypeError, ValueError):
            continue
        dev_type = str(node.get("type", "")).strip().lower()
        rows[(y, dev_type)].append(node)

    type_order = {"nmos": 0, "res": 1, "cap": 1, "pmos": 2}
    cursor = None
    for (row_y, dev_type), row_nodes in sorted(
        rows.items(),
        key=lambda item: (item[0][0], type_order.get(item[0][1], 1), item[0][1]),
    ):
        if not row_nodes:
            continue
        row_height = max(_node_height_um(n) for n in row_nodes)
        new_y = row_y if cursor is None else max(row_y, cursor)
        if abs(new_y - row_y) > 1e-9:
            for node in row_nodes:
                node.setdefault("geometry", {})["y"] = round(new_y, 6)
        cursor = round(new_y + row_height + gap + touch_epsilon, 6)

    return nodes

def _parse_id(node_id: str) -> Tuple[str, int | None, int | None]:
    """
    Parse a node id into (parent_name, multiplier_index, finger_index).

    Handles multiple naming conventions:

    New naming (from updated parse_mos):
        "MM6_m2_f3"  -> ("MM6", 2, 3)    <- multiplier 2, finger 3
        "MM6_m3"     -> ("MM6", 3, None)  <- multiplier/array child 3
        "MM9_m8"     -> ("MM9", 8, None)  <- array copy 8
        "MM5_f2"     -> ("MM5", None, 2)  <- finger 2 (finger-only)
        "MM1"        -> ("MM1", None, None) <- single device

    Legacy array-bus naming (from old data / layout files):
        "MM9<3>_f4"  -> ("MM9", 3, 4)
        "MM9<3>"     -> ("MM9", 3, None)

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

    "MM9<3>_f4" -> "MM9"   (all multiplier copies + all fingers share one key)
    "MM5_f2"    -> "MM5"
    "MM6_f1"    -> "MM6"
    """
    parent, _, _ = _parse_id(node_id)
    return parent


# ---------------------------------------------------------------------------
# Public API — Step 1: GROUP
# ---------------------------------------------------------------------------

def aggregate_to_logical_devices(nodes: list, edges: list = None) -> list:
    """
    Collapse multiple finger-level node dictionaries into single transistor-level groups.

    Backward-compatible: if edges is None, returns just group_nodes (old behavior).
    If edges is provided, returns full tuple (group_nodes, group_edges, finger_map).

    Parameters
    ----------
    nodes : list
        The raw graph extracted nodes array from the UI schematic.
    edges : list, optional
        The raw connection edge array.

    Returns
    -------
    list  (when edges=None)
        Compact list of merged entities for the LLM.
    tuple (when edges provided)
        (group_nodes, group_edges, finger_map)
    """
    # --- 1. Bucket every finger node under its logical transistor key --------
    buckets: Dict[str, List[dict]] = defaultdict(list)
    for n in nodes:
        if _is_dummy_node(n):
            continue
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
            # True abutted width = (N-1)*FINGER_PITCH + STD_PITCH
            # This provides the LLM with an accurate physical footprint to
            # prevent false overlaps and improve macro-placement precision.
            "geometry": {
                "x":           0.0,
                "y":           PMOS_Y if dev_type == "pmos" else NMOS_Y,
                "width":       round((total_fingers - 1) * FINGER_PITCH + STD_PITCH, 6),
                "height":      rep.get("geometry", {}).get("height", 0.568),
                "orientation": "R0",
            },
        }

        # Copy block membership from rep if present
        if "block" in rep:
            group_node["block"] = rep["block"]

        group_nodes.append(group_node)
        finger_map[group_id] = members

    # --- 3. Build compact edges (group -> group) ------------------------------
    # We need to keep an edge if the two endpoint nodes belong to different
    # transistor groups and the net is not a power rail.
    _POWER = frozenset({"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS"})

    seen_edges: set[tuple] = set()
    group_edges = []
    id_to_group = {n["id"]: _transistor_key(n["id"]) for n in nodes}

    for e in (edges or []):
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

    if edges is None:
        return group_nodes
    return group_nodes, group_edges, finger_map


# Backward-compatible alias
group_fingers = aggregate_to_logical_devices


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
    Fill in the net-based ``diff_pairs``, ``cross_coupled``, ``load_pairs``,
    and ``tail_sources`` fields of *matching_info* (in-place) using terminal
    net analysis.

    Detects:
      - Differential pairs (VINP/VINN gated, same type)
      - Cross-coupled latches (D_A == G_B AND D_B == G_A, same type)
      - CLK-symmetric pairs: two same-type same-size devices whose gate is CLK
        and whose drain nets are the two sides of a symmetric latch/output
        (e.g. MM1/MM2 precharge PMOS in a comparator with D=VOUTN/VOUTP)
      - Load pairs (same-type devices whose drains connect to the diff pair
        output nets — typically PMOS active loads in a comparator)
      - Tail sources (devices sharing a non-power S/D net with the diff pair)
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
            # Same-type constraint: both must be NMOS or both PMOS
            type_a = grp_lookup.get(a, {}).get("type", "")
            type_b = grp_lookup.get(b, {}).get("type", "")
            if type_a == type_b:
                diff_pairs.append((a, b))
    matching_info["diff_pairs"] = diff_pairs

    # --- Cross-coupled pairs (D of A == G of B AND D of B == G of A) ------
    # Must be same device type (both NMOS or both PMOS) to form a valid latch
    cross_pairs: List[Tuple[str, str]] = []
    gids = list(group_terminal_nets.keys())
    for i, ga in enumerate(gids):
        for gb in gids[i + 1:]:
            ta = group_terminal_nets[ga]
            tb = group_terminal_nets[gb]
            type_a = grp_lookup.get(ga, {}).get("type", "")
            type_b = grp_lookup.get(gb, {}).get("type", "")
            if type_a != type_b:
                continue  # Cross-coupled must be same type
            if (ta.get("D") and ta.get("D") == tb.get("G") and
                    tb.get("D") and tb.get("D") == ta.get("G")):
                cross_pairs.append((ga, gb))
    matching_info["cross_coupled"] = cross_pairs

    # --- CLK-symmetric pairs: same-type, same-size, gate=CLK, symmetric drains
    # In a dynamic comparator: MM1 (D=VOUTN, G=CLK, S=VDD) and
    # MM2 (D=VOUTP, G=CLK, S=VDD) are precharge devices that must be
    # placed symmetrically (ABBA) around the vertical axis.
    # Detection: two same-type, same-signature devices share the CLK gate
    # AND their drain nets form a symmetric pair (each drain of one appears
    # as the gate of the other in any cross-coupled pair).
    clk_sym_pairs: List[Tuple[str, str]] = []
    cross_drain_nets: set = set()
    for ga, gb in cross_pairs:
        ta = group_terminal_nets.get(ga, {})
        tb = group_terminal_nets.get(gb, {})
        if ta.get("D"):
            cross_drain_nets.add(ta["D"])
        if tb.get("D"):
            cross_drain_nets.add(tb["D"])

    # Group CLK-gated devices by (type, electrical_signature)
    clk_by_sig: Dict[tuple, List[str]] = defaultdict(list)
    for gid, t in group_terminal_nets.items():
        g = t.get("G", "")
        if g.upper() == "CLK":
            node = grp_lookup.get(gid, {})
            sig = (_electrical_signature(node), node.get("type", ""))
            clk_by_sig[sig].append(gid)

    for sig, members in clk_by_sig.items():
        if len(members) < 2:
            continue
        # Only pair CLK devices whose drains BOTH feed the cross-coupled latch outputs
        for i, ma in enumerate(members):
            for mb in members[i + 1:]:
                ta = group_terminal_nets.get(ma, {})
                tb = group_terminal_nets.get(mb, {})
                d_a = ta.get("D", "")
                d_b = tb.get("D", "")
                # Both drains must go to the cross-coupled latch's output nets
                # AND must be different nets (left/right side of axis)
                if (d_a in cross_drain_nets and d_b in cross_drain_nets
                        and d_a != d_b):
                    clk_sym_pairs.append((ma, mb))

    # Remove duplicates with cross_pairs (avoid double-counting)
    cross_set = {frozenset(p) for p in cross_pairs}
    clk_sym_pairs = [p for p in clk_sym_pairs if frozenset(p) not in cross_set]
    matching_info["clk_sym_pairs"] = clk_sym_pairs

    # Extend diff_pairs with CLK-symmetric pairs so _symmetry_order treats
    # them as axis-centered symmetric structures (same ordering priority)
    # Keep the original diff_pairs and add clk_sym_pairs to the extended list
    matching_info["diff_pairs"] = diff_pairs + clk_sym_pairs

    # --- Load pairs: same-type devices whose drains connect to diff outputs
    # In a comparator, the PMOS loads have drains on VOUTP/VOUTN which are
    # the same nets as the diff pair's drains.
    diff_all = set(vinp_ids + vinn_ids)
    diff_drain_nets = set()
    for did in diff_all:
        t = group_terminal_nets.get(did, {})
        d = t.get("D", "")
        if d and d.upper() not in _POWER:
            diff_drain_nets.add(d)

    load_pairs: List[Tuple[str, str]] = []
    load_candidates = []
    for gid, t in group_terminal_nets.items():
        if gid in diff_all:
            continue
        d_net = t.get("D", "")
        if d_net in diff_drain_nets:
            load_candidates.append(gid)

    # Pair load candidates that have matching electrical signatures
    for i, la in enumerate(load_candidates):
        for lb in load_candidates[i + 1:]:
            sig_a = _electrical_signature(grp_lookup.get(la, {}))
            sig_b = _electrical_signature(grp_lookup.get(lb, {}))
            if sig_a == sig_b:
                load_pairs.append((la, lb))
    matching_info["load_pairs"] = load_pairs

    # --- Tail sources: share a non-power S/D net with the diff pair ------
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
    # Filter terminals to only include groups present in group_nodes
    # (Avoids 'phantom' devices that were merged into matched blocks)
    current_ids = {n["id"] for n in group_nodes}
    grp_terminals = {gid: t for gid, t in group_terminal_nets.items() if gid in current_ids}

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
    # its partner (VINP <-> VINN is the classic diff pair).
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
                    "ABBA_diff_pair":          "ABBA interdigitated diff pair",
                    "ABBA_current_mirror":     "ABBA interdigitated current mirror",
                    "ABAB_load_pair":          "ABAB interdigitated active load pair",
                    "symmetric_cross_coupled": "symmetric cross-coupled latch pair",
                    "common_centroid_mirror":  "2D common centroid current mirror",
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
                partner = gn.get("_matching_partner")
                if partner:
                    # Single-finger unmerged symmetry pair
                    lines.append(
                        f"  {grp_id:<30}  [FREE SYMMETRIC DEVICE]"
                    )
                    lines.append(
                        f"    Symmetric partner: {partner}  (topology: {gn.get('_technique','matched')})"
                    )
                    lines.append(
                        f"    total_fingers={tot}  footprint={w:.3f} um"
                    )
                    lines.append(
                        f"    ** Place symmetrically: one on each SIDE of the center axis. **"
                    )
                    lines.append(
                        f"    ** May share a row with adjacent unmatched devices (e.g. tail switch). **"
                    )
                else:
                    lines.append(
                        f"  {grp_id:<12}  nf={nf}  m={m}  "
                        f"total_fingers={tot}  "
                        f"footprint={w:.3f} um  "
                        f"(place fingers at X, X+0.070, X+0.140, ...)"
                    )

    lines.append(
        "LLM TASK: Assign an origin X and use the pre-assigned Y from the "
        "ROW ASSIGNMENT table for EACH GROUP/BLOCK. Multiple PMOS rows and "
        "multiple NMOS rows are allowed. Matched blocks are FIXED — just "
        "assign their origin X, do NOT re-order their internal fingers.\n"
        "CRITICAL: Do NOT attempt to place individual fingers or multipliers "
        "(e.g., do not output MM10_m1). You MUST output ONE move command per "
        "group ID exactly as listed above."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Matching techniques: ABBA interdigitation + fixed block merging
# ---------------------------------------------------------------------------

def interdigitate_fingers(
        fingers_a: List[dict],
        fingers_b: List[dict],
        pattern: str = "ABBA",
        edge_dummies: bool = True,
) -> List[dict]:
    """
    Generate an interdigitation pattern from two device finger lists.

    Supports multiple professional motifs:
      - "ABBA": Classic gradient-cancelling motif (A B B A | A B B A ...).
                Best for linear oxide/thermal gradient cancellation.
      - "ABAB": Alternating motif (A B A B A B ...).
                Best for minimizing parasitic capacitance differences when
                common-source abutment is prioritised.
      - "AABB": Paired motif (A A B B | A A B B ...).
                Preferred for high-frequency pairs where dense crossover
                routing is detrimental.

    When ``edge_dummies=True``, the function automatically inserts shielding
    dummy fingers at the far-left and far-right of the resulting array.
    These dummies ensure the active fingers see identical lithographic
    micro-loading (etch symmetry) from both edges.

    For unequal finger counts the shorter list is padded with ``is_dummy=True``
    copies so that the motif is always perfectly balanced.

    Parameters
    ----------
    fingers_a : List[dict]
        Raw finger nodes for Device A.
    fingers_b : List[dict]
        Raw finger nodes for Device B.
    pattern : str
        One of "ABBA", "ABAB", "AABB". Defaults to "ABBA".
    edge_dummies : bool
        If True, prepend and append a dummy finger for edge shielding.

    Returns
    -------
    List[dict]
        Ordered, interleaved list of shallow-copied finger dictionaries.
        Each dict gets an injected ``_match_owner`` key ("A" or "B") for
        debug tracing, and dummies get ``is_dummy=True``.
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

    # Pad shorter list with dummy fingers for perfect balance
    while len(a_list) < len(b_list):
        dummy = dict(a_list[-1]) if a_list else {}
        dummy["id"] = dummy.get("id", "dummy") + f"_dummy_{len(a_list)}"
        dummy["is_dummy"] = True
        dummy["_match_owner"] = "A"
        a_list.append(dummy)

    while len(b_list) < len(a_list):
        dummy = dict(b_list[-1]) if b_list else {}
        dummy["id"] = dummy.get("id", "dummy") + f"_dummy_{len(b_list)}"
        dummy["is_dummy"] = True
        dummy["_match_owner"] = "B"
        b_list.append(dummy)

    n = len(a_list)
    result: List[dict] = []

    pattern_upper = pattern.upper()

    if pattern_upper == "ABBA":
        # Classic gradient-cancelling: A B B A | A B B A ...
        i = 0
        while i < n:
            if i + 1 < n:
                result.append(a_list[i])
                result.append(b_list[i])
                result.append(b_list[i + 1])
                result.append(a_list[i + 1])
                i += 2
            else:
                result.append(a_list[i])
                result.append(b_list[i])
                i += 1

    elif pattern_upper == "ABAB":
        # Alternating: A B A B A B ...
        for i in range(n):
            result.append(a_list[i])
            result.append(b_list[i])

    elif pattern_upper == "AABB":
        # Paired: A A B B | A A B B ...
        i = 0
        while i < n:
            if i + 1 < n:
                result.append(a_list[i])
                result.append(a_list[i + 1])
                result.append(b_list[i])
                result.append(b_list[i + 1])
                i += 2
            else:
                result.append(a_list[i])
                result.append(b_list[i])
                i += 1
    else:
        # Fallback to ABBA
        vprint(f"[interdigitate] Unknown pattern '{pattern}', falling back to ABBA")
        return interdigitate_fingers(fingers_a, fingers_b, "ABBA", edge_dummies)

    # -- Edge-effect dummy shielding --------------------------------------
    # Insert a dummy finger at each end to ensure the outermost active
    # fingers see identical lithographic etch environment from both sides.
    if edge_dummies and result:
        import copy
        left_dummy = dict(result[0])
        left_dummy["id"] = "EDGE_DUMMY_L"
        left_dummy["is_dummy"] = True
        left_dummy["_match_owner"] = "edge"
        if "electrical" in left_dummy:
            left_dummy["electrical"] = copy.deepcopy(left_dummy["electrical"])
            for k in ["parent", "m", "multiplier_index", "finger_index", "array_index"]:
                left_dummy["electrical"].pop(k, None)

        right_dummy = dict(result[-1])
        right_dummy["id"] = "EDGE_DUMMY_R"
        right_dummy["is_dummy"] = True
        right_dummy["_match_owner"] = "edge"
        if "electrical" in right_dummy:
            right_dummy["electrical"] = copy.deepcopy(right_dummy["electrical"])
            for k in ["parent", "m", "multiplier_index", "finger_index", "array_index"]:
                right_dummy["electrical"].pop(k, None)

        result = [left_dummy] + result + [right_dummy]

    return result


# Keep backward-compatible alias
def _generate_abba_pattern(
        fingers_a: List[dict],
        fingers_b: List[dict],
) -> List[dict]:
    """Backward-compatible wrapper — delegates to interdigitate_fingers(ABBA)."""
    return interdigitate_fingers(fingers_a, fingers_b, pattern="ABBA", edge_dummies=True)



def _detect_current_mirrors(
        group_nodes: list,
        group_terminal_nets: dict,
) -> List[List[str]]:
    """
    Detect current mirror clusters (diode + copies).

    A current mirror cluster is identified when:
      - Multiple same-type groups share the same Gate net
      - That gate net is also the Drain of at least one of them (diode-connected)
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

    mirror_clusters: List[List[str]] = []
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
            mirror_clusters.append(members)
    return mirror_clusters


def merge_matched_groups(
        group_nodes: list,
        group_edges: list,
        finger_map: dict,
        matching_info: dict,
        group_terminal_nets: dict,
        terminal_nets: dict,
        no_abutment: bool = False
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

    # Collect all clusters to merge (diff pairs + current mirrors + cross-coupled)
    clusters_to_merge: List[Tuple[List[str], str]] = []  # (members, technique)
    used: set = set()

    # 1. Diff pairs (highest priority)
    for a, b in matching_info.get("diff_pairs", []):
        if a not in used and b not in used:
            clusters_to_merge.append(([a, b], "ABBA_diff_pair"))
            used.update([a, b])

    # 2. Current mirrors
    cm_clusters = _detect_current_mirrors(group_nodes, group_terminal_nets)
    for cluster in cm_clusters:
        if all(m not in used for m in cluster):
            clusters_to_merge.append((cluster, "common_centroid_mirror"))
            used.update(cluster)

    # 3. Cross-coupled pairs (symmetric mirror — reflection constraint)
    for a, b in matching_info.get("cross_coupled", []):
        if a not in used and b not in used:
            clusters_to_merge.append(([a, b], "symmetric_cross_coupled"))
            used.update([a, b])

    # 4. Load pairs (ABAB interdigitation for matched active loads)
    for a, b in matching_info.get("load_pairs", []):
        if a not in used and b not in used:
            clusters_to_merge.append(([a, b], "ABAB_load_pair"))
            used.update([a, b])

    if not clusters_to_merge:
        return group_nodes, group_edges, finger_map, {}

    # --- Build merged blocks ------------------------------------------------
    grp_lookup = {n["id"]: n for n in group_nodes}
    new_group_nodes = []
    new_finger_map: dict = {}
    merged_blocks: dict = {}

    merged_ids: set = set()

    for members, technique in clusters_to_merge:
        valid_members = [m for m in members if m in grp_lookup]
        if len(valid_members) != len(members) or len(members) < 2:
            continue

        if technique == "common_centroid_mirror":
            # Phase 2: 2D Matrix Generation
            from ai_agent.placement.centroid_generator import generate_common_centroid_matrix
            dev_infos = []
            total_fingers = 0
            for mid in members:
                fm = finger_map.get(mid, [])
                dev_infos.append({"id": mid, "fingers": len(fm)})
                total_fingers += len(fm)

            matrix_data = generate_common_centroid_matrix(dev_infos)
            # Flatten interleaved fingers based on matrix
            interleaved = []
            for mid in members:
                interleaved.extend(finger_map.get(mid, []))

            use_abutment = True
            block_pitch = FINGER_PITCH
            # Width = cols * pitch, Height = rows * row_height
            block_width = round(matrix_data["cols"] * block_pitch, 6)
            block_height = round(matrix_data["rows"] * 0.668, 6)

            grp_a_id = members[0]
            grp_a = grp_lookup[grp_a_id]
            block_id = "_".join(members[:2]) + f"_and_{len(members)-2}_more_centroid" if len(members) > 2 else "_".join(members) + "_centroid"

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
                    "height":      block_height,
                    "orientation": "R0",
                },
                "_matched_block": True,
                "_block_pitch":   block_pitch,
                "_members":       members,
                "_technique":     technique,
                "_matrix_data":   matrix_data,
            }

            if "block" in grp_a:
                block_node["block"] = grp_a["block"]

            new_group_nodes.append(block_node)
            new_finger_map[block_id] = interleaved
            merged_blocks[block_id] = {
                "members": members,
                "technique": technique,
                "use_abutment": use_abutment,
                "total_fingers": total_fingers,
                "block_pitch": block_pitch,
                "matrix_data": matrix_data
            }
            merged_ids.update(members)
            continue

        # Normal 1D pair handling
        grp_a_id, grp_b_id = members[0], members[1]
        grp_a = grp_lookup[grp_a_id]
        grp_b = grp_lookup[grp_b_id]
        fingers_a = finger_map.get(grp_a_id, [])
        fingers_b = finger_map.get(grp_b_id, [])

        if not fingers_a or not fingers_b:
            continue

        # ── Single-finger guard ─────────────────────────────────────────────
        # Interdigitation is only meaningful when each device has ≥ 2 fingers.
        # A 1-finger device (nf=1, m=1) cannot be interdigitated — the only
        # matching benefit for single-finger pairs is symmetric PLACEMENT
        # (left/right of the symmetry axis), which the deterministic axis-
        # centering already guarantees without merging.
        #
        # Keeping them UNMERGED lets the AI/row assigner pack them beside
        # other devices in the same tier (e.g., MM6 and MM7 beside MM10),
        # improving area utilization by eliminating a dedicated dummy-padded
        # row for a 2-finger block.
        nf_a = len(fingers_a)
        nf_b = len(fingers_b)
        if nf_a == 1 and nf_b == 1:
            # Mark both original group nodes as topological partners.
            # These nodes live in grp_lookup (original list), NOT in new_group_nodes
            # (which only holds fully merged blocks). Write directly to grp_lookup.
            grp_lookup[grp_a_id]["_matching_partner"] = grp_b_id
            grp_lookup[grp_a_id]["_technique"]        = technique
            grp_lookup[grp_b_id]["_matching_partner"] = grp_a_id
            grp_lookup[grp_b_id]["_technique"]        = technique
            vprint(
                f"[merge_matched] SKIP {grp_a_id}+{grp_b_id}: single-finger pair "
                f"({technique}) — no interdigitation possible, keeping as free devices"
            )
            continue   # leave both groups unmerged

        # Generate interleaved pattern based on technique
        if technique == "ABBA_diff_pair":
            interleaved = interdigitate_fingers(fingers_a, fingers_b, pattern="ABBA", edge_dummies=True)
        elif technique == "ABAB_load_pair":
            interleaved = interdigitate_fingers(fingers_a, fingers_b, pattern="ABAB", edge_dummies=True)
        elif technique == "symmetric_cross_coupled":
            # Cross-coupled latch: use ABBA for perfect vertical symmetry
            # with reflection constraint enforced downstream in the healer
            interleaved = interdigitate_fingers(fingers_a, fingers_b, pattern="ABBA", edge_dummies=False)
        else:
            # Generic fallback: ABBA with edge dummies
            interleaved = interdigitate_fingers(fingers_a, fingers_b, pattern="ABBA", edge_dummies=True)

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
        # If they share S/D nets -> use FINGER_PITCH (abutted), else STD_PITCH
        # Unless no_abutment is globally forced
        use_abutment = (len(shared_sd) > 0) and not no_abutment

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
            # Propagate connectivity for downstream matching analysts
            "terminal_nets": group_terminal_nets.get(grp_a_id, {}),
            # Mark this as a fixed matched block
            "_matched_block": True,
            "_block_pitch":   block_pitch,
            "_members":       [grp_a_id, grp_b_id],
            "_technique":     technique,
            # 2D fold eligibility: ABBA diff pairs with equal, even finger counts
            # can be folded into 2 rows to halve layout width and reduce dummies.
            # The actual 1D vs 2D decision is made later by _choose_fold_config.
            "_can_fold":      technique in ("ABBA_diff_pair", "ABAB_load_pair")
                              and len(fingers_a) == len(fingers_b)
                              and len(fingers_a) >= 4
                              and len(fingers_a) % 2 == 0,
            "_fingers_a":     fingers_a,   # kept for 2D matrix generation
            "_fingers_b":     fingers_b,
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
    group_nodes   : compact group node dicts from ``aggregate_to_logical_devices``
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

MAX_ROW_WIDTH = 8.0   # um — trigger a new row when a type's total footprint exceeds this


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
            # If this row has NO diff pair (it's a pure latch+tail row),
            # treat the tail as a center device so _assign_row_x anchors it
            # on the symmetry axis (odd-finger tail sources are axis devices).
            if not near_left and not near_right:
                center_ids.append(t)   # goes right of cross-coupled at axis
            elif len(tail_left) <= len(tail_right):
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


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic 1D vs 2D fold decision
# ─────────────────────────────────────────────────────────────────────────────

def _choose_fold_config(
    all_groups: List[dict],
    pitch: float = STD_PITCH,
    target_aspect: float = 1.3,
    aspect_lambda: float = 50.0,
) -> List[dict]:
    """
    Decide, for each ABBA matched block, whether to keep it as a 1D row or
    fold it into a 2D matrix across 2 physical rows.

    **Decision is layout-global and ASPECT-RATIO-AWARE** — it jointly minimises:
      1. Total dummy/filler cells across all rows (utilization objective)
      2. Deviation of the layout aspect ratio from ``target_aspect``
         (shape objective — prevents folding from creating tall portrait layouts)

    Cost function for each candidate fold mask:
        cost = dummy_count + λ × |layout_w / layout_h - target_aspect|

    Where:
        layout_w = max(group_widths in mask)
        layout_h = Σ (n_rows × row_pitch) for all groups in mask
        λ = ``aspect_lambda`` (weight for aspect-ratio term)

    This dual objective correctly handles mixed circuits:
    - A block that is already near the target width → NOT folded (folding adds rows
      without reducing width → worse aspect ratio)
    - A block that is much wider than other rows → folded (reduces width, few extra rows)
    - Result: some blocks stay 1D, others become 2D — per-circuit, per-block decision.

    Parameters
    ----------
    all_groups : list
        All group-level nodes (merged + unmerged), WITH ``geometry.width`` set.
    pitch : float
        STD_PITCH used for width/dummy calculation.
    target_aspect : float
        Target width/height ratio (default 1.3 = slightly wider than tall).
        Analog layouts are typically 1.0–2.0. Set to 1.0 for perfectly square.
    aspect_lambda : float
        Weight for aspect ratio deviation vs dummy count.
        Higher = more aggressive aspect ratio enforcement.
        Lower = more dummy-reduction focused.

    Returns
    -------
    list
        Modified ``all_groups`` with ``_n_rows`` set on each group.
    """
    import copy as _copy
    import math as _math

    ROW_PITCH_UM = ROW_HEIGHT_UM   # height of one physical row (incl. guard rings)

    # ── Helpers ─────────────────────────────────────────────────────────────
    def _group_width(g: dict) -> float:
        return g.get("geometry", {}).get("width", 0.0)

    def _group_rows(g: dict) -> int:
        return g.get("_n_rows", 1)

    def _score(groups: List[dict]) -> float:
        """Compute the combined dummy + aspect-ratio cost for a configuration."""
        if not groups:
            return 0.0
        layout_w = max(_group_width(g) for g in groups)
        layout_h = sum(_group_rows(g) * ROW_PITCH_UM for g in groups)
        if layout_h == 0 or layout_w == 0:
            return 0.0
        # Dummy count
        dummy = sum(
            max(0.0, (layout_w - _group_width(g)) / pitch) * _group_rows(g)
            for g in groups
        )
        # Aspect ratio deviation (width/height should equal target)
        actual_aspect = layout_w / layout_h
        aspect_dev = abs(actual_aspect - target_aspect)
        return dummy + aspect_lambda * aspect_dev

    # ── Initialise ───────────────────────────────────────────────────────────
    groups = [_copy.copy(g) for g in all_groups]
    for g in groups:
        if "_n_rows" not in g:
            g["_n_rows"] = 1

    # ── Compute folded widths for each candidate ─────────────────────────────
    fold_map: dict = {}   # group_id -> folded_width
    for g in groups:
        if not g.get("_can_fold", False):
            continue
        current_w  = _group_width(g)
        total_cols = _math.ceil(current_w / pitch)
        folded_cols = _math.ceil(total_cols / 2)
        real_per_row = folded_cols - 2
        if real_per_row < 2:
            continue   # too narrow after fold — ABBA pattern would break
        fold_map[g["id"]] = round(folded_cols * pitch, 6)

    if not fold_map:
        return groups   # no foldable blocks → no decision needed

    # ── Exhaustive search over all 2^N subsets ───────────────────────────────
    # For N ≤ 8 candidates (covers all realistic analog circuits), 2^8=256
    # combinations are checked instantly.
    candidates = [g for g in groups if g["id"] in fold_map]
    n_cand     = len(candidates)
    cand_idx   = {g["id"]: i for i, g in enumerate(candidates)}

    baseline_score = _score(groups)
    best_score     = baseline_score
    best_mask      = 0   # 0 = no fold (baseline)

    for mask in range(1, 1 << n_cand):
        sim = []
        for g in groups:
            bit = cand_idx.get(g["id"])
            if bit is not None and (mask >> bit) & 1:
                g_sim = _copy.copy(g)
                g_sim["_n_rows"]  = 2
                g_sim["geometry"] = _copy.copy(g["geometry"])
                g_sim["geometry"]["width"] = fold_map[g["id"]]
                sim.append(g_sim)
            else:
                sim.append(g)

        s = _score(sim)
        if s < best_score - 1e-6:
            best_score = s
            best_mask  = mask

    # ── Diagnostics ──────────────────────────────────────────────────────────
    # Compute what layout_w and layout_h will be under the chosen mask
    _sim_final = []
    for g in groups:
        bit = cand_idx.get(g["id"])
        if bit is not None and (best_mask >> bit) & 1:
            g_sim = _copy.copy(g)
            g_sim["_n_rows"]  = 2
            g_sim["geometry"] = _copy.copy(g["geometry"])
            g_sim["geometry"]["width"] = fold_map[g["id"]]
            _sim_final.append(g_sim)
        else:
            _sim_final.append(g)

    _lw = max(_group_width(g) for g in _sim_final) if _sim_final else 0
    _lh = sum(_group_rows(g) * ROW_PITCH_UM for g in _sim_final) if _sim_final else 0
    _asp = _lw / _lh if _lh > 0 else 0

    vprint(
        f"[fold_config] baseline_score={baseline_score:.2f} -> best_score={best_score:.2f} "
        f"| mask={bin(best_mask)} "
        f"| predicted W={_lw:.3f}um H={_lh:.3f}um aspect={_asp:.2f} (target={target_aspect})"
    )

    # ── Apply the winning configuration ─────────────────────────────────────
    if best_mask == 0:
        vprint("[fold_config] No fold improves the combined score — keeping all 1D")
        return groups

    baseline_dummy = sum(
        max(0.0, (max(_group_width(g) for g in groups) - _group_width(g)) / pitch)
        * _group_rows(g)
        for g in groups
    )

    for gid, idx in cand_idx.items():
        if (best_mask >> idx) & 1:
            g = next(gg for gg in groups if gg["id"] == gid)
            old_w = _group_width(g)
            new_w = fold_map[gid]
            g["_n_rows"]            = 2
            g["geometry"]           = _copy.copy(g["geometry"])
            g["geometry"]["width"]  = new_w
            g["_folded_cols"]       = _math.ceil(old_w / pitch) // 2 + 1
            g["_fold_real_per_row"] = g["_folded_cols"] - 2
            vprint(f"[fold_config] Fold {gid}: 1D {old_w:.3f}um -> 2D {new_w:.3f}um x2rows")

    return groups




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

    # ── Step 0: Dynamic 1D vs 2D fold decision ────────────────────────────
    # _choose_fold_config evaluates the entire layout (all group widths)
    # and decides which ABBA blocks benefit from being folded into 2 rows.
    # This is NOT a hard-coded threshold — it computes the fold that
    # minimises total dummy/filler cells across all rows.
    group_nodes = _choose_fold_config(group_nodes, pitch=STD_PITCH)
    # For groups that were folded (_n_rows=2), generate the 2D matrix now
    # so expand_to_fingers uses the matrix path automatically.
    from ai_agent.placement.centroid_generator import generate_2d_abba_matrix
    for g in group_nodes:
        if g.get("_n_rows", 1) >= 2 and g.get("_can_fold") and "_matrix_data" not in g:
            fa = g.pop("_fingers_a", [])
            fb = g.pop("_fingers_b", [])
            if fa and fb:
                g["_matrix_data"] = generate_2d_abba_matrix(fa, fb, n_rows=2)
                vprint(
                    f"[pre_assign_rows] 2D matrix for {g['id']}: "
                    f"{g['_matrix_data']['rows']}×{g['_matrix_data']['cols']} "
                    f"(width={g['geometry']['width']:.3f}µm)"
                )
        else:
            # Not folded — discard the stored finger lists (no longer needed)
            g.pop("_fingers_a", None)
            g.pop("_fingers_b", None)

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
    # close to the narrower type's width -> rectangular layout.
    # The narrower type keeps its original max_row_width (stays in 1 row).
    pmos_max = max_row_width
    nmos_max = max_row_width

    if pmos_total > 0 and nmos_total > 0:
        wider  = max(pmos_total, nmos_total)
        narrow = min(pmos_total, nmos_total)
        # Lower threshold to 1.15 so even moderate imbalances trigger row splitting.
        # For a comparator: 6 PMOS groups * 8 fingers * STD_PITCH approx 14.1 um PMOS
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

    # ── Topology-aware NMOS row split ──────────────────────────────────────
    # Diff-pair ABBA blocks (e.g. MM8+MM9) MUST be in their own row so the
    # symmetry enforcer can center the interdigitated block perfectly.
    # Latch (cross-coupled) + tail devices go to a separate row above the
    # diff pair row.  This split is DETERMINISTIC — it does not rely on the
    # LLM placing things symmetrically.
    #
    # Detection: a group is a "diff pair block" if its _technique is
    # "ABBA_diff_pair".  For PMOS, "ABBA_diff_pair" precharge blocks also
    # get their own row to keep the precharge pair symmetric.
    def _is_diff_pair_block(g: dict) -> bool:
        return g.get("_technique", "") == "ABBA_diff_pair"

    nmos_diff_pair = [g for g in nmos_groups if _is_diff_pair_block(g)]
    nmos_other     = [g for g in nmos_groups if not _is_diff_pair_block(g)]

    pmos_diff_pair = [g for g in pmos_groups if _is_diff_pair_block(g)]
    pmos_other     = [g for g in pmos_groups if not _is_diff_pair_block(g)]

    if nmos_diff_pair:
        # Three-tier NMOS row split for dynamic comparators:
        #   Row 0 (bottom): ABBA diff-pair block(s) — input pair, centered
        #   Row 1 (middle): cross-coupled latch block(s) — centered
        #   Row 2 (top):    tail/switch device(s) — centered
        #
        # This gives 3 cleanly symmetric NMOS rows instead of mixing
        # latch+tail in one row, which causes MM10 to appear asymmetrically.
        def _is_cross_coupled(g: dict) -> bool:
            return g.get("_technique", "") in ("symmetric_cross_coupled", "common_centroid_mirror")

        def _is_tail(g: dict, matching_info: dict) -> bool:
            """True if this group is a tail/axis device (in tail_sources list)."""
            tail_ids = matching_info.get("tail_sources", []) if matching_info else []
            # Also check by _technique absence (not a matched block but an axis device)
            return g["id"] in tail_ids

        def _is_single_finger_unmerged(g: dict) -> bool:
            """True for single-finger cross-coupled devices skipped during merge.

            Detected by finger count + technique alone — does NOT require
            _matching_partner flag (as a safety measure since _technique is now
            reliably set by the skip guard directly on grp_lookup).
            """
            # Must be cross-coupled topology
            if not _is_cross_coupled(g):
                return False
            # Must NOT be a merged block
            if g.get("_matched_block", False):
                return False
            # Check total finger count from electrical dict (always set by group_fingers)
            total = g.get("electrical", {}).get("total_fingers", 1)
            return total <= 1

        # Split cross-coupled groups:
        #   - Multi-finger merged latch blocks → their own row (common centroid needed)
        #   - Single-finger unmerged partners → join the tail row beside MM10
        nmos_latch_real   = [g for g in nmos_other
                             if _is_cross_coupled(g) and not _is_single_finger_unmerged(g)]
        nmos_latch_singlef = [g for g in nmos_other
                              if _is_cross_coupled(g) and _is_single_finger_unmerged(g)]
        nmos_tail  = [g for g in nmos_other if _is_tail(g, matching_info)]
        nmos_misc  = [g for g in nmos_other
                      if not _is_cross_coupled(g) and not _is_tail(g, matching_info)]

        # Single-finger latch devices join the tail row for better packing.
        # Target row order: [MM7 | MM10 | MM6]
        #   — one cross-coupled device on each side of the tail device.
        # Build the tail row manually for symmetric ordering:
        nmos_tail_row_candidates = nmos_tail + nmos_misc + nmos_latch_singlef

        nmos_rows_diff  = _bin_pack(nmos_diff_pair, nmos_max)
        nmos_rows_latch = _bin_pack(nmos_latch_real, nmos_max) if nmos_latch_real else []

        if nmos_tail_row_candidates:
            # Use bin-pack first, then reorder each row for symmetric flanking:
            # If this row contains both tail devices and single-finger cross-coupled
            # partners, reorder to [left_partner ... | center_tail | ... right_partner].
            nmos_rows_tail = _bin_pack(nmos_tail_row_candidates, nmos_max)
            for row_idx, row in enumerate(nmos_rows_tail):
                _tail_devs     = [g for g in row if _is_tail(g, matching_info) or
                                  (not _is_cross_coupled(g) and not _is_tail(g, matching_info))]
                _singlef_devs  = [g for g in row if _is_single_finger_unmerged(g)]
                if _singlef_devs and _tail_devs:
                    # Symmetric: put half of single-finger devices on each side of tail
                    mid   = len(_singlef_devs) // 2
                    left  = _singlef_devs[:mid] or []
                    right = _singlef_devs[mid:] or []
                    nmos_rows_tail[row_idx] = left + _tail_devs + right
                    vprint(
                        f"[pre_assign_rows] Tail row {row_idx} reordered: "
                        f"[{' '.join(g['id'] for g in left)}] | "
                        f"[{' '.join(g['id'] for g in _tail_devs)}] | "
                        f"[{' '.join(g['id'] for g in right)}]"
                    )
        else:
            nmos_rows_tail = []

        # Order bottom-to-top: diff pair | latch | tail+single-finger-latch
        nmos_rows = nmos_rows_diff + nmos_rows_latch + nmos_rows_tail
        vprint(f"[pre_assign_rows] NMOS 3-tier split: "
               f"diff={len(nmos_diff_pair)} latch={len(nmos_latch_real)} "
               f"tail={len(nmos_tail+nmos_misc)} "
               f"(+{len(nmos_latch_singlef)} single-finger latch device(s) moved to tail row)")
    else:
        nmos_rows = _bin_pack(nmos_groups, nmos_max)

    if pmos_diff_pair:
        pmos_rows_diff  = _bin_pack(pmos_diff_pair, pmos_max)
        pmos_rows_other = _bin_pack(pmos_other, pmos_max) if pmos_other else []
        # PMOS order: latch/load rows first (bottom of PMOS), precharge pair above
        pmos_rows = pmos_rows_other + pmos_rows_diff
        vprint(f"[pre_assign_rows] PMOS split: "
               f"{len(pmos_diff_pair)} diff-pair block(s) in {len(pmos_rows_diff)} row(s) + "
               f"{len(pmos_other)} other block(s) in {len(pmos_rows_other)} row(s)")
    else:
        pmos_rows = _bin_pack(pmos_groups, pmos_max)

    num_nmos = len(nmos_rows)

    # --- Symmetry-aware reordering within each row -----------------------
    if matching_info and group_terminal_nets:
        for i, row in enumerate(nmos_rows):
            nmos_rows[i] = _symmetry_order(row, matching_info, group_terminal_nets)
        for i, row in enumerate(pmos_rows):
            pmos_rows[i] = _symmetry_order(row, matching_info, group_terminal_nets)

    def _row_height(row: list) -> float:
        return _row_step_um(row)

    # ── Build group_id → (Y, X) maps ─────────────────────────────────────
    # Y: deterministic stacking (NMOS bottom, PMOS top)
    # X: deterministic centering — each row is centered on a shared x_axis
    #    so the layout is symmetric WITHOUT depending on the LLM.
    y_map: Dict[str, float] = {}
    x_map: Dict[str, float] = {}
    current_y = 0.0

    all_rows = []  # for X centering calculation

    nmos_row_ys = []
    for row_idx, row in enumerate(nmos_rows):
        nmos_row_ys.append(current_y)
        for g in row:
            y_map[g["id"]] = current_y
        all_rows.append(row)
        # 2D blocks consume n_rows Y slots
        max_n_rows = max((g.get("_n_rows", 1) for g in row), default=1)
        current_y += _row_height(row) * max_n_rows

    pmos_row_ys = []
    for row_idx, row in enumerate(pmos_rows):
        pmos_row_ys.append(current_y)
        for g in row:
            y_map[g["id"]] = current_y
        all_rows.append(row)
        # 2D blocks consume n_rows Y slots
        max_n_rows = max((g.get("_n_rows", 1) for g in row), default=1)
        current_y += _row_height(row) * max_n_rows

    passive_y = current_y
    for g in passive_groups:
        y_map[g["id"]] = passive_y

    # --- Deterministic X centering ----------------------------------------
    # Compute the layout width as the widest row total footprint
    def _row_total_w(row: list) -> float:
        if not row:
            return 0.0
        total = sum(g.get("geometry", {}).get("width", 0.0) for g in row)
        total += STD_PITCH * max(0, len(row) - 1)
        return total

    layout_width = max((_row_total_w(r) for r in all_rows), default=0.0)
    layout_width = max(layout_width, STD_PITCH)   # safety floor
    x_axis = layout_width / 2.0

    def _assign_row_x(row: list) -> None:
        """
        Assign x_map positions for groups in a single row.

        Strategy:
        - If the row contains exactly one group: center it on x_axis.
        - If the row has a 'cross-coupled' or ABBA block at position idx:
            anchor that block's centre on x_axis; place remaining groups
            to the left (then right) of it symmetrically.
        - Otherwise: center the full row cluster on x_axis.
        """
        if not row:
            return

        # Identify the "axis anchor": prefer a cross-coupled/ABBA merged block
        anchor_idx = None
        for i, g in enumerate(row):
            tech = g.get("_technique", "")
            if tech in ("symmetric_cross_coupled", "ABBA_diff_pair", "common_centroid_mirror"):
                anchor_idx = i
                break

        if anchor_idx is None or len(row) == 1:
            # No special anchor: just center the whole row cluster
            row_w = _row_total_w(row)
            start_x = round(x_axis - row_w / 2.0, 6)
            cursor_x = start_x
            for g in row:
                w = g.get("geometry", {}).get("width", 0.0)
                x_map[g["id"]] = cursor_x
                cursor_x = round(cursor_x + w + STD_PITCH, 6)
            return

        # Anchor the special block at x_axis
        anchor = row[anchor_idx]
        anchor_w = anchor.get("geometry", {}).get("width", 0.0)
        anchor_x = round(x_axis - anchor_w / 2.0, 6)
        x_map[anchor["id"]] = anchor_x

        # Place groups to the LEFT of anchor (reverse order)
        cursor_left = anchor_x - STD_PITCH
        for g in reversed(row[:anchor_idx]):
            w = g.get("geometry", {}).get("width", 0.0)
            x_map[g["id"]] = round(cursor_left - w, 6)
            cursor_left = round(cursor_left - w - STD_PITCH, 6)

        # Place groups to the RIGHT of anchor
        cursor_right = round(anchor_x + anchor_w + STD_PITCH, 6)
        for g in row[anchor_idx + 1:]:
            w = g.get("geometry", {}).get("width", 0.0)
            x_map[g["id"]] = cursor_right
            cursor_right = round(cursor_right + w + STD_PITCH, 6)

    for row in all_rows:
        _assign_row_x(row)

    vprint(f"[pre_assign_rows] layout_width={layout_width:.4f} um → x_axis={x_axis:.4f} um")

    # Clone nodes and update geometry.y AND geometry.x
    updated: List[dict] = []
    for n in group_nodes:
        nc = _copy.deepcopy(n)
        if nc["id"] in y_map:
            nc["geometry"]["y"] = y_map[nc["id"]]
        if nc["id"] in x_map:
            nc["geometry"]["x"] = x_map[nc["id"]]
        updated.append(nc)

    # Build human-readable summary for the LLM prompt
    lines: List[str] = []
    for row_idx, row in enumerate(nmos_rows):
        y = nmos_row_ys[row_idx]
        ids    = ", ".join(g["id"] for g in row)
        widths = " + ".join(f"{g['geometry']['width']:.3f}" for g in row)
        xs     = " | ".join(f"{x_map.get(g['id'],0):.3f}" for g in row)
        lines.append(f"   NMOS Row {row_idx}  y={y:.3f} um : {ids}  (widths: {widths} um, x_start: {xs})")

    for row_idx, row in enumerate(pmos_rows):
        y = pmos_row_ys[row_idx]
        ids    = ", ".join(g["id"] for g in row)
        widths = " + ".join(f"{g['geometry']['width']:.3f}" for g in row)
        xs     = " | ".join(f"{x_map.get(g['id'],0):.3f}" for g in row)
        lines.append(f"   PMOS Row {row_idx}  y={y:.3f} um : {ids}  (widths: {widths} um, x_start: {xs})")

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


def expand_to_fingers(
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
        Mapping recorded during ``aggregate_to_logical_devices`` dictating which atomic nodes
        belong to which AI group.
    matching_info : dict, optional
        Precomputed symmetry definitions used to determine finger-level orderings.
        Defaults to None.
    no_abutment : bool, optional
        Force diffusion breaks everywhere by reverting standard FINGER_PITCH to
        STD_PITCH arrays. Defaults to False.
    original_group_nodes : dict, optional
        Mapping of group_id -> original group node dict (from ``aggregate_to_logical_devices``).
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
    #   - Height is computed from nfin: height approx 0.5 + nfin x 0.04 um
    group_ys: Dict[str, float] = {}   # group_id -> snapped Y
    group_types: Dict[str, str] = {}   # group_id -> device type
    group_heights: Dict[str, float] = {}  # group_id -> computed device height in um

    for grp_id, members in finger_map.items():
        grp_geom = placed.get(grp_id, {}).get("geometry", {})
        raw_y = grp_geom.get("y", NMOS_Y)
        dev_type = members[0].get("type", "nmos")
        member_height = max((_node_height_um(m) for m in members), default=ROW_HEIGHT_UM)
        llm_height = _node_height_um({"geometry": grp_geom}, fallback=0.0)

        # Use actual geometry height — do NOT clamp to ROW_HEIGHT_UM.
        # The old max(height, ROW_HEIGHT_UM) inflated 0.568 → 0.668 which made
        # expand_to_fingers think NMOS extended further than pre_assign_rows
        # planned, re-introducing the gap we eliminated.
        computed_height = member_height
        llm_height = max(llm_height, 0.0)
        group_heights[grp_id] = max(computed_height, llm_height) if llm_height > 0 else computed_height

        group_types[grp_id] = dev_type
        # DON'T re-snap — trust the Y from the geometry engine which already
        # used a dynamic row pitch based on actual device heights.
        group_ys[grp_id] = round(float(raw_y), 6)

    # --- Pass 2: enforce strict PMOS/NMOS bounding box separation --------
    # Compute the maximum top edge of any NMOS device: max(nmos_y + nmos_height)
    nmos_top_edges = []
    for grp_id, y in group_ys.items():
        if group_types.get(grp_id) == "nmos":
            h = group_heights.get(grp_id, ROW_HEIGHT_UM)
            # The top edge of the NMOS bounding box is y + height
            nmos_top_edges.append(y + h)

    max_nmos_top = max(nmos_top_edges) if nmos_top_edges else 0.0

    # PMOS bottom edge must be at or above max NMOS top edge plus the configured
    # active-row gap.
    # PMOS bottom edge must be at or above max NMOS top edge (+ ROW_GAP_UM).
    # With ROW_GAP_UM = 0, rows touch without any gap.
    min_pmos_y = max_nmos_top + ROW_GAP_UM

    # Also ensure NMOS and PMOS are on different row grid levels
    # (prevents same-row overlap even with height check)
    nmos_ys_used = {y for grp_id, y in group_ys.items()
                    if group_types.get(grp_id) == "nmos"}
    max_nmos_y = max(nmos_ys_used) if nmos_ys_used else 0.0
    # PMOS must be strictly above the highest NMOS top edge.
    # Instead of adding a full ROW_PITCH from the NMOS *origin* (which
    # creates an artificial gap), we use the bounding-box constraint
    # already computed above (max_nmos_top + ROW_GAP_UM).
    min_pmos_y = max(min_pmos_y, max_nmos_top + ROW_GAP_UM)

    # Find the current lowest PMOS row
    pmos_ys = [y for grp_id, y in group_ys.items() if group_types.get(grp_id) == "pmos"]
    if pmos_ys and nmos_ys_used:
        current_min_pmos = min(pmos_ys)
        # If the lowest PMOS row is below the minimum allowed PMOS Y, shift ALL PMOS rows up uniformly
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
    #   abutted — placed directly adjacent with no gap (FINGER_PITCH = 0.070 um).
    #
    #   For a two-level hierarchy (e.g. m=3, nf=5 = 15 leaves), the members
    #   list is sorted so that multiplier child 1's fingers come first,
    #   followed by multiplier child 2's fingers, etc.  All 15 are placed
    #   consecutively with abutment spacing.
    #
    #   The parent device's bounding box (set during group placement) spans
    #   from the origin X to origin X + total_fingers x pitch.
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

        matrix_data = orig_meta.get("_matrix_data", grp_placed.get("_matrix_data"))
        if matrix_data and is_matched_block and matrix_data.get("matrix"):
            matrix = matrix_data["matrix"]
            rows_count = len(matrix)
            cols_count = len(matrix[0]) if rows_count > 0 else 0
            
            # Group members by transistor base ID
            dev_fingers = {}
            for m in members:
                base, _, _ = _parse_id(m["id"])
                if base not in dev_fingers:
                    dev_fingers[base] = []
                dev_fingers[base].append(m)
                
            MATRIX_ROW_PITCH = _row_step_um(members)
            rep_node = members[0]
            dummy_idx = 0
            
            for r in range(rows_count):
                for c in range(cols_count):
                    base = matrix[r][c]
                    if base == "dummy" or base not in dev_fingers or len(dev_fingers[base]) == 0:
                        node = copy.deepcopy(rep_node)
                        node["id"] = f"DUMMY_matrix_{grp_id}_{dummy_idx}"
                        node["is_dummy"] = True
                        dummy_idx += 1
                        node["net_d"] = "NC"
                        node["net_g"] = "NC"
                        node["net_s"] = "NC"
                    else:
                        node = copy.deepcopy(dev_fingers[base].pop(0))
                        
                    fx = round(origin_x + c * pitch, 6)
                    fy = round(final_y + r * MATRIX_ROW_PITCH, 6)
                    
                    node["geometry"].update({
                        "x":           fx,
                        "y":           fy,
                        "orientation": orient,
                        "width":       pitch,
                    })
                    
                    node["_matched_block"] = True
                    node["_technique"] = orig_meta.get("_technique", grp_placed.get("_technique", ""))
                    node["_block_id"] = grp_id
                    node.pop("_match_owner", None)
                    
                    if no_abutment:
                        node["abutment"] = {"abut_left": False, "abut_right": False}
                    else:
                        node["abutment"] = {
                            "abut_left": c > 0,
                            "abut_right": c < cols_count - 1
                        }
                        
                    expanded.append(node)
            continue

        for finger_idx, orig_node in enumerate(members):
            node = copy.deepcopy(orig_node)
            if node.get("is_dummy"):
                raw_id = str(node.get("id", "DUMMY"))
                if raw_id == "EDGE_DUMMY_L":
                    dummy_label = "EDGE_DUMMY_L"
                elif raw_id == "EDGE_DUMMY_R":
                    dummy_label = "EDGE_DUMMY_R"
                else:
                    dummy_label = "MATCH_DUMMY"
                node["id"] = f"{dummy_label}_{grp_id}_{finger_idx}"
            # Place each sibling at consecutive positions with the group's pitch
            # This ensures abutment for all hierarchy leaves within the group
            fx = round(origin_x + finger_idx * pitch, 6)
            node["geometry"].update({
                "x":           fx,
                "y":           final_y,
                "orientation": orient,
                "width":       pitch,
            })

            if is_matched_block:
                node["_matched_block"] = True
                node["_technique"] = orig_meta.get("_technique", grp_placed.get("_technique", ""))
                node["_block_id"] = grp_id

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
    vprint(f"[expand_to_fingers] Before overlap resolution: {len(expanded)} devices expanded")
    expanded = _resolve_row_overlaps(expanded, no_abutment)
    expanded = legalize_vertical_rows(expanded)
    vprint(f"[expand_to_fingers] After overlap resolution: returning {len(expanded)} devices")

    # POST-EXPANSION VALIDATION: Check for duplicate positions
    pos_check = defaultdict(list)
    for n in expanded:
        x = n.get("geometry", {}).get("x", -1)
        y = n.get("geometry", {}).get("y", -1)
        pos_check[(x, y)].append(n.get("id", "?"))

    duplicates = {pos: ids for pos, ids in pos_check.items() if len(ids) > 1}
    if duplicates:
        vprint(f"[expand_to_fingers] WARNING: {len(duplicates)} position(s) have multiple devices:")
        for pos, ids in list(duplicates.items())[:5]:
            vprint(f"  Position {pos}: {ids}")

    return expanded


# Backward-compatible alias
expand_groups = expand_to_fingers


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

    # Strip only regenerated density fillers.  Structural edge/matrix dummies
    # are part of matched/current-mirror blocks and must survive legalization.
    active_nodes = [n for n in nodes if not _is_regenerated_filler_dummy(n)]
    
    pitch_abut = STD_PITCH if no_abutment else FINGER_PITCH  # 0.294 or 0.070
    pitch_std  = STD_PITCH     # 0.294

    vprint(f"[resolve_overlaps] Starting with {len(active_nodes)} active devices (stripped {len(nodes) - len(active_nodes)} old dummies)")

    # Group by (Y, type) — PMOS and NMOS are always in separate buckets
    type_rows: Dict[Tuple[float, str], List[dict]] = defaultdict(list)
    for n in active_nodes:
        y = round(n.get("geometry", {}).get("y", 0.0), 6)
        dev_type = n.get("type", "nmos")
        type_rows[(y, dev_type)].append(n)

    vprint(f"[resolve_overlaps] Found {len(type_rows)} type-rows")

    for (y_key, _dev_type), row_nodes in type_rows.items():
        # --- Step 1: Identify chains by parent name -----------------------
        chains: Dict[str, List[dict]] = defaultdict(list)
        for node in row_nodes:
            nid = node.get("id", "")
            # FIX: Use _block_id if present to keep interdigitated fingers in one chain
            parent = node.get("_block_id", _transistor_key(nid))
            chains[parent].append(node)

        vprint(f"[resolve_overlaps] Row y={y_key} ({_dev_type}): {len(chains)} chains")

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

        # Compute original chain footprints (first x -> last x + width)
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
                vprint(f"[resolve_overlaps] Shifting chain by +{shift:.4f} to resolve overlap")
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

    # --- Step 5: Global Centering & Inner/Outer Filler Dummies ---
    row_bounds = {}
    for (y_key, dev_type), row_nodes in type_rows.items():
        if not row_nodes: continue
        min_x = min(n.get("geometry", {}).get("x", 0.0) for n in row_nodes)
        max_x = max(n.get("geometry", {}).get("x", 0.0) + n.get("geometry", {}).get("width", pitch_std) for n in row_nodes)
        row_bounds[(y_key, dev_type)] = (min_x, max_x)

    if row_bounds:
        global_min_x = min(b[0] for b in row_bounds.values())
        global_max_x = max(b[1] for b in row_bounds.values())
        global_center = (global_min_x + global_max_x) / 2.0

        new_dummies = []
        dummy_counter = 0

        for (y_key, dev_type), row_nodes in type_rows.items():
            min_x, max_x = row_bounds[(y_key, dev_type)]
            row_center = (min_x + max_x) / 2.0
            
            # Shift the row to align its center with the global center
            shift = round(global_center - row_center, 6)
            if abs(shift) > 0.0001:
                for n in row_nodes:
                    geo = n.get("geometry", {})
                    geo["x"] = round(geo.get("x", 0.0) + shift, 6)
            
            # Collect and sort all device footprints in the shifted row
            footprints = []
            for n in row_nodes:
                x = n.get("geometry", {}).get("x", 0.0)
                w = n.get("geometry", {}).get("width", pitch_std)
                footprints.append((x, x + w))
            
            footprints.sort(key=lambda f: f[0])

            # Merge overlapping or touching footprints to find true gaps
            merged_footprints = []
            if footprints:
                curr_start, curr_end = footprints[0]
                for f_start, f_end in footprints[1:]:
                    if f_start <= curr_end + 0.001:  # Touch or overlap
                        curr_end = max(curr_end, f_end)
                    else:
                        merged_footprints.append((curr_start, curr_end))
                        curr_start, curr_end = f_start, f_end
                merged_footprints.append((curr_start, curr_end))

            # Identify all gaps from global_min_x to global_max_x
            gaps = []
            if not merged_footprints:
                gaps.append((global_min_x, global_max_x))
            else:
                if global_min_x < merged_footprints[0][0] - 0.001:
                    gaps.append((global_min_x, merged_footprints[0][0]))
                
                for i in range(len(merged_footprints) - 1):
                    gap_start = merged_footprints[i][1]
                    gap_end = merged_footprints[i+1][0]
                    if gap_end - gap_start > 0.001:
                        gaps.append((gap_start, gap_end))
                        
                if merged_footprints[-1][1] < global_max_x - 0.001:
                    gaps.append((merged_footprints[-1][1], global_max_x))

            # Get reference dimensions and electrical parameters from an active device in this row
            ref_height = 0.568
            ref_elec = {"nf_per_device": 1, "multiplier": 1, "nfin": 2, "l": 1.4e-8}
            
            for n in row_nodes:
                if not n.get("is_dummy", False):
                    geo = n.get("geometry", {})
                    if "height" in geo:
                        ref_height = geo["height"]
                    
                    elec = n.get("electrical", {})
                    if elec:
                        ref_elec = {
                            "nf_per_device": 1,
                            "multiplier": 1,
                            "nfin": elec.get("nfin", 2),
                            "l": elec.get("l", 1.4e-8)
                        }
                    break

            # Fill each gap with left-aligned dummies (flush placement)
            for g_start, g_end in gaps:
                gap_width = g_end - g_start
                # Relax tolerance: any gap >= 0.290 can hold at least one 0.294 device
                num_fillers = int((gap_width + 0.005) / pitch_std)
                if num_fillers > 0:
                    for i in range(num_fillers):
                        dummy_counter += 1
                        # Left-align: place dummies flush from gap start
                        curr_x = round(g_start + i * pitch_std, 6)
                        # Safety: don't overshoot the gap end
                        if curr_x + pitch_std > g_end + 0.01:
                            break
                        new_dummies.append({
                            "id": f"FILLER_DUMMY_{dummy_counter}_{dev_type}",
                            "type": dev_type,
                            "is_dummy": True,
                            "geometry": {"x": curr_x, "y": float(y_key), "width": pitch_std, "height": ref_height, "orientation": "R0"},
                            "electrical": dict(ref_elec)
                        })

        if new_dummies:
            active_nodes.extend(new_dummies)
            vprint(f"[resolve_overlaps] Centered layout & added {len(new_dummies)} filler dummies for symmetry & density.")

    return active_nodes


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


# ---------------------------------------------------------------------------
# Backward-compatible wrapper for old expand_logical_to_fingers signature
# ---------------------------------------------------------------------------

def expand_logical_to_fingers(logical_nodes: list, original_nodes: list, pitch: float = 0.294) -> list:
    """Expand logical placement back to physical fingers (backward-compatible).

    This wrapper matches the old ai_chat_bot/finger_grouping.py signature.
    """
    original_map = {n["id"]: n for n in original_nodes}
    physical_nodes = []

    for logical_node in logical_nodes:
        if not logical_node.get("_is_logical"):
            physical_nodes.append(logical_node)
            continue

        finger_ids = logical_node.get("_fingers", [])
        base_x = float(logical_node["geometry"]["x"])
        base_y = float(logical_node["geometry"]["y"])
        orientation = logical_node["geometry"].get("orientation", "R0")

        for i, finger_id in enumerate(finger_ids):
            original = original_map.get(finger_id)
            if not original:
                continue
            finger_node = dict(original)
            finger_node["geometry"] = dict(finger_node["geometry"])
            finger_node["geometry"]["x"] = base_x + (i * pitch)
            finger_node["geometry"]["y"] = base_y
            finger_node["geometry"]["orientation"] = orientation
            physical_nodes.append(finger_node)

    return physical_nodes

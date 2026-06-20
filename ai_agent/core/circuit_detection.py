"""
Circuit Detection Core Logic
============================
Pure-Python detection helpers that identify analog structural patterns
(matched pairs, differential pairs, current mirrors, cross-coupled pairs)
directly from a raw node list + terminal_nets.

These are FC-tool ready: every public function returns a LayoutToolResult.
Detection logic is consolidated from:
  - ai_agent.core.topology.extract_symmetry_block
  - ai_agent.placement.finger_grouper.detect_matching_groups
  - ai_agent.placement.finger_grouper._detect_current_mirrors
  - ai_agent.placement.finger_grouper._enrich_matching_info
No new heuristics are introduced here; each function delegates to existing
behaviour when one is available.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Tuple

from ai_agent.core.interfaces import LayoutToolResult, wrap_tool

_POWER_NETS = frozenset({"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS",
                          "VDDA", "VSSA", "CLK", ""})


# ---------------------------------------------------------------------------
# Internal helpers (mirrors topology.py / finger_grouper.py conventions)
# ---------------------------------------------------------------------------

import re

def _parent_id(dev_id: str) -> str:
    """Strip finger/multiplier suffixes like _f1, _m1, .f1, _f1_d1, etc."""
    return re.sub(r'(_[mf]\d+|\.f\d+).*', '', dev_id)


def _nets_for(dev_id: str, terminal_nets: dict) -> dict:
    if not isinstance(terminal_nets, dict):
        return {}
    for key in (dev_id, dev_id.upper(), dev_id.lower()):
        v = terminal_nets.get(key)
        if isinstance(v, dict):
            return v
    # Fallback: strip finger suffix and try to look up parent ID
    pid = _parent_id(dev_id)
    if pid != dev_id:
        for key in (pid, pid.upper(), pid.lower()):
            v = terminal_nets.get(key)
            if isinstance(v, dict):
                return v
    return {}


def _net(dev_id: str, pin: str, terminal_nets: dict) -> str:
    return str(_nets_for(dev_id, terminal_nets).get(pin, "")).upper().strip()


def _signature(node: dict) -> tuple:
    """Electrical signature used by detect_matched_pairs.

    A node is structurally matched to another iff they share the same
    (type, width, height, is_dummy, orientation_class) — a coarser version
    of finger_grouper._electrical_signature that does not depend on
    compaction state.
    """
    geo = node.get("geometry", {}) or {}
    return (
        str(node.get("type", "")).lower(),
        round(float(geo.get("width",  0.0)),  6),
        round(float(geo.get("height", 0.0)),  6),
        bool(node.get("is_dummy", False)),
    )


def _real_nodes(nodes: list) -> list:
    return [n for n in (nodes or []) if not n.get("is_dummy")]


# ---------------------------------------------------------------------------
# Detection: matched pairs (same electrical signature)
# ---------------------------------------------------------------------------

@wrap_tool
def detect_matched_pairs(nodes: list) -> LayoutToolResult:
    """Group devices that share an identical (type, W, H) signature.

    Returns metrics:
        matched_pairs    : list[(id_a, id_b)]
        matched_clusters : list[list[id]] - full equivalence classes
    """
    sig_buckets: Dict[tuple, List[str]] = defaultdict(list)
    for n in _real_nodes(nodes):
        sig_buckets[_signature(n)].append(str(n.get("id", "")))

    matched_pairs: List[Tuple[str, str]] = []
    matched_clusters: List[List[str]] = []
    for members in sig_buckets.values():
        if len(members) < 2:
            continue
        members_sorted = sorted(members)
        matched_clusters.append(members_sorted)
        for i, a in enumerate(members_sorted):
            for b in members_sorted[i + 1:]:
                matched_pairs.append((a, b))

    return LayoutToolResult(
        success=True,
        message=f"Detected {len(matched_pairs)} matched pair(s) across "
                f"{len(matched_clusters)} cluster(s)",
        changed=False,
        nodes=list(nodes),
        metrics={
            "matched_pairs":    [list(p) for p in matched_pairs],
            "matched_clusters": matched_clusters,
        },
    )


# ---------------------------------------------------------------------------
# Detection: differential pairs (shared source, non-power, same type)
# ---------------------------------------------------------------------------

@wrap_tool
def detect_differential_pairs(nodes: list, terminal_nets: dict) -> LayoutToolResult:
    """Detect diff pairs by shared source net.

    Two same-type devices whose source net is identical and not a power net
    form a differential pair (matches extract_symmetry_block rule #1).
    """
    by_type: Dict[str, List[str]] = defaultdict(list)
    for n in _real_nodes(nodes):
        by_type[str(n.get("type", "")).lower()].append(str(n.get("id", "")))

    diff_pairs: List[Tuple[str, str]] = []
    shared_sources: List[str] = []
    for devs in by_type.values():
        src_map: Dict[str, List[str]] = defaultdict(list)
        for did in devs:
            snet = _net(did, "S", terminal_nets)
            if snet and snet not in _POWER_NETS:
                src_map[snet].append(did)
        for snet, members in src_map.items():
            parent_to_devs = defaultdict(list)
            for m in members:
                parent_to_devs[_parent_id(m)].append(m)
            parents = list(parent_to_devs.keys())
            if len(parents) >= 2:
                p0, p1 = parents[0], parents[1]
                diff_pairs.append((parent_to_devs[p0][0], parent_to_devs[p1][0]))
                shared_sources.append(snet)

    return LayoutToolResult(
        success=True,
        message=f"Detected {len(diff_pairs)} differential pair(s)",
        changed=False,
        nodes=list(nodes),
        metrics={
            "diff_pairs":      [list(p) for p in diff_pairs],
            "shared_sources":  shared_sources,
        },
    )


# ---------------------------------------------------------------------------
# Detection: current mirrors (shared gate, diode-connected, same type)
# ---------------------------------------------------------------------------

@wrap_tool
def detect_current_mirrors(nodes: list, terminal_nets: dict) -> LayoutToolResult:
    """Detect current mirror clusters.

    A cluster is identified when:
      - >= 2 same-type devices share the same Gate net
      - That gate net is also the Drain of at least one (diode-connected)
      - The gate net is not a power/CLK net
    """
    real = _real_nodes(nodes)
    type_lookup = {str(n.get("id", "")): str(n.get("type", "")).lower()
                   for n in real}

    gate_groups: Dict[str, List[str]] = defaultdict(list)
    for did in type_lookup:
        gnet = _net(did, "G", terminal_nets)
        if gnet and gnet not in _POWER_NETS:
            gate_groups[gnet].append(did)

    mirror_clusters: List[List[str]] = []
    for gnet, members in gate_groups.items():
        if len(members) < 2:
            continue
        types = {type_lookup.get(m, "") for m in members}
        if len(types) != 1:
            continue
        has_diode = any(_net(m, "D", terminal_nets) == gnet for m in members)
        if has_diode:
            distinct_parents = {_parent_id(m) for m in members}
            if len(distinct_parents) >= 2:
                mirror_clusters.append(sorted(members))

    return LayoutToolResult(
        success=True,
        message=f"Detected {len(mirror_clusters)} current mirror cluster(s)",
        changed=False,
        nodes=list(nodes),
        metrics={"current_mirrors": mirror_clusters},
    )


# ---------------------------------------------------------------------------
# Detection: cross-coupled pairs (D_a == G_b AND D_b == G_a, same type)
# ---------------------------------------------------------------------------

@wrap_tool
def detect_cross_coupled_pairs(nodes: list, terminal_nets: dict) -> LayoutToolResult:
    """Detect cross-coupled latch pairs.

    Mirrors finger_grouper._enrich_matching_info cross_pairs logic.
    """
    real = _real_nodes(nodes)
    type_lookup = {str(n.get("id", "")): str(n.get("type", "")).lower()
                   for n in real}
    ids = list(type_lookup.keys())

    cross: List[Tuple[str, str]] = []
    for i, a in enumerate(ids):
        ta = _nets_for(a, terminal_nets)
        if not ta:
            continue
        for b in ids[i + 1:]:
            if type_lookup[a] != type_lookup[b]:
                continue
            if _parent_id(a) == _parent_id(b):
                continue
            tb = _nets_for(b, terminal_nets)
            if not tb:
                continue
            d_a = str(ta.get("D", "")).upper()
            g_a = str(ta.get("G", "")).upper()
            d_b = str(tb.get("D", "")).upper()
            g_b = str(tb.get("G", "")).upper()
            if d_a and g_a and d_b and g_b and d_a == g_b and d_b == g_a:
                cross.append((a, b))

    return LayoutToolResult(
        success=True,
        message=f"Detected {len(cross)} cross-coupled pair(s)",
        changed=False,
        nodes=list(nodes),
        metrics={"cross_coupled_pairs": [list(p) for p in cross]},
    )


# ---------------------------------------------------------------------------
# Detection: circuit type (diff amp / latch / current mirror / mixed)
# ---------------------------------------------------------------------------

@wrap_tool
def detect_circuit_type(nodes: list, terminal_nets: dict) -> LayoutToolResult:
    """Best-effort circuit classification using detection results.

    Decision rules (priority order):
      cross_coupled present + diff_pair present  → "comparator"
      cross_coupled present                      → "latch"
      diff_pair + current_mirror                 → "differential_amplifier"
      diff_pair only                             → "differential_pair"
      current_mirror only                        → "current_mirror_array"
      matched_pairs only                         → "matched_array"
      otherwise                                  → "generic"
    """
    diff   = detect_differential_pairs(nodes, terminal_nets).metrics.get("diff_pairs", [])
    mirror = detect_current_mirrors(nodes, terminal_nets).metrics.get("current_mirrors", [])
    cross  = detect_cross_coupled_pairs(nodes, terminal_nets).metrics.get("cross_coupled_pairs", [])
    matched = detect_matched_pairs(nodes).metrics.get("matched_pairs", [])

    if cross and diff:
        circuit_type = "comparator"
    elif cross:
        circuit_type = "latch"
    elif diff and mirror:
        circuit_type = "differential_amplifier"
    elif diff:
        circuit_type = "differential_pair"
    elif mirror:
        circuit_type = "current_mirror_array"
    elif matched:
        circuit_type = "matched_array"
    else:
        circuit_type = "generic"

    return LayoutToolResult(
        success=True,
        message=f"Circuit type: {circuit_type}",
        changed=False,
        nodes=list(nodes),
        metrics={
            "circuit_type":         circuit_type,
            "diff_pairs":           diff,
            "current_mirrors":      mirror,
            "cross_coupled_pairs":  cross,
            "matched_pairs":        matched,
        },
    )


# ---------------------------------------------------------------------------
# Validation: symmetry score
# ---------------------------------------------------------------------------

@wrap_tool
def validate_symmetry(nodes: list) -> LayoutToolResult:
    """Score the current placement against symmetry/matching benchmarks.

    Delegates to placement.quality_metrics.score_placement and surfaces
    a pass/fail interpretation. The threshold (>= 90% on matching axes)
    matches the score_placement default summary.
    """
    from ai_agent.placement.quality_metrics import score_placement

    report = score_placement(nodes)
    matching_score = float(report.get("matching_score_percent", 0.0))
    symmetry_score = float(report.get("y_symmetry_percent", 0.0))
    threshold      = 90.0
    passed         = matching_score >= threshold and symmetry_score >= threshold

    return LayoutToolResult(
        success=True,
        message=(
            f"Symmetry validation: {'PASS' if passed else 'FAIL'} "
            f"(matching={matching_score:.1f}%, y_sym={symmetry_score:.1f}%)"
        ),
        changed=False,
        nodes=list(nodes),
        metrics={
            "passed":            passed,
            "matching_score":    matching_score,
            "y_symmetry_score":  symmetry_score,
            "threshold":         threshold,
            "report":            report,
        },
    )


# ---------------------------------------------------------------------------
# Validation: dummy presence around a matched group
# ---------------------------------------------------------------------------

@wrap_tool
def validate_dummy_presence(
    nodes: list,
    group_node_ids: list,
    min_dummies_per_side: int = 1,
) -> LayoutToolResult:
    """Verify that structural dummies sit on both sides of a matched group.

    Looks for dummies within 0.5 µm of either end of the bounding box of
    the supplied group_node_ids on the SAME row(s). Structural dummies
    (STRUCT_DUMMY_*) and edge filler (EDGE_DUMMY*) both count.
    """
    if not group_node_ids:
        return LayoutToolResult(
            success=False,
            message="validate_dummy_presence: group_node_ids must not be empty",
            nodes=list(nodes),
        )

    id_set = set(group_node_ids)
    group  = [n for n in nodes if str(n.get("id", "")) in id_set]
    if not group:
        return LayoutToolResult(
            success=False,
            message="validate_dummy_presence: none of the requested IDs were found",
            nodes=list(nodes),
        )

    rows: Dict[float, List[dict]] = defaultdict(list)
    for n in group:
        y = round(float(n.get("geometry", {}).get("y", 0.0)), 3)
        rows[y].append(n)

    proximity_um = 0.5
    per_row_status: List[dict] = []
    overall_pass  = True
    for y, row_nodes in rows.items():
        xs    = [float(n["geometry"]["x"]) for n in row_nodes]
        ends  = [float(n["geometry"]["x"]) + float(n["geometry"].get("width", 0.0))
                 for n in row_nodes]
        left, right = min(xs), max(ends)

        left_dummies  = 0
        right_dummies = 0
        for n in nodes:
            nid = str(n.get("id", ""))
            if not (n.get("is_dummy") or
                    nid.startswith(("STRUCT_DUMMY_", "EDGE_DUMMY", "FILLER_DUMMY_", "DUMMY_", "MATCH_DUMMY_"))
                    or (len(nid) >= 2 and nid[0] == "D" and nid[1:].isdigit())):
                continue
            geo = n.get("geometry", {}) or {}
            ny  = round(float(geo.get("y", 0.0)), 3)
            if ny != y:
                continue
            nx_end = float(geo.get("x", 0.0)) + float(geo.get("width", 0.0))
            nx     = float(geo.get("x", 0.0))
            if abs(nx_end - left) <= proximity_um and nx_end <= left + 1e-6:
                left_dummies += 1
            elif abs(nx - right) <= proximity_um and nx >= right - 1e-6:
                right_dummies += 1

        row_pass = (left_dummies  >= min_dummies_per_side and
                    right_dummies >= min_dummies_per_side)
        overall_pass &= row_pass
        per_row_status.append({
            "y":            y,
            "left_count":   left_dummies,
            "right_count":  right_dummies,
            "passed":       row_pass,
        })

    return LayoutToolResult(
        success=True,
        message=("Dummy presence: PASS" if overall_pass else "Dummy presence: FAIL"),
        changed=False,
        nodes=list(nodes),
        metrics={
            "passed":              overall_pass,
            "min_per_side":        min_dummies_per_side,
            "rows":                per_row_status,
        },
    )

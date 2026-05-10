"""
Circuit Orchestrator
====================
High-level FC tools that compose existing detection + placement primitives
into named circuit workflows. No new layout algorithms are introduced —
each function calls already-tested core/ functions in a deterministic order.

Tools defined here:
    place_comparator                — diff_pair + cross_couple + ABBA loads
    place_tx_driver                 — current mirror chain placement
    run_full_layout_pipeline        — physical cells + matching + DRC + score
    optimize_layout_for_matching    — common-centroid every detected group
    optimize_layout_for_routing     — legalize + structural dummy band
"""
from __future__ import annotations

import copy
from collections import defaultdict
from typing import List

from ai_agent.core.interfaces       import LayoutToolResult, wrap_tool
from ai_agent.core.circuit_detection import (
    detect_circuit_type,
    detect_differential_pairs,
    detect_current_mirrors,
    detect_cross_coupled_pairs,
    detect_matched_pairs,
    validate_symmetry,
)
from ai_agent.core.group_placer import (
    place_matched_pair,
    place_differential_pair,
    place_current_mirror,
    add_dummy_group,
)
from ai_agent.core.physical_cells   import insert_all_physical_cells
from ai_agent.core.common_centroid  import insert_dummies_around_group
from ai_agent.placement.quality_metrics import _transistor_key


# ---------------------------------------------------------------------------
# Internal helper: chain results, threading nodes through each step
# ---------------------------------------------------------------------------

def _chain(initial_nodes: list, steps: list) -> LayoutToolResult:
    """Run a list of (label, callable(nodes) -> LayoutToolResult) steps in order.

    Aggregates messages and warnings; returns the final node list.
    Failure of any step short-circuits and reports.
    """
    cur     = list(initial_nodes)
    msgs:   List[str] = []
    warns:  List[str] = []
    metrics_acc: dict = {}
    changed_any = False

    for label, fn in steps:
        result: LayoutToolResult = fn(cur)
        msgs.append(f"  {'✓' if result.success else '✗'} {label}: {result.message}")
        warns.extend(result.warnings or [])
        if result.metrics:
            metrics_acc[label] = result.metrics
        if not result.success:
            return LayoutToolResult(
                success  = False,
                message  = f"Pipeline halted at {label!r}\n" + "\n".join(msgs),
                changed  = changed_any,
                nodes    = cur,
                metrics  = metrics_acc,
                warnings = warns,
            )
        if result.changed:
            changed_any = True
        if result.nodes:
            cur = result.nodes

    return LayoutToolResult(
        success  = True,
        message  = "Pipeline complete\n" + "\n".join(msgs),
        changed  = changed_any,
        nodes    = cur,
        metrics  = metrics_acc,
        warnings = warns,
    )


def _bbox_geometry(nodes: list) -> dict:
    xs = []
    ys = []
    xe = []
    ye = []
    for n in nodes:
        geo = n.get("geometry", {}) or {}
        x = float(geo.get("x", 0.0))
        y = float(geo.get("y", 0.0))
        w = float(geo.get("width", 0.0))
        h = float(geo.get("height", 0.0))
        xs.append(x)
        ys.append(y)
        xe.append(x + w)
        ye.append(y + h)
    if not xs:
        return {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0}
    first_geo = nodes[0].get("geometry", {}) or {}
    return {
        **first_geo,
        "x": min(xs),
        "y": min(ys),
        "width": max(xe) - min(xs),
        "height": max(ye) - min(ys),
    }


def _logical_layout_context(nodes: list, terminal_nets: dict) -> tuple[list, dict]:
    """Collapse expanded finger nodes to parent devices for topology tools."""
    buckets: dict[str, list] = defaultdict(list)
    for n in nodes or []:
        if n.get("is_dummy"):
            continue
        parent_id = _transistor_key(str(n.get("id", "")))
        if parent_id:
            buckets[parent_id].append(n)

    logical_nodes: list = []
    for parent_id, members in sorted(buckets.items()):
        rep = copy.deepcopy(members[0])
        rep["id"] = parent_id
        rep["geometry"] = _bbox_geometry(members)
        rep["finger_count"] = len(members)
        logical_nodes.append(rep)

    source_nets = terminal_nets if isinstance(terminal_nets, dict) else {}
    logical_nets: dict = {}
    for parent_id, members in buckets.items():
        if isinstance(source_nets.get(parent_id), dict):
            logical_nets[parent_id] = dict(source_nets[parent_id])
            continue
        child_ids = {str(m.get("id", "")) for m in members}
        by_pin: dict[str, str] = {}
        for key, nets in source_nets.items():
            if key not in child_ids or not isinstance(nets, dict):
                continue
            for pin in ("D", "G", "S"):
                value = str(nets.get(pin, "")).strip()
                if value and pin not in by_pin:
                    by_pin[pin] = value
        if by_pin:
            logical_nets[parent_id] = by_pin

    return logical_nodes, logical_nets


# ---------------------------------------------------------------------------
# place_comparator
# ---------------------------------------------------------------------------

@wrap_tool
def place_comparator(nodes: list, terminal_nets: dict, pdk: dict) -> LayoutToolResult:
    """Place a comparator: differential input pair + cross-coupled latch.

    Pipeline:
      1. Detect differential pair → place_differential_pair
      2. Detect cross-coupled pair → place_matched_pair (ABBA)
      3. Detect load mirror (PMOS pair sharing a gate) → place_matched_pair
    """
    diff   = detect_differential_pairs(nodes, terminal_nets).metrics.get("diff_pairs", []) or []
    cross  = detect_cross_coupled_pairs(nodes, terminal_nets).metrics.get("cross_coupled_pairs", []) or []
    mirror = detect_current_mirrors(nodes, terminal_nets).metrics.get("current_mirrors", []) or []

    if not (diff or cross or mirror):
        return LayoutToolResult(
            success=False,
            message="place_comparator: no diff/cross/mirror structures detected",
            nodes=list(nodes),
        )

    steps: list = []
    if diff:
        a, b = diff[0]
        steps.append((
            f"place differential pair {a},{b}",
            lambda ns, a=a, b=b: place_differential_pair(ns, a, b, pdk),
        ))
    if cross:
        a, b = cross[0]
        steps.append((
            f"place cross-coupled latch {a},{b}",
            lambda ns, a=a, b=b: place_matched_pair(ns, a, b, pdk),
        ))
    if mirror:
        cluster = mirror[0]
        if len(cluster) == 2:
            a, b = cluster
            steps.append((
                f"place load mirror {a},{b}",
                lambda ns, a=a, b=b: place_matched_pair(ns, a, b, pdk),
            ))
        elif len(cluster) > 2:
            steps.append((
                f"place load mirror cluster {cluster}",
                lambda ns, c=cluster: place_current_mirror(ns, c, pdk),
            ))

    return _chain(nodes, steps)


# ---------------------------------------------------------------------------
# place_tx_driver
# ---------------------------------------------------------------------------

@wrap_tool
def place_tx_driver(nodes: list, terminal_nets: dict, pdk: dict) -> LayoutToolResult:
    """Place a TX driver layout: every detected current-mirror cluster
    gets common-centroid placement."""
    mirrors = detect_current_mirrors(nodes, terminal_nets).metrics.get("current_mirrors", []) or []
    if not mirrors:
        return LayoutToolResult(
            success=False,
            message="place_tx_driver: no current mirror cluster detected",
            nodes=list(nodes),
        )

    steps = [
        (f"place current mirror {cluster}",
         lambda ns, c=cluster: place_current_mirror(ns, c, pdk))
        for cluster in mirrors
    ]
    return _chain(nodes, steps)


# ---------------------------------------------------------------------------
# run_full_layout_pipeline
# ---------------------------------------------------------------------------

@wrap_tool
def run_full_layout_pipeline(
    nodes: list,
    terminal_nets: dict,
    pdk: dict,
) -> LayoutToolResult:
    """End-to-end pipeline: type-detect → matching placement → physical cells.

    Steps:
      1. detect_circuit_type
      2. optimize_layout_for_matching (places every detected pair/mirror)
      3. insert_all_physical_cells (endcaps + taps + fillers)
      4. validate_symmetry (final score)
    """
    classification = detect_circuit_type(nodes, terminal_nets)

    steps = [
        ("optimize for matching",
         lambda ns: optimize_layout_for_matching(ns, terminal_nets, pdk)),
        ("insert physical cells",
         lambda ns: insert_all_physical_cells(ns, pdk)),
        ("validate symmetry",
         lambda ns: validate_symmetry(ns)),
    ]
    chained = _chain(nodes, steps)

    if chained.success:
        chained.metrics["circuit_type"] = classification.metrics.get("circuit_type", "generic")
        chained.message = (
            f"Full layout pipeline ({classification.metrics.get('circuit_type','?')}):\n"
            + chained.message
        )
    return chained


# ---------------------------------------------------------------------------
# optimize_layout_for_matching
# ---------------------------------------------------------------------------

@wrap_tool
def optimize_layout_for_matching(
    nodes: list,
    terminal_nets: dict,
    pdk: dict,
) -> LayoutToolResult:
    """Apply common-centroid placement to every detected matched structure.

    Order of placement (priority):
      1. diff pairs        (ABAB)
      2. cross-coupled     (ABBA)
      3. current mirrors   (ABBA / 2D-CC for clusters > 2)
      4. plain matched pairs (ABBA)
    """
    logical_nodes, logical_terminal_nets = _logical_layout_context(nodes, terminal_nets)
    detection_nodes = logical_nodes or list(nodes)
    detection_nets = logical_terminal_nets or terminal_nets

    diff    = detect_differential_pairs(detection_nodes, detection_nets).metrics.get("diff_pairs", []) or []
    cross   = detect_cross_coupled_pairs(detection_nodes, detection_nets).metrics.get("cross_coupled_pairs", []) or []
    mirrors = detect_current_mirrors(detection_nodes, detection_nets).metrics.get("current_mirrors", []) or []
    matched = detect_matched_pairs(detection_nodes).metrics.get("matched_pairs", []) or []

    used: set = set()
    steps: list = []

    for a, b in diff:
        if a in used or b in used:
            continue
        used.update([a, b])
        steps.append((f"diff_pair {a},{b}",
                      lambda ns, a=a, b=b: place_differential_pair(ns, a, b, pdk)))
    for a, b in cross:
        if a in used or b in used:
            continue
        used.update([a, b])
        steps.append((f"cross_coupled {a},{b}",
                      lambda ns, a=a, b=b: place_matched_pair(ns, a, b, pdk)))
    for cluster in mirrors:
        if any(m in used for m in cluster):
            continue
        used.update(cluster)
        if len(cluster) == 2:
            a, b = cluster
            steps.append((f"current_mirror {a},{b}",
                          lambda ns, a=a, b=b: place_matched_pair(ns, a, b, pdk)))
        else:
            steps.append((f"current_mirror_cluster {cluster}",
                          lambda ns, c=cluster: place_current_mirror(ns, c, pdk)))
    for a, b in matched:
        if a in used or b in used:
            continue
        used.update([a, b])
        steps.append((f"matched_pair {a},{b}",
                      lambda ns, a=a, b=b: place_matched_pair(ns, a, b, pdk)))

    if not steps:
        return LayoutToolResult(
            success=True,
            message="optimize_layout_for_matching: no matched structures detected",
            changed=False,
            nodes=list(nodes),
        )
    return _chain(nodes, steps)


# ---------------------------------------------------------------------------
# optimize_layout_for_routing
# ---------------------------------------------------------------------------

@wrap_tool
def optimize_layout_for_routing(
    nodes: list,
    pdk: dict,
    gap_px: float = 0.0,
) -> LayoutToolResult:
    """Routing-friendly cleanup: legalize DRC, then insert structural dummies
    around every matched cluster to give nets more vertical breathing room."""
    from ai_agent.core import drc as _drc
    from ai_agent.tools.cmd_parser import apply_cmds_to_nodes

    cur     = list(nodes)
    msgs:   List[str] = []
    metrics: dict = {}

    # 1. Legalize
    drc = _drc.run_drc_check(cur, gap_px=gap_px)
    if not drc["pass"]:
        fixes = _drc.compute_prescriptive_fixes(drc, gap_px=gap_px, nodes=cur)
        cur   = apply_cmds_to_nodes(cur, fixes)
        msgs.append(f"  ✓ legalize: applied {len(fixes)} prescriptive fix(es)")
        metrics["legalize_fixes"] = len(fixes)
    else:
        msgs.append("  ✓ legalize: DRC already clean")
        metrics["legalize_fixes"] = 0

    # 2. Wrap each matched cluster in structural dummies (one per side)
    matched_clusters = detect_matched_pairs(cur).metrics.get("matched_clusters", [])
    dummy_inserted   = 0
    for cluster in matched_clusters:
        group_nodes = [n for n in cur if str(n.get("id", "")) in set(cluster)]
        if not group_nodes:
            continue
        result = insert_dummies_around_group(group_nodes, pdk, n_dummies=1)
        if result.success and result.changed:
            non_group = [n for n in cur if str(n.get("id", "")) not in set(cluster)]
            cur = non_group + result.nodes
            dummy_inserted += len(result.nodes) - len(group_nodes)

    msgs.append(f"  ✓ structural dummies: inserted {dummy_inserted}")
    metrics["dummies_inserted"] = dummy_inserted

    return LayoutToolResult(
        success  = True,
        message  = "Routing-friendly optimization\n" + "\n".join(msgs),
        changed  = True,
        nodes    = cur,
        metrics  = metrics,
    )

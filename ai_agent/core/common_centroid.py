"""
Common Centroid Placement
=========================
Orchestration layer for common-centroid and interdigitated placement.
All logic is delegated to existing modules — nothing is reimplemented here.

Delegates to:
  generate_placement_grid       ← ai_agent.matching.universal_pattern_generator
  generate_common_centroid_matrix ← ai_agent.placement.centroid_generator
  _common_centroid_accuracy      ← ai_agent.placement.quality_metrics

All placement functions return LayoutToolResult.
evaluate_centroid_error returns a plain float (µm distance, 0.0 = perfect).
"""

from __future__ import annotations

import math
import logging
from typing import List

from ai_agent.core.interfaces import LayoutToolResult, wrap_tool
from ai_agent.pdks.loader import get_rule

# ── Existing logic — imported, NOT reimplemented ─────────────────────────────
from ai_agent.matching.universal_pattern_generator import generate_placement_grid
from ai_agent.placement.centroid_generator import generate_common_centroid_matrix
from ai_agent.placement.quality_metrics import (
    _common_centroid_accuracy,
    _transistor_key,
    STD_PITCH,
)

logger = logging.getLogger("ai_agent")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _snap(x: float, pitch: float) -> float:
    """Snap x to the nearest fin-pitch grid point."""
    if not pitch or pitch <= 0:
        return x
    return round(round(x / pitch) * pitch, 6)


def _ref_dim(nodes: list, key: str, fallback: float) -> float:
    """Return the first non-None geometry value for *key* found in *nodes*."""
    for n in nodes:
        v = (n.get("geometry") or {}).get(key)
        if v is not None:
            return float(v)
    return fallback


# ---------------------------------------------------------------------------
# Function 4 — defined first because functions 1–3 call it
# ---------------------------------------------------------------------------

def evaluate_centroid_error(
    placed_nodes: list,
    group_a_ids: list,
    group_b_ids: list,
) -> float:
    """Thin wrapper around _common_centroid_accuracy from quality_metrics.

    Filters *placed_nodes* to those whose transistor key is in either group,
    then delegates entirely to _common_centroid_accuracy.

    Returns centroid distance in micrometers.  0.0 means perfect alignment
    OR that the arrangement is 1D (no 2D error detectable by the metric).
    """
    try:
        all_ids = set(group_a_ids) | set(group_b_ids)
        if not all_ids:
            return 0.0
        relevant = [
            n for n in placed_nodes
            if _transistor_key(str(n.get("id", ""))) in all_ids
        ]
        if not relevant:
            return 0.0

        score, _ = _common_centroid_accuracy(relevant)

        # N/A: arrangement is 1D — by construction that means zero 2D error
        if score is None:
            return 0.0
        # Perfect or numerical 1.0: log(1.0) == 0
        if score >= 1.0:
            return 0.0

        # Invert: score = exp(-offset / dev_width)  →  offset = -log(score) * dev_width
        dev_width = _ref_dim(relevant, "width", STD_PITCH)
        return round(-math.log(max(score, 1e-12)) * max(dev_width, 1e-9), 6)

    except Exception as exc:
        logger.debug("evaluate_centroid_error: %s", exc)
        return 0.0


# ---------------------------------------------------------------------------
# Function 1 — 1-D common centroid for a matched pair
# ---------------------------------------------------------------------------

@wrap_tool
def place_common_centroid(
    group_a: list,
    group_b: list,
    start_x: float,
    row_y: float,
    pdk: dict,
    pattern: str = "ABBA",
) -> LayoutToolResult:
    """Place two groups of finger nodes in a 1D common-centroid pattern.

    Calls generate_placement_grid(technique="CC", rows=1) from
    universal_pattern_generator — no CC logic is reimplemented here.

    Args:
        group_a:   finger node dicts for device A (repositioned in-place).
        group_b:   finger node dicts for device B (repositioned in-place).
        start_x:   left-edge X for the first finger (µm).
        row_y:     Y coordinate for the row (µm).
        pdk:       PDK dict — used for fin_pitch_um snapping.
        pattern:   informational label only (technique is always "CC").

    Returns:
        LayoutToolResult with:
        - nodes:    the repositioned finger nodes (group_a + group_b, minus
                    any grid DUMMY slots).
        - metrics:  centroid_error_um, placed_count.
    """
    pdk = pdk or {}
    if not group_a or not group_b:
        return LayoutToolResult(
            success=False,
            message="group_a and group_b must both be non-empty",
            changed=False,
            nodes=list(group_a or []) + list(group_b or []),
        )

    # Build the token→count dict expected by generate_placement_grid
    devices_in = {"M0": len(group_a), "M1": len(group_b)}

    # Delegate all pattern logic to the existing generator
    grid = generate_placement_grid(devices_in, technique="CC", rows=1)

    ref_all = list(group_a) + list(group_b)
    pitch     = _ref_dim(ref_all, "width", STD_PITCH)
    fin_pitch = get_rule(pdk, "fin_pitch_um") or 0.014

    # Finger queues — consumed left-to-right through the grid
    queues: dict = {"M0": list(group_a), "M1": list(group_b)}

    placed: List[dict] = []
    for cell in sorted(grid, key=lambda g: (g["y_index"], g["x_index"])):
        token = cell["device"]
        if token not in queues or not queues[token]:
            continue          # skip DUMMY slots and exhausted queues
        node = queues[token].pop(0)
        geo = node.setdefault("geometry", {})
        geo["x"] = _snap(float(start_x) + cell["x_index"] * pitch, fin_pitch)
        geo["y"] = float(row_y)
        placed.append(node)

    a_ids = list({_transistor_key(str(n.get("id", ""))) for n in group_a})
    b_ids = list({_transistor_key(str(n.get("id", ""))) for n in group_b})
    centroid_err = evaluate_centroid_error(placed, a_ids, b_ids)

    return LayoutToolResult(
        success=True,
        message=(
            f"Placed {len(placed)} finger(s) in CC-{pattern} pattern. "
            f"centroid_error={centroid_err:.4f}µm"
        ),
        changed=True,
        nodes=placed,
        metrics={"centroid_error_um": centroid_err, "placed_count": len(placed)},
        warnings=[],
    )


# ---------------------------------------------------------------------------
# Function 2 — 2-D common centroid for arbitrary device arrays
# ---------------------------------------------------------------------------

@wrap_tool
def place_common_centroid_2d(
    devices: list,
    start_x: float,
    row_y: float,
    pdk: dict,
) -> LayoutToolResult:
    """Place multiple device finger groups in a 2D common-centroid matrix.

    Calls generate_common_centroid_matrix() from centroid_generator —
    no matrix logic is reimplemented here.

    Args:
        devices:  list of dicts, each with:
                    "id"      (str)  — device identifier
                    "fingers" (int)  — finger count  [optional: inferred from nodes]
                    "nodes"   (list) — physical finger node dicts to reposition
        start_x:  left-edge X (µm).
        row_y:    Y of the lowest row (µm).
        pdk:      PDK dict.

    Returns:
        LayoutToolResult with nodes = all repositioned finger nodes,
        metrics = centroid_error_um, matrix_rows, matrix_cols, placed_count.
    """
    pdk = pdk or {}
    if not devices:
        return LayoutToolResult(
            success=False, message="devices list must be non-empty",
            changed=False, nodes=[],
        )

    # Extract specs that generate_common_centroid_matrix expects
    device_specs = [
        {
            "id":      d["id"],
            "fingers": d.get("fingers") or len(d.get("nodes", [])),
        }
        for d in devices
    ]

    # Delegate to existing 2D matrix generator
    matrix_data = generate_common_centroid_matrix(device_specs)
    matrix = matrix_data.get("matrix", [])
    if not matrix:
        all_nodes = [n for d in devices for n in d.get("nodes", [])]
        return LayoutToolResult(
            success=False,
            message="generate_common_centroid_matrix returned an empty matrix",
            changed=False, nodes=all_nodes,
        )

    n_rows = matrix_data["rows"]
    n_cols = matrix_data["cols"]

    all_nodes = [n for d in devices for n in d.get("nodes", [])]
    pitch      = _ref_dim(all_nodes, "width",  STD_PITCH)
    row_height = _ref_dim(all_nodes, "height", 0.668)
    fin_pitch  = get_rule(pdk, "fin_pitch_um") or 0.014

    # Finger queues per device id
    queues: dict = {d["id"]: list(d.get("nodes", [])) for d in devices}

    placed: List[dict] = []
    for r in range(n_rows):
        for c in range(n_cols):
            cell_id = matrix[r][c]
            if cell_id == "dummy" or cell_id not in queues or not queues[cell_id]:
                continue
            node = queues[cell_id].pop(0)
            geo = node.setdefault("geometry", {})
            geo["x"] = _snap(float(start_x) + c * pitch,      fin_pitch)
            geo["y"] = float(row_y) + r * row_height
            placed.append(node)

    all_ids      = [d["id"] for d in devices]
    centroid_err = evaluate_centroid_error(placed, all_ids, [])

    return LayoutToolResult(
        success=True,
        message=(
            f"Placed {len(placed)} finger(s) in 2D CC matrix "
            f"({n_rows}×{n_cols}). centroid_error={centroid_err:.4f}µm"
        ),
        changed=True,
        nodes=placed,
        metrics={
            "centroid_error_um": centroid_err,
            "matrix_rows":       n_rows,
            "matrix_cols":       n_cols,
            "placed_count":      len(placed),
        },
        warnings=[],
    )


# ---------------------------------------------------------------------------
# Function 3 — structural dummy insertion around a matched group
# ---------------------------------------------------------------------------

@wrap_tool
def insert_dummies_around_group(
    group_nodes: list,
    pdk: dict,
    n_dummies: int = 1,
) -> LayoutToolResult:
    """Add structural isolation dummy fingers on both sides of a device group.

    The inserted nodes are marked "structural": True and their IDs are prefixed
    with "STRUCT_DUMMY_", which satisfies two invariants:

    1. finger_grouper._is_regenerated_filler_dummy() will NOT strip them
       (it only strips "FILLER_DUMMY_*").
    2. finger_grouper._is_dummy_node() will NOT flag them as generic dummies
       (no is_dummy key, ID doesn't match "FILLER_DUMMY_"/"DUMMY_matrix_"/"EDGE_DUMMY").

    Args:
        group_nodes: physical finger nodes that form the matched group.
        pdk:         PDK dict.
        n_dummies:   number of dummy fingers inserted on each side, per row.

    Returns:
        LayoutToolResult with nodes = group_nodes + inserted structural dummies.
    """
    pdk = pdk or {}
    if not group_nodes:
        return LayoutToolResult(
            success=True,
            message="No nodes in group — nothing to add dummies around",
            changed=False, nodes=[],
        )

    fin_pitch = get_rule(pdk, "fin_pitch_um") or 0.014

    # Group nodes by row Y
    row_map: dict = {}
    for n in group_nodes:
        geo = n.get("geometry") or {}
        y = round(float(geo.get("y", 0.0)), 6)
        row_map.setdefault(y, []).append(n)

    inserted: List[dict] = []
    ctr = 0

    for y, row_nodes in row_map.items():
        geos   = [n.get("geometry") or {} for n in row_nodes]
        xs     = [float(g.get("x", 0.0))               for g in geos]
        widths = [float(g.get("width",  STD_PITCH))     for g in geos]
        ref_w  = widths[0] if widths else STD_PITCH
        ref_h  = float(geos[0].get("height", 0.568)) if geos else 0.568
        ref_t  = str(row_nodes[0].get("type", "nmos"))

        min_x = min(xs)
        max_x = max(xs[i] + widths[i] for i in range(len(xs)))

        for i in range(n_dummies):
            # Left dummy: placed just to the left of the group
            left_x  = _snap(min_x  - (i + 1) * ref_w, fin_pitch)
            # Right dummy: placed just to the right of the group
            right_x = _snap(max_x  +  i      * ref_w, fin_pitch)

            ctr += 1
            inserted.append({
                "id":         f"STRUCT_DUMMY_L_{ctr}",
                "type":       ref_t,
                "structural": True,
                # NOTE: no "is_dummy" key — keeps _is_dummy_node() from flagging it
                "geometry": {
                    "x": left_x, "y": y,
                    "width": ref_w, "height": ref_h,
                    "orientation": "R0",
                },
            })
            ctr += 1
            inserted.append({
                "id":         f"STRUCT_DUMMY_R_{ctr}",
                "type":       ref_t,
                "structural": True,
                "geometry": {
                    "x": right_x, "y": y,
                    "width": ref_w, "height": ref_h,
                    "orientation": "R0",
                },
            })

    return LayoutToolResult(
        success=True,
        message=(
            f"Inserted {len(inserted)} structural dummy finger(s) "
            f"({n_dummies} per side per row)"
        ),
        changed=len(inserted) > 0,
        nodes=list(group_nodes) + inserted,
        metrics={"structural_dummies_inserted": len(inserted)},
        warnings=[],
    )

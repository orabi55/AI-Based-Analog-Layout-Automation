"""
Physical Cell Insertion
=======================
Inserts process-required physical-only cells (endcaps, taps, fillers) into a
placement node list. All functions return LayoutToolResult — never raise.

Functions:
- insert_endcaps:           row-boundary endcap cells
- insert_taps:              substrate / well-tie tap cells at required intervals
- insert_fillers:           density-fill dummies (wraps finger_grouper logic)
- insert_all_physical_cells: ordered pipeline of the three above
"""

from __future__ import annotations

import logging
from typing import List

from ai_agent.core.interfaces import LayoutToolResult, wrap_tool
from ai_agent.pdks.loader import get_rule
from ai_agent.placement.finger_grouper import _resolve_row_overlaps

logger = logging.getLogger("ai_agent")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _snap(x: float, pitch: float) -> float:
    """Snap x to the nearest fin-pitch grid point."""
    if not pitch or pitch <= 0:
        return x
    return round(round(x / pitch) * pitch, 6)


def _row_map(nodes: list) -> dict:
    """Group nodes by (y_row, dev_type) — skipping any without geometry."""
    rows: dict = {}
    for n in nodes:
        if not isinstance(n.get("geometry"), dict):
            continue
        y = round(float(n["geometry"].get("y", 0.0)), 6)
        t = str(n.get("type", "nmos")).lower()
        rows.setdefault((y, t), []).append(n)
    return rows


# ---------------------------------------------------------------------------
# 1. Endcaps
# ---------------------------------------------------------------------------

@wrap_tool
def insert_endcaps(nodes: list, pdk: dict) -> LayoutToolResult:
    """Insert endcap cells at the left and right boundary of every row.

    Cell names come from get_rule(pdk, "endcap_cell_names").
    If a cell name is None/missing the subtype "endcap" is used as a
    placeholder and a warning is added to the result.
    All inserted nodes carry physical_only=True.
    """
    pdk = pdk or {}
    warnings: List[str] = []

    cell_names: list = get_rule(pdk, "endcap_cell_names") or []
    raw_name = cell_names[0] if cell_names else None
    endcap_width: float = get_rule(pdk, "endcap_width_um") or 0.294
    fin_pitch: float = get_rule(pdk, "fin_pitch_um") or 0.014

    if raw_name is None:
        cell_name = "endcap"
        warnings.append(
            "endcap_cell_names is null in PDK — using 'endcap' subtype as placeholder cell name"
        )
    else:
        cell_name = raw_name

    rows = _row_map(nodes)
    inserted: List[dict] = []
    ctr = 0

    for (y, dev_type), row_nodes in rows.items():
        xs = [float(n["geometry"]["x"]) for n in row_nodes]
        x_ends = [float(n["geometry"]["x"]) + float(n["geometry"].get("width", 0.294))
                  for n in row_nodes]
        ref_h = float(row_nodes[0]["geometry"].get("height", 0.568))

        min_x = min(xs)
        max_x = max(x_ends)

        left_x  = _snap(min_x - endcap_width, fin_pitch)
        right_x = _snap(max_x, fin_pitch)

        ctr += 1
        inserted.append({
            "id":           f"ENDCAP_{ctr}_L_{dev_type}",
            "type":         dev_type,
            "subtype":      "endcap",
            "cell_name":    cell_name,
            "physical_only": True,
            "geometry": {
                "x": left_x, "y": y,
                "width": endcap_width, "height": ref_h,
                "orientation": "R0",
            },
        })
        ctr += 1
        inserted.append({
            "id":           f"ENDCAP_{ctr}_R_{dev_type}",
            "type":         dev_type,
            "subtype":      "endcap",
            "cell_name":    cell_name,
            "physical_only": True,
            "geometry": {
                "x": right_x, "y": y,
                "width": endcap_width, "height": ref_h,
                "orientation": "MX",
            },
        })

    return LayoutToolResult(
        success=True,
        message=f"Inserted {len(inserted)} endcap(s)",
        changed=len(inserted) > 0,
        nodes=list(nodes) + inserted,
        metrics={"endcaps_inserted": len(inserted)},
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# 2. Tap cells
# ---------------------------------------------------------------------------

@wrap_tool
def insert_taps(nodes: list, pdk: dict) -> LayoutToolResult:
    """Insert substrate / well-tie tap cells at required intervals.

    NMOS rows receive ptap cells; PMOS rows receive ntap cells.
    Interval is driven by get_rule(pdk, "tap_max_distance_um") — that call
    logs a WARNING automatically when the value comes from a heuristic fallback.
    All inserted nodes are snapped to the fin-pitch grid and carry
    type="tap" and physical_only=True.
    """
    pdk = pdk or {}

    tap_max: float  = get_rule(pdk, "tap_max_distance_um") or 2.5  # warning already logged
    fin_pitch: float = get_rule(pdk, "fin_pitch_um") or 0.014
    tap_w: float    = get_rule(pdk, "tap_width_um") or 0.294
    tap_h: float    = get_rule(pdk, "tap_height_um") or 0.568

    rows = _row_map(nodes)
    inserted: List[dict] = []
    ctr = 0

    for (y, dev_type), row_nodes in rows.items():
        tap_subtype = "ptap" if dev_type.startswith("n") else "ntap"

        xs    = [float(n["geometry"]["x"]) for n in row_nodes]
        x_ends = [float(n["geometry"]["x"]) + float(n["geometry"].get("width", 0.294))
                  for n in row_nodes]
        min_x = min(xs)
        max_x = max(x_ends)
        row_w = max_x - min_x

        if row_w <= 0:
            continue

        # Number of equal-spaced intervals; at least one tap per row
        n_intervals = max(1, int(row_w / tap_max))
        interval = row_w / n_intervals

        for i in range(n_intervals + 1):
            raw_x = min_x + i * interval
            snap_x = _snap(raw_x, fin_pitch)
            ctr += 1
            inserted.append({
                "id":            f"TAP_{ctr}_{tap_subtype}",
                "type":          "tap",
                "subtype":       tap_subtype,
                "physical_only": True,
                "geometry": {
                    "x": snap_x, "y": y,
                    "width": tap_w, "height": tap_h,
                    "orientation": "R0",
                },
            })

    return LayoutToolResult(
        success=True,
        message=f"Inserted {len(inserted)} tap cell(s)",
        changed=len(inserted) > 0,
        nodes=list(nodes) + inserted,
        metrics={"taps_inserted": len(inserted)},
        warnings=[],
    )


# ---------------------------------------------------------------------------
# 3. Fillers — wraps existing logic in finger_grouper, zero reimplementation
# ---------------------------------------------------------------------------

@wrap_tool
def insert_fillers(nodes: list, pdk: dict) -> LayoutToolResult:
    """Fill intra-row gaps using the existing density-filler logic.

    Delegates entirely to finger_grouper._resolve_row_overlaps which already
    implements centering, gap detection, and FILLER_DUMMY insertion at the
    pitch grid. This function only wraps the result in LayoutToolResult.
    """
    before_ids = {n.get("id") for n in nodes}

    updated = _resolve_row_overlaps(list(nodes))

    fillers = [
        n for n in updated
        if n.get("id") not in before_ids
        and str(n.get("id", "")).startswith("FILLER_DUMMY_")
    ]

    return LayoutToolResult(
        success=True,
        message=f"Inserted {len(fillers)} filler(s)",
        changed=len(fillers) > 0,
        nodes=updated,
        metrics={"fillers_inserted": len(fillers)},
        warnings=[],
    )


# ---------------------------------------------------------------------------
# 4. Aggregated pipeline
# ---------------------------------------------------------------------------

@wrap_tool
def insert_all_physical_cells(nodes: list, pdk: dict) -> LayoutToolResult:
    """Run insert_endcaps → insert_taps → insert_fillers in order.

    Aggregates warnings from all three steps.
    Message format: "Inserted N endcaps, M tap cells, K fillers. Warnings: [...]"
    Stops and returns a failure result if any step fails.
    """
    pdk = pdk or {}
    all_warnings: List[str] = []

    ec = insert_endcaps(nodes, pdk)
    if not ec.success:
        return LayoutToolResult(
            success=False,
            message=f"insert_endcaps failed: {ec.message}",
            changed=False,
            nodes=list(nodes),
            warnings=ec.warnings,
        )
    all_warnings.extend(ec.warnings)

    tp = insert_taps(ec.nodes, pdk)
    if not tp.success:
        return LayoutToolResult(
            success=False,
            message=f"insert_taps failed: {tp.message}",
            changed=ec.changed,
            nodes=ec.nodes,
            warnings=all_warnings + tp.warnings,
        )
    all_warnings.extend(tp.warnings)

    fl = insert_fillers(tp.nodes, pdk)
    if not fl.success:
        return LayoutToolResult(
            success=False,
            message=f"insert_fillers failed: {fl.message}",
            changed=ec.changed or tp.changed,
            nodes=tp.nodes,
            warnings=all_warnings + fl.warnings,
        )
    all_warnings.extend(fl.warnings)

    n_ec  = ec.metrics.get("endcaps_inserted", 0)
    n_tp  = tp.metrics.get("taps_inserted", 0)
    n_fl  = fl.metrics.get("fillers_inserted", 0)
    warn_suffix = f" Warnings: {all_warnings}" if all_warnings else ""
    msg = f"Inserted {n_ec} endcaps, {n_tp} tap cells, {n_fl} fillers.{warn_suffix}"

    return LayoutToolResult(
        success=True,
        message=msg,
        changed=ec.changed or tp.changed or fl.changed,
        nodes=fl.nodes,
        metrics={
            "endcaps_inserted": n_ec,
            "taps_inserted":    n_tp,
            "fillers_inserted": n_fl,
        },
        warnings=all_warnings,
    )

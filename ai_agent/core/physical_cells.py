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

    # Filter out existing endcaps to prevent duplication
    nodes_filtered = [n for n in nodes if not str(n.get("id", "")).startswith("ENDCAP_")]

    rows = _row_map(nodes_filtered)
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
        nodes=list(nodes_filtered) + inserted,
        metrics={"endcaps_inserted": len(inserted)},
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# 2. Tap cells
# ---------------------------------------------------------------------------

@wrap_tool
def insert_taps(nodes: list, pdk: dict) -> LayoutToolResult:
    """Insert substrate / well-tie tap cells aligned with devices.

    VDD taps (ntap) are placed at the top of PMOS devices of the topmost PMOS row,
    and GND taps (ptap) are placed at the bottom of NMOS devices of the bottommost NMOS row.
    All inserted nodes are snapped to the fin-pitch grid and carry
    type="tap" and physical_only=True.
    """
    pdk = pdk or {}
    fin_pitch: float = get_rule(pdk, "fin_pitch_um") or 0.014
    tap_h: float    = get_rule(pdk, "tap_height_um") or 0.568

    # Filter out existing taps to prevent duplication
    nodes_filtered = [n for n in nodes if not str(n.get("id", "")).startswith("TAP_")]

    pmos_nodes = [n for n in nodes_filtered if str(n.get("type", "")).lower() == "pmos" and isinstance(n.get("geometry"), dict)]
    nmos_nodes = [n for n in nodes_filtered if str(n.get("type", "")).lower() == "nmos" and isinstance(n.get("geometry"), dict)]

    max_pmos_y = None
    if pmos_nodes:
        max_pmos_y = max(float(n["geometry"].get("y", 0.0)) for n in pmos_nodes)

    min_nmos_y = None
    if nmos_nodes:
        min_nmos_y = min(float(n["geometry"].get("y", 0.0)) for n in nmos_nodes)

    inserted: List[dict] = []
    ctr = 0

    for n in nodes_filtered:
        geom = n.get("geometry")
        if not isinstance(geom, dict):
            continue
        dev_type = str(n.get("type", "")).lower()
        if dev_type not in ("nmos", "pmos"):
            continue
            
        x = float(geom.get("x", 0.0))
        y = float(geom.get("y", 0.0))
        w = float(geom.get("width", 0.294))
        h = float(geom.get("height", 0.568))

        visual_margin = 0.1
        if dev_type == "pmos":
            # Only place VDD tap above the topmost PMOS row
            if max_pmos_y is not None and abs(y - max_pmos_y) < 1e-4:
                tap_subtype = "ntap"
                # Derived from: tap_y - 0.003 (tap bottom) = y + 0.235 (pmos top) + visual_margin
                tap_y = _snap(y + 0.238 + visual_margin, fin_pitch)
                
                ctr += 1
                tap_node = {
                    "id":            f"TAP_{ctr}_{tap_subtype}",
                    "type":          "tap",
                    "subtype":       tap_subtype,
                    "physical_only": True,
                    "layout_cell":   "Ntap",
                    "template_device_id": n.get("id"),
                    "geometry": {
                        "x": _snap(x, fin_pitch),
                        "y": tap_y,
                        "width": w,
                        "height": tap_h,
                        "orientation": "R0",
                    },
                }
                if n.get("layout_index") is not None:
                    tap_node["template_layout_index"] = n["layout_index"]
                inserted.append(tap_node)
        elif dev_type == "nmos":
            # Only place GND tap below the bottommost NMOS row
            if min_nmos_y is not None and abs(y - min_nmos_y) < 1e-4:
                tap_subtype = "ptap"
                # Derived from: tap_y + 0.197 (tap top) = y - 0.333 (nmos bottom) - visual_margin
                tap_y = _snap(y - 0.530 - visual_margin, fin_pitch)
                
                ctr += 1
                tap_node = {
                    "id":            f"TAP_{ctr}_{tap_subtype}",
                    "type":          "tap",
                    "subtype":       tap_subtype,
                    "physical_only": True,
                    "layout_cell":   "Ptap",
                    "template_device_id": n.get("id"),
                    "geometry": {
                        "x": _snap(x, fin_pitch),
                        "y": tap_y,
                        "width": w,
                        "height": tap_h,
                        "orientation": "R0",
                    },
                }
                if n.get("layout_index") is not None:
                    tap_node["template_layout_index"] = n["layout_index"]
                inserted.append(tap_node)

    return LayoutToolResult(
        success=True,
        message=f"Inserted {len(inserted)} tap cell(s)",
        changed=len(inserted) > 0,
        nodes=list(nodes_filtered) + inserted,
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


@wrap_tool
def insert_guard_ring(
    nodes: list,
    pdk: dict,
    group_node_ids: list = None,
    ring_type: str = "ptap",
    spacing_um: float = 0.5,
    tap_width_um: float = 0.294,
    bounding_box: tuple = None,
) -> LayoutToolResult:
    """Add an automated substrate isolation guard ring around the selected group of devices or around
    the entire layout bounding box. Places ptap cells for NMOS / p-substrate and ntap cells for PMOS / n-well.
    """
    pdk = pdk or {}
    group_ids = set(group_node_ids) if group_node_ids else None
    
    # 1. Filter out existing guard ring nodes to prevent duplication
    nodes_filtered = [n for n in nodes if not str(n.get("id", "")).startswith("GUARDRING_")]
    
    # 2. Automatically detect default ring type based on target device types
    pmos_count = 0
    nmos_count = 0
    for n in nodes_filtered:
        if group_ids and n.get("id") not in group_ids:
            continue
        t = str(n.get("type", "")).lower()
        if t == "pmos":
            pmos_count += 1
        elif t == "nmos":
            nmos_count += 1
            
    if pmos_count > 0 and nmos_count == 0:
        ring_type = "ntap"
    elif nmos_count > 0 and pmos_count == 0:
        ring_type = "ptap"
    
    if bounding_box:
        min_x, max_x, min_y, max_y = bounding_box
    else:
        # 3. Find bounding box of selected nodes
        xs, ys, x_ends, y_ends = [], [], [], []
        for n in nodes_filtered:
            if not isinstance(n.get("geometry"), dict):
                continue
            if group_ids and n.get("id") not in group_ids:
                continue
            geom = n["geometry"]
            x = float(geom.get("x", 0.0))
            y = float(geom.get("y", 0.0))
            w = float(geom.get("width", 0.294))
            h = float(geom.get("height", 0.568))
            xs.append(x)
            ys.append(y)
            x_ends.append(x + w)
            y_ends.append(y + h)
            
        if not xs:
            return LayoutToolResult(
                success=False,
                message="insert_guard_ring failed: no devices with geometry to surround.",
                nodes=list(nodes),
            )
            
        min_x = min(xs)
        max_x = max(x_ends)
        min_y = min(ys)
        max_y = max(y_ends)
    
    # 4. Get PDK rules
    fin_pitch: float = get_rule(pdk, "fin_pitch_um") or 0.014
    tap_w: float = tap_width_um
    tap_h: float = get_rule(pdk, "tap_height_um") or 0.568
    
    # Calculate ring boundaries
    ring_min_x = _snap(min_x - spacing_um - tap_w, fin_pitch)
    ring_max_x = _snap(max_x + spacing_um, fin_pitch)
    ring_min_y = _snap(min_y - spacing_um - tap_h, fin_pitch)
    ring_max_y = _snap(max_y + spacing_um, fin_pitch)
    
    inserted = []
    ctr = 0
    
    # Left segment: x = ring_min_x, y goes from ring_min_y to ring_max_y
    y_curr = ring_min_y
    while y_curr <= ring_max_y + 1e-5:
        ctr += 1
        inserted.append({
            "id": f"GUARDRING_{ctr}_{ring_type}",
            "type": "tap",
            "subtype": ring_type,
            "physical_only": True,
            "geometry": {
                "x": ring_min_x,
                "y": round(y_curr, 6),
                "width": tap_w,
                "height": tap_h,
                "orientation": "R0",
            }
        })
        y_curr += tap_h
        
    # Right segment: x = ring_max_x, y goes from ring_min_y to ring_max_y
    y_curr = ring_min_y
    while y_curr <= ring_max_y + 1e-5:
        ctr += 1
        inserted.append({
            "id": f"GUARDRING_{ctr}_{ring_type}",
            "type": "tap",
            "subtype": ring_type,
            "physical_only": True,
            "geometry": {
                "x": ring_max_x,
                "y": round(y_curr, 6),
                "width": tap_w,
                "height": tap_h,
                "orientation": "R0",
            }
        })
        y_curr += tap_h
        
    # Bottom segment: y = ring_min_y, x goes from ring_min_x + tap_w to ring_max_x - tap_w
    x_curr = ring_min_x + tap_w
    while x_curr <= ring_max_x - tap_w + 1e-5:
        ctr += 1
        inserted.append({
            "id": f"GUARDRING_{ctr}_{ring_type}",
            "type": "tap",
            "subtype": ring_type,
            "physical_only": True,
            "geometry": {
                "x": _snap(x_curr, fin_pitch),
                "y": ring_min_y,
                "width": tap_w,
                "height": tap_h,
                "orientation": "R0",
            }
        })
        x_curr += tap_w
        
    # Top segment: y = ring_max_y, x goes from ring_min_x + tap_w to ring_max_x - tap_w
    x_curr = ring_min_x + tap_w
    while x_curr <= ring_max_x - tap_w + 1e-5:
        ctr += 1
        inserted.append({
            "id": f"GUARDRING_{ctr}_{ring_type}",
            "type": "tap",
            "subtype": ring_type,
            "physical_only": True,
            "geometry": {
                "x": _snap(x_curr, fin_pitch),
                "y": ring_max_y,
                "width": tap_w,
                "height": tap_h,
                "orientation": "R0",
            }
        })
        x_curr += tap_w
        
    return LayoutToolResult(
        success=True,
        message=f"Inserted isolation guard ring using {len(inserted)} {ring_type} tap cell(s).",
        changed=len(inserted) > 0,
        nodes=list(nodes_filtered) + inserted,
        metrics={"guard_ring_taps_inserted": len(inserted)},
    )


@wrap_tool
def insert_edge_dummies(nodes: list, pdk: dict) -> LayoutToolResult:
    """Insert dummy transistor cells at the left and right boundary of every device row.

    Automatically handles PMOS and NMOS rows separately. Copies the matching transistor
    electrical and geometric layout parameters to ensure physical identicality.
    This fulfills Option 6: Edge Dummies for Lithography Isolation.
    """
    import copy
    pdk = pdk or {}
    dummy_width: float = get_rule(pdk, "tap_width_um") or 0.294
    dummy_height: float = get_rule(pdk, "tap_height_um") or 0.568
    fin_pitch: float = get_rule(pdk, "fin_pitch_um") or 0.014

    # Filter out existing automatically placed edge dummies to make it idempotent
    nodes_filtered = [n for n in nodes if not str(n.get("id", "")).startswith("DUMMY_ROW_")]

    rows = _row_map(nodes_filtered)
    inserted: List[dict] = []
    ctr = 0

    for (y, dev_type), row_nodes in rows.items():
        # Skip rows that are already composed entirely of physical-only or tap cells
        active_nodes = [n for n in row_nodes if not n.get("physical_only") and n.get("type") in ("nmos", "pmos")]
        if not active_nodes:
            continue

        xs = [float(n["geometry"]["x"]) for n in active_nodes]
        x_ends = [float(n["geometry"]["x"]) + float(n["geometry"].get("width", 0.294)) for n in active_nodes]
        ref_h = float(active_nodes[0]["geometry"].get("height", dummy_height))

        min_x = min(xs)
        max_x = max(x_ends)

        left_x = _snap(min_x - dummy_width, fin_pitch)
        right_x = _snap(max_x, fin_pitch)

        # Mirror first real node in the row for identical electrical properties
        template = active_nodes[0]
        electrical = copy.deepcopy(template.get("electrical", {"l": 1.4e-08, "nf": 1, "nfin": 1}))

        ctr += 1
        dummy_l = {
            "id": f"DUMMY_ROW_L_{ctr}_{dev_type}",
            "type": dev_type,
            "is_dummy": True,
            "physical_only": True,
            "electrical": electrical,
            "geometry": {
                "x": left_x,
                "y": y,
                "width": dummy_width,
                "height": ref_h,
                "orientation": "R0",
            }
        }
        if template.get("layout_index") is not None:
            dummy_l["template_layout_index"] = template["layout_index"]
        if template.get("layout_cell"):
            dummy_l["layout_cell"] = template["layout_cell"]

        ctr += 1
        dummy_r = {
            "id": f"DUMMY_ROW_R_{ctr}_{dev_type}",
            "type": dev_type,
            "is_dummy": True,
            "physical_only": True,
            "electrical": electrical,
            "geometry": {
                "x": right_x,
                "y": y,
                "width": dummy_width,
                "height": ref_h,
                "orientation": "R0",
            }
        }
        if template.get("layout_index") is not None:
            dummy_r["template_layout_index"] = template["layout_index"]
        if template.get("layout_cell"):
            dummy_r["layout_cell"] = template["layout_cell"]

        inserted.extend([dummy_l, dummy_r])

    return LayoutToolResult(
        success=True,
        message=f"Inserted {len(inserted)} edge dummy cell(s) automatically at both row boundaries.",
        changed=len(inserted) > 0,
        nodes=list(nodes_filtered) + inserted,
        metrics={"edge_dummies_inserted": len(inserted)},
    )


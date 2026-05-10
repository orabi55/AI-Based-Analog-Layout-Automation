"""
Layout Operations Core
======================
Deterministic pure-data implementations of every layout operation that
appears in the GUI menus, toolbars, and right-click context menus.

All functions return LayoutToolResult and never raise.
They operate entirely on the node list — no Qt / GUI dependencies.
"""
from __future__ import annotations

import copy
import uuid
from typing import List, Optional

from ai_agent.core.interfaces import LayoutToolResult, wrap_tool

_ABUT_PITCH = 0.070   # µm — diffusion-sharing pitch (standard cell rule)
_STD_PITCH  = 0.294   # µm — standard device slot width


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find(nodes: list, device_id: str) -> Optional[dict]:
    return next((n for n in nodes if str(n.get("id", "")) == device_id), None)


def _geo(node: dict) -> dict:
    return node.get("geometry") or {}


# ---------------------------------------------------------------------------
# delete_device
# ---------------------------------------------------------------------------

@wrap_tool
def delete_device(nodes: list, device_id: str) -> LayoutToolResult:
    """Remove a device (and its finger nodes) from the layout."""
    before = len(nodes)
    updated = [n for n in nodes if str(n.get("id", "")) != device_id
               and not str(n.get("id", "")).startswith(device_id + "_")]
    removed = before - len(updated)
    if removed == 0:
        return LayoutToolResult(
            success=False,
            message=f"delete_device: device not found: {device_id!r}",
            nodes=list(nodes),
        )
    return LayoutToolResult(
        success=True,
        message=f"Deleted {device_id} ({removed} node(s) removed)",
        changed=True,
        nodes=updated,
        metrics={"removed_count": removed},
    )


# ---------------------------------------------------------------------------
# align_devices
# ---------------------------------------------------------------------------

@wrap_tool
def align_devices(
    nodes: list,
    device_ids: list,
    axis: str = "x",
    mode: str = "mean",
    reference_id: str = None,
) -> LayoutToolResult:
    """Align a group of devices along x or y.

    axis: 'x' (align left-edges) or 'y' (align to same row)
    mode: 'mean' | 'min' | 'max' | 'reference'
          'reference' uses reference_id's coordinate as the target
    """
    axis  = axis.lower()
    mode  = mode.lower()
    id_set = set(str(i) for i in device_ids)

    targets = [n for n in nodes if str(n.get("id", "")) in id_set]
    if not targets:
        return LayoutToolResult(
            success=False,
            message="align_devices: no matching device IDs found",
            nodes=list(nodes),
        )

    key = "x" if axis == "x" else "y"

    # Determine target coordinate
    if mode == "reference" and reference_id:
        ref = _find(nodes, reference_id)
        if ref is None:
            return LayoutToolResult(
                success=False,
                message=f"align_devices: reference device not found: {reference_id!r}",
                nodes=list(nodes),
            )
        target_val = float(_geo(ref).get(key, 0.0))
    else:
        vals = [float(_geo(n).get(key, 0.0)) for n in targets]
        if mode == "min":
            target_val = min(vals)
        elif mode == "max":
            target_val = max(vals)
        else:  # mean
            target_val = sum(vals) / len(vals)

    updated = copy.deepcopy(nodes)
    id_map  = {str(n.get("id", "")): n for n in updated}
    for did in id_set:
        n = id_map.get(did)
        if n:
            (n.get("geometry") or {}).update({key: round(target_val, 6)})

    return LayoutToolResult(
        success=True,
        message=f"Aligned {len(targets)} device(s) on {axis}-axis "
                f"({mode}={target_val:.3f} µm)",
        changed=True,
        nodes=updated,
        metrics={"axis": axis, "mode": mode, "target_value": target_val},
    )


# ---------------------------------------------------------------------------
# abut_devices
# ---------------------------------------------------------------------------

@wrap_tool
def abut_devices(nodes: list, device_a: str, device_b: str) -> LayoutToolResult:
    """Place device_b immediately to the right of device_a sharing diffusion.

    Sets abutment metadata on both nodes and snaps to the shared-diffusion
    pitch (0.070 µm rather than the standard 0.294 µm slot width).
    """
    updated = copy.deepcopy(nodes)
    na = _find(updated, device_a)
    nb = _find(updated, device_b)
    if na is None or nb is None:
        missing = device_a if na is None else device_b
        return LayoutToolResult(
            success=False,
            message=f"abut_devices: device not found: {missing!r}",
            nodes=list(nodes),
        )
    ga = _geo(na)
    gb = _geo(nb)
    ax = float(ga.get("x", 0.0))
    aw = float(ga.get("width", _STD_PITCH))
    y  = float(ga.get("y", 0.0))

    gb["x"] = round(ax + aw - _ABUT_PITCH, 6)
    gb["y"] = y
    na.setdefault("abutment", {})["abut_right"] = True
    nb.setdefault("abutment", {})["abut_left"]  = True

    return LayoutToolResult(
        success=True,
        message=f"Abutted {device_a} ↔ {device_b} "
                f"(b.x = {gb['x']:.3f} µm, shared-diffusion pitch)",
        changed=True,
        nodes=updated,
        metrics={"abut_x": gb["x"], "abut_y": y},
    )


# ---------------------------------------------------------------------------
# merge_shared_source / merge_shared_drain
# ---------------------------------------------------------------------------

@wrap_tool
def merge_shared_source(nodes: list, device_a: str, device_b: str) -> LayoutToolResult:
    """Align two same-type devices for shared-source diffusion (SS merge).

    device_b is placed immediately LEFT of device_a and flipped horizontally
    so their source diffusions face each other and can be shared.
    """
    updated = copy.deepcopy(nodes)
    na = _find(updated, device_a)
    nb = _find(updated, device_b)
    if na is None or nb is None:
        missing = device_a if na is None else device_b
        return LayoutToolResult(
            success=False,
            message=f"merge_shared_source: device not found: {missing!r}",
            nodes=list(nodes),
        )
    if na.get("type", "").lower() != nb.get("type", "").lower():
        return LayoutToolResult(
            success=False,
            message="merge_shared_source: both devices must be the same type",
            nodes=list(nodes),
        )
    ga = _geo(na)
    gb = _geo(nb)
    y   = float(ga.get("y", 0.0))
    ax  = float(ga.get("x", 0.0))
    bw  = float(gb.get("width", _STD_PITCH))

    gb["x"] = round(ax - bw + _ABUT_PITCH, 6)
    gb["y"] = y
    ga["orientation"] = "R0"
    gb["orientation"] = "R0_FH"   # horizontal flip on B so sources face

    return LayoutToolResult(
        success=True,
        message=f"SS-merged {device_b} ← {device_a} "
                f"(b.x = {gb['x']:.3f} µm, b flipped)",
        changed=True,
        nodes=updated,
    )


@wrap_tool
def merge_shared_drain(nodes: list, device_a: str, device_b: str) -> LayoutToolResult:
    """Align two same-type devices for shared-drain diffusion (DD merge).

    device_b is placed immediately RIGHT of device_a and flipped horizontally
    so their drain diffusions face each other.
    """
    updated = copy.deepcopy(nodes)
    na = _find(updated, device_a)
    nb = _find(updated, device_b)
    if na is None or nb is None:
        missing = device_a if na is None else device_b
        return LayoutToolResult(
            success=False,
            message=f"merge_shared_drain: device not found: {missing!r}",
            nodes=list(nodes),
        )
    if na.get("type", "").lower() != nb.get("type", "").lower():
        return LayoutToolResult(
            success=False,
            message="merge_shared_drain: both devices must be the same type",
            nodes=list(nodes),
        )
    ga = _geo(na)
    gb = _geo(nb)
    y   = float(ga.get("y", 0.0))
    ax  = float(ga.get("x", 0.0))
    aw  = float(ga.get("width", _STD_PITCH))

    gb["x"] = round(ax + aw - _ABUT_PITCH, 6)
    gb["y"] = y
    ga["orientation"] = "R0"
    gb["orientation"] = "R0_FH"

    return LayoutToolResult(
        success=True,
        message=f"DD-merged {device_a} → {device_b} "
                f"(b.x = {gb['x']:.3f} µm, b flipped)",
        changed=True,
        nodes=updated,
    )


# ---------------------------------------------------------------------------
# lock_device / unlock_device
# ---------------------------------------------------------------------------

@wrap_tool
def lock_device(nodes: list, device_id: str) -> LayoutToolResult:
    """Mark a device as locked (position frozen in the editor)."""
    updated = copy.deepcopy(nodes)
    node = _find(updated, device_id)
    if node is None:
        return LayoutToolResult(
            success=False,
            message=f"lock_device: device not found: {device_id!r}",
            nodes=list(nodes),
        )
    node["locked"] = True
    return LayoutToolResult(
        success=True,
        message=f"Locked {device_id} — position frozen",
        changed=True,
        nodes=updated,
    )


@wrap_tool
def unlock_device(nodes: list, device_id: str) -> LayoutToolResult:
    """Remove the position lock from a device."""
    updated = copy.deepcopy(nodes)
    node = _find(updated, device_id)
    if node is None:
        return LayoutToolResult(
            success=False,
            message=f"unlock_device: device not found: {device_id!r}",
            nodes=list(nodes),
        )
    node.pop("locked", None)
    return LayoutToolResult(
        success=True,
        message=f"Unlocked {device_id} — position now free",
        changed=True,
        nodes=updated,
    )


# ---------------------------------------------------------------------------
# set_device_color / reset_device_color
# ---------------------------------------------------------------------------

@wrap_tool
def set_device_color(nodes: list, device_id: str, color: str) -> LayoutToolResult:
    """Assign a custom hex color to a device (e.g. '#ff6b6b')."""
    updated = copy.deepcopy(nodes)
    node = _find(updated, device_id)
    if node is None:
        return LayoutToolResult(
            success=False,
            message=f"set_device_color: device not found: {device_id!r}",
            nodes=list(nodes),
        )
    node["color"] = str(color)
    return LayoutToolResult(
        success=True,
        message=f"Set {device_id} color → {color}",
        changed=True,
        nodes=updated,
    )


@wrap_tool
def reset_device_color(nodes: list, device_id: str) -> LayoutToolResult:
    """Remove the custom color from a device (reverts to type default)."""
    updated = copy.deepcopy(nodes)
    node = _find(updated, device_id)
    if node is None:
        return LayoutToolResult(
            success=False,
            message=f"reset_device_color: device not found: {device_id!r}",
            nodes=list(nodes),
        )
    node.pop("color", None)
    return LayoutToolResult(
        success=True,
        message=f"Reset {device_id} color to default",
        changed=True,
        nodes=updated,
    )


# ---------------------------------------------------------------------------
# get_layout_bounds
# ---------------------------------------------------------------------------

@wrap_tool
def get_layout_bounds(nodes: list) -> LayoutToolResult:
    """Return bounding box, aspect ratio, and area of the current layout."""
    real = [n for n in nodes if not n.get("is_dummy")]
    if not real:
        return LayoutToolResult(
            success=True,
            message="Layout is empty",
            changed=False,
            nodes=list(nodes),
            metrics={"width": 0, "height": 0, "area": 0},
        )

    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    active_area = 0.0

    for n in real:
        g = _geo(n)
        try:
            x = float(g.get("x", 0)); y = float(g.get("y", 0))
            w = float(g.get("width", 0)); h = float(g.get("height", 0))
            min_x = min(min_x, x); min_y = min(min_y, y)
            max_x = max(max_x, x + w); max_y = max(max_y, y + h)
            active_area += w * h
        except (TypeError, ValueError):
            continue

    width  = max_x - min_x if max_x > min_x else 0.0
    height = max_y - min_y if max_y > min_y else 0.0
    area   = width * height
    util   = (active_area / area * 100) if area > 0 else 0.0
    aspect = f"{width/height:.2f}" if height > 0 else "?"

    return LayoutToolResult(
        success=True,
        message=f"Bounds: {width:.3f} × {height:.3f} µm  "
                f"(area={area:.3f} µm², util={util:.1f}%, AR={aspect})",
        changed=False,
        nodes=list(nodes),
        metrics={
            "min_x": round(min_x, 6), "min_y": round(min_y, 6),
            "max_x": round(max_x, 6), "max_y": round(max_y, 6),
            "width": round(width, 6),  "height": round(height, 6),
            "area":  round(area, 6),
            "active_area": round(active_area, 6),
            "utilization_pct": round(util, 2),
            "aspect_ratio": aspect,
        },
    )


# ---------------------------------------------------------------------------
# match_devices  (data-level: delegates to universal_pattern_generator)
# ---------------------------------------------------------------------------

@wrap_tool
def match_devices(
    nodes: list,
    device_ids: list,
    technique: str = "interdigitated",
    custom_pattern: str = None,
) -> LayoutToolResult:
    """Apply an interdigitation or common-centroid pattern to a set of devices.

    technique: 'interdigitated' | 'common_centroid' | 'common_centroid_2d' | 'custom'
    custom_pattern: pattern string when technique=='custom' (e.g. 'M0 M1 M0 / M1 M0 M1')
    """
    from ai_agent.matching.universal_pattern_generator import generate_placement_grid
    import re as _re
    import copy

    updated = copy.deepcopy(nodes)
    id_set  = set(str(i) for i in (device_ids or []))
    targets = [n for n in updated if str(n.get("id", "")) in id_set]

    if not targets:
        return LayoutToolResult(
            success=False,
            message="match_devices: no matching device IDs found",
            nodes=list(nodes),
        )

    # Group fingers by parent device
    parent_map: dict = {}
    for n in targets:
        nid = str(n.get("id", ""))
        m   = _re.match(r"^([A-Za-z]+\d+)", nid)
        parent = m.group(1) if m else nid
        parent_map.setdefault(parent, []).append(n)

    parents = sorted(parent_map.keys())
    token_to_parent = {f"M{i}": p for i, p in enumerate(parents)}
    token_counts = {f"M{i}": len(parent_map[p]) for i, p in enumerate(parents)}

    rows = 2 if technique.lower() == "common_centroid_2d" else 1
    if custom_pattern and "/" in (custom_pattern or ""):
        rows = custom_pattern.count("/") + 1

    tech_upper = technique.upper().replace("INTERDIGITATED", "CC").replace(
        "COMMON_CENTROID", "CC").replace("COMMON_CENTROID_2D", "CC")

    try:
        grid = generate_placement_grid(token_counts, tech_upper, rows, custom_pattern)
    except Exception as exc:
        return LayoutToolResult(
            success=False,
            message=f"match_devices: pattern generation failed — {exc}",
            nodes=list(nodes),
        )

    # Anchor at the min-x of the selection
    xs = [float((n.get("geometry") or {}).get("x", 0.0)) for n in targets]
    ys = [float((n.get("geometry") or {}).get("y", 0.0)) for n in targets]
    anchor_x = min(xs) if xs else 0.0
    anchor_y = min(ys) if ys else 0.0

    ref_w = float((targets[0].get("geometry") or {}).get("width",  _STD_PITCH))
    ref_h = float((targets[0].get("geometry") or {}).get("height", 0.568))

    available: dict = {p: list(parent_map[p]) for p in parents}

    for gc in grid:
        token  = gc.get("device", "")
        parent = token_to_parent.get(token)
        if parent is None or not available.get(parent):
            continue
        node = available[parent].pop(0)
        geo  = node.setdefault("geometry", {})
        geo["x"] = round(anchor_x + gc["x_index"] * ref_w, 6)
        geo["y"] = round(anchor_y + gc["y_index"] * ref_h, 6)

    return LayoutToolResult(
        success=True,
        message=f"Applied {technique} matching to {len(targets)} device(s) "
                f"({len(parents)} parent(s))",
        changed=True,
        nodes=updated,
        metrics={"technique": technique, "device_count": len(targets),
                 "parent_count": len(parents)},
    )

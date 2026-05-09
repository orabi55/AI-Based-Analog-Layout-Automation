"""
Passive Device Placer
=====================
Geometry generation for resistors, MOM capacitors, and MOS capacitors.
All functions return LayoutToolResult — never raise.

Each function stores a `_passive` key on the returned node so that
reshape_passive() can re-derive correct dimensions without extra arguments.

Functions
---------
place_resistor       — R geometry from area + aspect ratio; optional folding
place_mom_cap        — interdigitated metal-finger capacitor; can_overlap=True
place_mos_cap        — transistor geometry with gate tied to drain
reshape_passive      — resize any passive while preserving type and electrical ratios
"""

from __future__ import annotations

import math
import logging
from typing import List, Optional

from ai_agent.core.interfaces import LayoutToolResult, wrap_tool

logger = logging.getLogger("ai_agent")

# ---------------------------------------------------------------------------
# Physical constants (SAED14nm / generic FinFET)
# ---------------------------------------------------------------------------
_TRANSISTOR_PITCH_UM   = 0.294   # non-abutted device pitch
_TRANSISTOR_HEIGHT_UM  = 0.568   # physical device height per row
_MOM_FINGER_PITCH_UM   = 0.268   # M2-M8 width+spacing (0.134 × 2)
_MOM_FINGER_WIDTH_UM   = 0.100   # minimum metal width
_SERIES_FOLD_THRESHOLD = 10.0    # fold if a single segment exceeds this height (µm)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _checked_area(area_um2: float, fn: str) -> Optional[str]:
    """Return an error string if area is invalid, else None."""
    if area_um2 is None or area_um2 <= 0:
        return f"{fn}: area_um2 must be > 0, got {area_um2!r}"
    return None


def _apply_passive(node: dict, passive_meta: dict, geo: dict, extra: dict) -> dict:
    """Return a copy of *node* with passive metadata and geometry applied.

    The geometry dict is copied fresh so callers that reuse the same node
    object across multiple calls do not see aliasing side-effects.
    """
    out = dict(node)
    out["geometry"] = dict(node.get("geometry") or {})  # break aliasing
    out["geometry"].update(geo)
    out["_passive"] = passive_meta
    out.update(extra)
    return out


# ---------------------------------------------------------------------------
# 1. Resistor
# ---------------------------------------------------------------------------

@wrap_tool
def place_resistor(
    node: dict,
    area_um2: float,
    aspect_ratio: float = 4.0,
    allow_series: bool = True,
    allow_parallel: bool = True,
) -> LayoutToolResult:
    """Compute resistor geometry from target area and aspect ratio.

    aspect_ratio = L / W  (length-to-width, the resistance-determining ratio).

    Series folding (allow_series=True):
      When a single segment would exceed _SERIES_FOLD_THRESHOLD µm in height,
      the resistor is folded into N stacked segments of equal height.
      Each fold preserves the total R (same effective L/W), but reduces the
      layout height by N at the cost of N× wider footprint.

    Parallel (allow_parallel):
      Flag reserved for future multi-finger parallel configurations.
      Currently only n_parallel=1 is generated; the flag gates that path.

    Returns:
        node with type="resistor", segments (list[rect]), _passive metadata.
    Metrics include actual_resistance_ratio (= aspect_ratio = effective L/W).
    """
    err = _checked_area(area_um2, "place_resistor")
    if err:
        return LayoutToolResult(success=False, message=err, nodes=[node])
    if aspect_ratio <= 0:
        return LayoutToolResult(
            success=False,
            message=f"place_resistor: aspect_ratio must be > 0, got {aspect_ratio!r}",
            nodes=[node],
        )

    # Base dimensions from area = width × height, aspect_ratio = height / width
    base_width  = math.sqrt(area_um2 / aspect_ratio)
    base_height = math.sqrt(area_um2 * aspect_ratio)

    # ── Series folding ────────────────────────────────────────────────────
    n_series = 1
    if allow_series and base_height > _SERIES_FOLD_THRESHOLD:
        n_series = max(2, math.ceil(base_height / _SERIES_FOLD_THRESHOLD))

    seg_width  = base_width
    seg_height = base_height / n_series  # each fold reduces individual height

    # ── Parallel (future expansion; n_parallel=1 today) ──────────────────
    n_parallel = 1   # allow_parallel gates this path when > 1 is needed

    warnings: List[str] = []
    if not allow_parallel and n_parallel > 1:
        warnings.append("allow_parallel=False; parallel segments were not generated")

    # ── Build segment rects (relative to node origin) ────────────────────
    segments: List[dict] = []
    for s in range(n_series):
        for p in range(n_parallel):
            segments.append({
                "x":      round(p * seg_width,  6),
                "y":      round(s * seg_height, 6),
                "width":  round(seg_width,  6),
                "height": round(seg_height, 6),
            })

    # Total footprint
    total_width  = round(seg_width  * n_parallel, 6)
    total_height = round(seg_height * n_series,   6)

    # actual L/W is invariant to folding: same ρ·L/W regardless of n_series
    actual_resistance_ratio = round(aspect_ratio, 6)

    passive_meta = {
        "area_um2":    area_um2,
        "aspect_ratio": aspect_ratio,
        "n_series":    n_series,
        "n_parallel":  n_parallel,
        "seg_width":   seg_width,
        "seg_height":  seg_height,
    }

    result_node = _apply_passive(
        node, passive_meta,
        geo={"width": total_width, "height": total_height},
        extra={
            "type":     "resistor",
            "segments": segments,
        },
    )
    result_node.setdefault("electrical", {}).update({
        "width_um":  round(seg_width,  6),
        "length_um": round(base_height, 6),
    })

    return LayoutToolResult(
        success=True,
        message=(
            f"Placed resistor '{node.get('id', '?')}': "
            f"{n_series}S×{n_parallel}P, "
            f"{total_width:.3f}×{total_height:.3f} µm, "
            f"L/W={actual_resistance_ratio:.2f}"
        ),
        changed=True,
        nodes=[result_node],
        metrics={
            "actual_resistance_ratio": actual_resistance_ratio,
            "n_series":   n_series,
            "n_parallel": n_parallel,
            "width_um":   total_width,
            "height_um":  total_height,
            "area_um2":   area_um2,
        },
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# 2. MOM capacitor
# ---------------------------------------------------------------------------

@wrap_tool
def place_mom_cap(
    node: dict,
    area_um2: float,
    layers: list = None,
) -> LayoutToolResult:
    """Rectangular interdigitated metal-finger MOM capacitor.

    Derives a square-ish footprint from area_um2, then packs as many
    alternating A/B fingers as fit at the M2-M8 pitch.

    can_overlap=True is set on the returned node — MOM caps may be stacked
    above transistor rows in the floorplan (they occupy routing metal only).
    """
    err = _checked_area(area_um2, "place_mom_cap")
    if err:
        return LayoutToolResult(success=False, message=err, nodes=[node])

    # None  → use default layers.  [] → explicit empty list → error.
    if layers is None:
        layers = ["M2", "M3", "M4"]
    else:
        layers = list(layers)
    if len(layers) == 0:
        return LayoutToolResult(
            success=False,
            message="place_mom_cap: layers must be non-empty",
            nodes=[node],
        )
    n_layers = len(layers)

    # Derive a square footprint then parameterise fingers
    side = math.sqrt(area_um2)

    # Number of fingers that fit across the width; snap to even (A+B pairs)
    n_fingers = max(2, int(side / _MOM_FINGER_PITCH_UM))
    if n_fingers % 2 != 0:
        n_fingers += 1

    actual_width  = round(n_fingers * _MOM_FINGER_PITCH_UM, 6)
    finger_length = round(area_um2 / actual_width, 6)

    # Build finger descriptors (relative to node origin, one entry per layer)
    fingers: List[dict] = []
    for i in range(n_fingers):
        net = "A" if i % 2 == 0 else "B"
        for layer in layers:
            fingers.append({
                "net":    net,
                "layer":  layer,
                "x":      round(i * _MOM_FINGER_PITCH_UM, 6),
                "y":      0.0,
                "width":  _MOM_FINGER_WIDTH_UM,
                "height": finger_length,
            })

    passive_meta = {
        "area_um2":     area_um2,
        "layers":       layers,
        "finger_count": n_fingers,
        "n_layers":     n_layers,
    }

    result_node = _apply_passive(
        node, passive_meta,
        geo={"width": actual_width, "height": finger_length},
        extra={
            "type":        "mom_cap",
            "can_overlap": True,   # MOM caps may be placed above transistor rows
            "fingers":     fingers,
        },
    )

    return LayoutToolResult(
        success=True,
        message=(
            f"Placed MOM cap '{node.get('id', '?')}': "
            f"{n_fingers} fingers × {n_layers} layer(s), "
            f"{actual_width:.3f}×{finger_length:.3f} µm, "
            f"can_overlap=True"
        ),
        changed=True,
        nodes=[result_node],
        metrics={
            "finger_count": n_fingers,
            "n_layers":    n_layers,
            "can_overlap": True,
            "width_um":    actual_width,
            "height_um":   finger_length,
            "area_um2":    area_um2,
        },
        warnings=[],
    )


# ---------------------------------------------------------------------------
# 3. MOS capacitor
# ---------------------------------------------------------------------------

@wrap_tool
def place_mos_cap(
    node: dict,
    nf: int,
    width_um: float,
) -> LayoutToolResult:
    """MOS capacitor — transistor geometry with gate tied to drain.

    Reuses standard transistor physical geometry:
      Physical width  = nf × TRANSISTOR_PITCH_UM (0.294 µm per finger slot)
      Physical height = TRANSISTOR_HEIGHT_UM      (0.568 µm)

    width_um is the channel width per finger (electrical parameter).
    The gate and drain terminals are marked as tied in electrical metadata.
    """
    if nf is None or nf < 1:
        return LayoutToolResult(
            success=False,
            message=f"place_mos_cap: nf must be >= 1, got {nf!r}",
            nodes=[node],
        )
    if width_um is None or width_um <= 0:
        return LayoutToolResult(
            success=False,
            message=f"place_mos_cap: width_um must be > 0, got {width_um!r}",
            nodes=[node],
        )

    total_width_um = round(nf * _TRANSISTOR_PITCH_UM, 6)
    area_um2       = round(total_width_um * _TRANSISTOR_HEIGHT_UM, 6)

    passive_meta = {
        "nf":             nf,
        "width_um":       width_um,
        "total_width_um": total_width_um,
        "area_um2":       area_um2,
    }

    result_node = _apply_passive(
        node, passive_meta,
        geo={"width": total_width_um, "height": _TRANSISTOR_HEIGHT_UM},
        extra={"type": "mos_cap"},
    )
    result_node.setdefault("electrical", {}).update({
        "nf":             nf,
        "width_um":       round(width_um, 6),
        "gate_drain_tied": True,
    })

    return LayoutToolResult(
        success=True,
        message=(
            f"Placed MOS cap '{node.get('id', '?')}': "
            f"nf={nf}, width_um={width_um:.3f}, "
            f"physical {total_width_um:.3f}×{_TRANSISTOR_HEIGHT_UM:.3f} µm. "
            f"Gate tied to drain."
        ),
        changed=True,
        nodes=[result_node],
        metrics={
            "nf":             nf,
            "width_um":       round(width_um, 6),
            "total_width_um": total_width_um,
            "height_um":      _TRANSISTOR_HEIGHT_UM,
            "area_um2":       area_um2,
        },
        warnings=[],
    )


# ---------------------------------------------------------------------------
# 4. Reshape (resize any passive, preserving type and electrical ratios)
# ---------------------------------------------------------------------------

@wrap_tool
def reshape_passive(
    node: dict,
    new_area_um2: float,
) -> LayoutToolResult:
    """Resize any passive node while preserving type and electrical ratios.

    Reads `_passive` metadata written by the original place_* call, then
    re-invokes the same place function with the new area.

    Supported types: resistor, mom_cap, mos_cap.
    """
    err = _checked_area(new_area_um2, "reshape_passive")
    if err:
        return LayoutToolResult(success=False, message=err, nodes=[node])

    node_type = str(node.get("type", ""))
    passive   = node.get("_passive") or {}

    if node_type == "resistor":
        return place_resistor(
            node,
            area_um2     = new_area_um2,
            aspect_ratio = passive.get("aspect_ratio", 4.0),
            allow_series  = passive.get("n_series",   1) >= 1,
            allow_parallel = passive.get("n_parallel", 1) >= 1,
        )

    if node_type == "mom_cap":
        return place_mom_cap(
            node,
            area_um2 = new_area_um2,
            layers   = passive.get("layers", ["M2", "M3", "M4"]),
        )

    if node_type == "mos_cap":
        # Scale nf proportionally; preserve width_um per finger
        old_area = passive.get("area_um2", 0.0)
        old_nf   = passive.get("nf", 1)
        if old_area > 0:
            new_nf = max(1, round(old_nf * (new_area_um2 / old_area)))
        else:
            # Fallback: derive nf from new area using transistor pitch
            new_nf = max(1, round(new_area_um2 /
                                  (_TRANSISTOR_PITCH_UM * _TRANSISTOR_HEIGHT_UM)))
        return place_mos_cap(
            node,
            nf       = new_nf,
            width_um = passive.get("width_um", 1.0),
        )

    return LayoutToolResult(
        success=False,
        message=(
            f"reshape_passive: unsupported type '{node_type}'. "
            f"Supported: resistor, mom_cap, mos_cap"
        ),
        changed=False,
        nodes=[node],
    )

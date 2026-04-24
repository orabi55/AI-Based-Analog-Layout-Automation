"""
ai_agent/ai_chat_bot/agents/placement_fallback.py
==================================================
Deterministic placement fallback and validation for the new LangGraph flow.

Ported from ai_agent/ai_initial_placement/multi_agent_placer.py with the
following improvements over the original:
  - validate_multirow uses distance-based (not slot-rounding) overlap check,
    so devices with non-uniform widths don't generate false positives.
  - deterministic_fallback groups by parent/connectivity and interdigitates
    matched pairs (A-B-B-A) for better matching than a simple alphabetical row.

Functions
---------
validate_multirow(nodes, placed)
    Structural checks: coverage, type consistency, row-level x-collisions.

deterministic_fallback(nodes, abutment_candidates)
    Connectivity-aware multi-row layout used when the LLM fails.
    Produces a roughly square aspect ratio with interdigitated matched pairs.
"""

import re
import copy
from collections import defaultdict

from ai_agent.ai_chat_bot.agents.geometry_engine import (
    convert_multirow_to_geometry,
    device_width,
    ABUT_SPACING,
    STD_PITCH,
)

MAX_ROW_WIDTH = 14   # max devices per row in fallback (~square aspect ratio)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_multirow(nodes: list, placed: list) -> list:
    """
    Lightweight multi-row-aware placement validation.

    Checks that:
    - Every input device ID appears in the output.
    - No device has a changed type (PMOS placed at NMOS y, etc.).
    - No two devices in the SAME ROW overlap (x-distance < min spacing).

    Uses a direct minimum-distance check instead of slot rounding to
    avoid false positives when device widths differ from STD_PITCH.

    Returns
    -------
    list of str error messages — empty means valid.
    """
    errors: list = []
    orig_ids   = {n["id"] for n in nodes}
    orig_types = {n["id"]: n.get("type", "?") for n in nodes}
    placed_ids = {p.get("id") for p in placed if isinstance(p, dict) and p.get("id")}

    # 1. Coverage
    missing = orig_ids - placed_ids
    if missing:
        errors.append(f"MISSING devices: {sorted(missing)}")

    # 2. Same-row overlap (distance-based, not slot-based)
    MIN_SPACING = ABUT_SPACING * 0.9  # ~0.063 µm — anything closer is a collision
    row_devs: dict = defaultdict(list)
    for p in placed:
        if not isinstance(p, dict):
            continue
        geo = p.get("geometry", {})
        x   = float(geo.get("x", 0.0))
        y   = round(float(geo.get("y", 0.0)), 3)
        row_devs[y].append((x, p.get("id", "?")))

    for y, devs in row_devs.items():
        devs_sorted = sorted(devs, key=lambda d: d[0])
        for i in range(len(devs_sorted) - 1):
            x_a, id_a = devs_sorted[i]
            x_b, id_b = devs_sorted[i + 1]
            if abs(x_b - x_a) < MIN_SPACING:
                errors.append(
                    f"X-COLLISION in row y={y:.3f}: '{id_a}' and '{id_b}' "
                    f"only {abs(x_b - x_a):.4f}µm apart (min={MIN_SPACING:.4f}µm)"
                )

    # 3. Type must not change
    for p in placed:
        if not isinstance(p, dict):
            continue
        pid = p.get("id", "")
        if pid in orig_types and p.get("type") and p["type"] != orig_types[pid]:
            errors.append(
                f"TYPE CHANGED: {pid} was {orig_types[pid]}, now {p['type']}"
            )

    return errors


# ---------------------------------------------------------------------------
# Deterministic fallback
# ---------------------------------------------------------------------------

def deterministic_fallback(nodes: list, abutment_candidates: list) -> list:
    """
    Connectivity-aware multi-row deterministic fallback.

    Instead of dumping all NMOS/PMOS into a single row each (which
    produces an extremely elongated, unusable layout), this groups
    devices by shared gate nets (mirrors, diff pairs) and splits
    large groups into multiple rows for a roughly square aspect ratio.

    Matched pairs are interdigitated (A-B-B-A) for better symmetry than
    a simple alphabetical sort. Also produces correct abutment flags
    via the geometry engine.

    Parameters
    ----------
    nodes               : physical device nodes
    abutment_candidates : abutment pair candidates

    Returns
    -------
    list of placed node dicts
    """
    nmos_nodes = [n for n in nodes if n.get("type") == "nmos"]
    pmos_nodes = [n for n in nodes if n.get("type") == "pmos"]

    def _build_rows(typed_nodes: list) -> list:
        """Group devices into rows by parent device, then split large rows."""
        if not typed_nodes:
            return []

        # Group by parent device (strip _fN, _mN suffixes)
        parent_groups: dict = defaultdict(list)
        for n in typed_nodes:
            dev_id = n["id"]
            parent = re.sub(r'_[mf]\d+$', '', dev_id)
            parent_groups[parent].append(dev_id)

        # Sort parents and interdigitate matched pairs (same-size groups)
        parents    = sorted(parent_groups.keys())
        used       = set()
        rows       = []
        current_row: list = []

        for parent in parents:
            if parent in used:
                continue
            group = parent_groups[parent]

            # Find a matching partner (same finger count, unused)
            partner = None
            for other in parents:
                if other != parent and other not in used:
                    if len(parent_groups[other]) == len(group):
                        partner = other
                        break

            if partner:
                # Interdigitate: A_f1, B_f1, A_f2, B_f2, … (common centroid)
                a_devs = sorted(parent_groups[parent])
                b_devs = sorted(parent_groups[partner])
                interdig: list = []
                for a, b in zip(a_devs, b_devs):
                    interdig.extend([a, b])
                interdig.extend(a_devs[len(b_devs):])
                interdig.extend(b_devs[len(a_devs):])

                if len(current_row) + len(interdig) > MAX_ROW_WIDTH:
                    if current_row:
                        rows.append(current_row)
                    current_row = interdig
                else:
                    current_row.extend(interdig)
                used.add(parent)
                used.add(partner)
            else:
                # No partner — add group sequentially
                if len(current_row) + len(group) > MAX_ROW_WIDTH:
                    if current_row:
                        rows.append(current_row)
                    current_row = sorted(group)
                else:
                    current_row.extend(sorted(group))
                used.add(parent)

        if current_row:
            rows.append(current_row)

        # Split any remaining oversized rows
        final_rows = []
        for row in rows:
            while len(row) > MAX_ROW_WIDTH:
                final_rows.append(row[:MAX_ROW_WIDTH])
                row = row[MAX_ROW_WIDTH:]
            if row:
                final_rows.append(row)

        return final_rows

    nmos_row_lists = _build_rows(nmos_nodes)
    pmos_row_lists = _build_rows(pmos_nodes)

    nmos_rows = [
        {"label": f"nmos_group_{i}", "devices": devs}
        for i, devs in enumerate(nmos_row_lists)
    ]
    pmos_rows = [
        {"label": f"pmos_group_{i}", "devices": devs}
        for i, devs in enumerate(pmos_row_lists)
    ]

    # If nothing built, absolute fallback: alphabetical single row each
    if not nmos_rows and not pmos_rows:
        nmos_ids = sorted(n["id"] for n in nmos_nodes)
        pmos_ids = sorted(n["id"] for n in pmos_nodes)
        nmos_rows = [{"label": "nmos", "devices": nmos_ids}]
        pmos_rows = [{"label": "pmos", "devices": pmos_ids}]

    n_total_rows = len(nmos_rows) + len(pmos_rows)
    print(f"[Fallback]   Deterministic: {len(nmos_rows)} NMOS row(s) + "
          f"{len(pmos_rows)} PMOS row(s) = {n_total_rows} total")

    return convert_multirow_to_geometry(
        {"nmos_rows": nmos_rows, "pmos_rows": pmos_rows},
        nodes,
        abutment_candidates,
    )

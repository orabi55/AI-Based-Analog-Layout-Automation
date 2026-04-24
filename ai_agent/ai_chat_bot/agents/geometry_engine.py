"""
ai_agent/ai_chat_bot/agents/geometry_engine.py
===============================================
Deterministic geometry engine for multi-row analog IC placement.

Ported from ai_agent/ai_initial_placement/multi_agent_placer.py and
enhanced with improvements from placer_utils.py.

Responsibilities
----------------
- device_width()              — compute physical device width from params
- place_row()                 — pack a single row left-to-right with correct spacing
- convert_multirow_to_geometry() — convert LLM {nmos_rows, pmos_rows} to x/y microns
  * Dynamic row pitch (based on actual device height, not hardcoded)
  * PMOS/NMOS type guarantee — PMOS always strictly above NMOS
  * Auto row-centering for symmetric layout
  * Orphan device recovery (catches any device the LLM missed)
  * Legacy 2-row fallback support
"""

import copy
import math
from collections import defaultdict

# ─── Physical layout constants ───────────────────────────────────────────────
ROW_PITCH    = 0.668   # µm — default row-to-row pitch (overridden dynamically)
ABUT_SPACING = 0.070   # µm — abutted finger pitch
STD_PITCH    = 0.294   # µm — non-abutted standard pitch
MAX_ROW_DEVS = 16      # max devices per row before auto-split


def device_width(node: dict) -> float:
    """
    Compute the physical width of a device from its geometry or electrical params.

    Priority:
      1. geometry.width  (if present and > 0)
      2. Computed from electrical: nf * STD_PITCH (finger-aware)
      3. Fallback: STD_PITCH

    Used for device-to-device spacing when not abutted.
    """
    geo = node.get("geometry", {})
    w = geo.get("width", 0)
    if w and float(w) > 0:
        return float(w)
    elec = node.get("electrical", {})
    nf = max(1, int(elec.get("nf", 1)))
    return round(nf * STD_PITCH, 6)


def build_abut_pairs(nodes: list, candidates: list) -> set:
    """
    Build a set of (dev_a, dev_b) abutment pairs from candidates and node flags.

    Parameters
    ----------
    nodes      : all device nodes (used to read embedded abutment flags)
    candidates : explicit abutment candidates from the layout JSON

    Returns
    -------
    set of (str, str) — directed pairs (a abuts right → b)
    """
    pairs: set = set()
    for c in (candidates or []):
        pairs.add((str(c.get("dev_a", "")), str(c.get("dev_b", ""))))
    for n in nodes:
        abut = n.get("abutment", {})
        nid  = str(n.get("id", ""))
        if abut.get("abut_right"):
            for m in nodes:
                mid = str(m.get("id", ""))
                if m.get("abutment", {}).get("abut_left") and mid != nid:
                    pairs.add((nid, mid))
    return pairs


def place_row(devices: list, row_y: float, node_map: dict, abut_pairs: set) -> list:
    """
    Pack a single row of devices left-to-right with correct spacing.

    Parameters
    ----------
    devices    : ordered device ID list for this row
    row_y      : Y coordinate for all devices in this row (µm)
    node_map   : {device_id: original_node_dict}
    abut_pairs : (dev_a, dev_b) pairs that must use ABUT_SPACING

    Returns
    -------
    list of placed node dicts (deep copies with geometry set)
    """
    placed = []
    cursor = 0.0
    for idx, dev_id in enumerate(devices):
        if dev_id not in node_map:
            print(f"[GeoEngine] WARNING: '{dev_id}' not in node_map — skipping")
            continue
        node = copy.deepcopy(node_map[dev_id])
        geo  = node.setdefault("geometry", {})
        geo["x"] = round(cursor, 6)
        geo["y"] = row_y
        geo.setdefault("orientation", "R0")

        # Abutment flags
        abut_left  = (idx > 0 and (devices[idx - 1], dev_id) in abut_pairs)
        abut_right = (idx < len(devices) - 1
                      and (dev_id, devices[idx + 1]) in abut_pairs)
        node["abutment"] = {"abut_left": abut_left, "abut_right": abut_right}

        # Advance cursor by the ACTUAL device width (not always STD_PITCH)
        if idx < len(devices) - 1:
            next_id = devices[idx + 1]
            if (dev_id, next_id) in abut_pairs:
                cursor = round(cursor + ABUT_SPACING, 6)
            else:
                cursor = round(cursor + device_width(node), 6)

        placed.append(node)
    return placed


def _split_rows(rows: list) -> list:
    """Auto-split oversized rows (>MAX_ROW_DEVS) into sub-rows."""
    split = []
    for row in rows:
        devs  = row.get("devices", [])
        label = row.get("label", "row")
        if len(devs) <= MAX_ROW_DEVS:
            split.append(row)
        else:
            chunk_idx = 0
            while devs:
                chunk = devs[:MAX_ROW_DEVS]
                devs  = devs[MAX_ROW_DEVS:]
                split.append({"label": f"{label}_sub{chunk_idx}", "devices": chunk})
                chunk_idx += 1
    return split


def convert_multirow_to_geometry(
    multirow_data: dict,
    original_nodes: list,
    abutment_candidates: list,
) -> list:
    """
    Convert multi-row LLM output to exact physical geometry.

    NMOS rows get y = 0, pitch, 2×pitch, …
    PMOS rows get y = n_nmos×pitch + gap, (n_nmos+1)×pitch + gap, …
    This guarantees min(PMOS y) > max(NMOS y) always.

    Also handles the legacy 2-row schema ``{nmos_order, pmos_order}``.

    Parameters
    ----------
    multirow_data        : LLM output (multi-row or legacy schema)
    original_nodes       : all device nodes (with metadata, electrical params)
    abutment_candidates  : abutment pair candidates from the layout JSON

    Returns
    -------
    list of placed node dicts with x/y geometry set
    """
    # ── Legacy 2-row fallback ─────────────────────────────────────────
    if "nmos_order" in multirow_data or "pmos_order" in multirow_data:
        nmos_order = multirow_data.get("nmos_order", [])
        pmos_order = multirow_data.get("pmos_order", [])
        return convert_multirow_to_geometry(
            {
                "nmos_rows": [{"label": "nmos", "devices": nmos_order}],
                "pmos_rows": [{"label": "pmos", "devices": pmos_order}],
            },
            original_nodes,
            abutment_candidates,
        )

    nmos_rows = multirow_data.get("nmos_rows", [])
    pmos_rows = multirow_data.get("pmos_rows", [])

    # ── Auto-split oversized rows ─────────────────────────────────────
    nmos_rows = _split_rows(nmos_rows)
    pmos_rows = _split_rows(pmos_rows)

    # ── Total fallback — alphabetical single-row each ─────────────────
    if not nmos_rows and not pmos_rows:
        nmos_ids = sorted(n["id"] for n in original_nodes if n.get("type") == "nmos")
        pmos_ids = sorted(n["id"] for n in original_nodes if n.get("type") == "pmos")
        return convert_multirow_to_geometry(
            {
                "nmos_rows": [{"label": "nmos", "devices": nmos_ids}],
                "pmos_rows": [{"label": "pmos", "devices": pmos_ids}],
            },
            original_nodes,
            abutment_candidates,
        )

    node_map   = {n["id"]: n for n in original_nodes if "id" in n}
    abut_pairs = build_abut_pairs(original_nodes, abutment_candidates)

    # ── Compute row pitch dynamically from actual device height ────────
    max_height = max(
        (float(n.get("geometry", {}).get("height", 0.5)) for n in original_nodes),
        default=0.5,
    )
    # Tight spacing: just enough clearance for routing channels (5% gap)
    row_pitch     = round(max(ROW_PITCH, max_height * 1.05), 3)
    # PMOS/NMOS gap must clear the full device height + routing channel
    # NMOS row 0 at y=0 extends to y=max_height, so PMOS must start above that
    pmos_nmos_routing = round(max_height * 0.30, 3)  # 30% routing channel
    pmos_nmos_gap     = round(max_height + pmos_nmos_routing, 3)

    print(f"[GeoEngine]   Row pitch: {row_pitch:.3f}um "
          f"(device height={max_height:.3f}um, gap={row_pitch - max_height:.3f}um)")
    print(f"[GeoEngine]   PMOS/NMOS gap: {pmos_nmos_gap:.3f}um "
          f"(device={max_height:.3f} + routing={pmos_nmos_routing:.3f})")

    n_nmos     = len(nmos_rows)
    n_pmos     = len(pmos_rows)

    # Y-axis layout: PMOS above NMOS
    #   NMOS rows: y = 0, -row_pitch, -2*row_pitch, ... (going down from 0)
    #   PMOS rows: y = gap, gap + row_pitch, ... (going up from gap)
    nmos_ys = [round(-i * row_pitch, 6) for i in range(n_nmos)]
    pmos_base = round(pmos_nmos_gap, 6)
    pmos_ys = [round(pmos_base + j * row_pitch, 6) for j in range(n_pmos)]

    placed_ids:   set  = set()
    placed_nodes: list = []

    # ── Place NMOS rows ───────────────────────────────────────────────
    for row_idx, row in enumerate(nmos_rows):
        y       = nmos_ys[row_idx]
        devices = [d for d in row.get("devices", []) if d not in placed_ids]
        label   = row.get("label", f"nmos_row_{row_idx}")
        print(f"[GeoEngine]   NMOS row {row_idx} [{label}]  y={y:.3f}  "
              f"{len(devices)} device(s)")
        row_nodes = place_row(devices, y, node_map, abut_pairs)
        placed_nodes.extend(row_nodes)
        placed_ids.update(n["id"] for n in row_nodes)

    # ── Place PMOS rows ───────────────────────────────────────────────
    for row_idx, row in enumerate(pmos_rows):
        y       = pmos_ys[row_idx]
        devices = [d for d in row.get("devices", []) if d not in placed_ids]
        label   = row.get("label", f"pmos_row_{row_idx}")
        print(f"[GeoEngine]   PMOS row {row_idx} [{label}]  y={y:.3f}  "
              f"{len(devices)} device(s)")
        row_nodes = place_row(devices, y, node_map, abut_pairs)
        placed_nodes.extend(row_nodes)
        placed_ids.update(n["id"] for n in row_nodes)

    # ── Orphan recovery — any device missed by the LLM ────────────────
    for n in original_nodes:
        nid = n.get("id", "")
        if nid in placed_ids:
            continue
        dev_type = str(n.get("type", "")).lower()
        if dev_type == "nmos":
            y = nmos_ys[-1] if nmos_ys else 0.0
        elif dev_type == "pmos":
            y = pmos_ys[-1] if pmos_ys else pmos_base
        else:
            # Passive (res/cap) — dedicated row above everything
            y = round(pmos_base + n_pmos * row_pitch + row_pitch, 6)

        # Find leftmost free x in that row
        used_x = {
            round(p["geometry"]["x"], 6)
            for p in placed_nodes
            if round(p.get("geometry", {}).get("y", -999), 6) == y
        }
        w = device_width(n)
        x = 0.0
        while round(x, 6) in used_x:
            x = round(x + w, 6)

        orphan = copy.deepcopy(n)
        geo    = orphan.setdefault("geometry", {})
        geo["x"] = round(x, 6)
        geo["y"] = y
        geo.setdefault("orientation", "R0")
        orphan["abutment"] = {"abut_left": False, "abut_right": False}
        placed_nodes.append(orphan)
        placed_ids.add(nid)
        print(f"[GeoEngine]   Orphan '{nid}' ({dev_type}) → ({x:.3f}, {y:.3f})")

    # ── Compute layout metrics ─────────────────────────────────────────
    row_nodes_by_y: dict = defaultdict(list)
    for p in placed_nodes:
        ry = round(float(p.get("geometry", {}).get("y", 0.0)), 6)
        row_nodes_by_y[ry].append(p)

    global_max_width = 0.0
    for ry, rnodes in row_nodes_by_y.items():
        if rnodes:
            rightmost = max(rnodes, key=lambda n: float(n["geometry"]["x"]))
            row_w = float(rightmost["geometry"]["x"]) + device_width(rightmost)
            global_max_width = max(global_max_width, row_w)

    total_height = (
        (pmos_ys[-1] if pmos_ys else 0)
        + max_height
        - (nmos_ys[0] if nmos_ys else 0)
    )
    aspect = global_max_width / total_height if total_height > 0 else 0
    print(f"[GeoEngine]   Layout: {global_max_width:.3f}um x {total_height:.3f}um "
          f"(aspect={aspect:.2f}, target~1.0)")

    return placed_nodes

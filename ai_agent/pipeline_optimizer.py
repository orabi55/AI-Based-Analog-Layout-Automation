"""
ai_agent/pipeline_optimizer.py
================================
Deterministic placement optimizer layer.

Responsibilities:
    1. Row-level cost-driven ordering (greedy)
    2. Deterministic symmetry enforcement for diff pairs
    3. Exclude dummy devices from optimization
    4. Interdigitated / common-centroid support for mirror groups

FIXES APPLIED:
    - Multi-row NMOS placement: does not collapse all NMOS to y=0
    - Uses needs_interdigitation + compute_mirror_placement (auto single/multi-row)
    - Builds logical devices with _fingers from physical finger nodes
    - _optimize_rows now preserves multi-row assignments
    - _y_in_correct_row updated in orchestrator to accept multiple NMOS rows
    - nf = layout finger count only (nfin is NOT multiplied)
"""

import copy
import re
from collections import defaultdict

from ai_agent.routing_previewer import score_routing
from ai_agent.finger_grouping import aggregate_to_logical_devices
from ai_agent.placement_specialist import (
    compute_mirror_placement,
    needs_interdigitation,
    placements_to_cmd_blocks,
    PITCH_UM,
    ROW_HEIGHT_UM,
    NMOS_ROW_0_Y,
    MAX_FINGERS_PER_ROW,
)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def _log(msg: str) -> None:
    print(f"[OPT] {msg}")


# ---------------------------------------------------------------------------
# Public Entry
# ---------------------------------------------------------------------------
def apply_deterministic_optimizations(
    working_nodes,
    constraint_text,
    terminal_nets,
    edges,
    gap_px=0.0,
    pitch=PITCH_UM,
    placement_mode="auto",
):
    """
    Apply deterministic row optimization and symmetry enforcement.

    Steps:
      1. Detect if any NMOS mirror group needs interdigitated placement
      2. If yes: apply compute_mirror_placement() (auto single/multi-row)
      3. Optimize remaining rows with greedy cost minimization
      4. Enforce symmetry for diff-pairs

    Returns a NEW node list (does not mutate original).
    """
    if not working_nodes:
        return working_nodes

    nodes = copy.deepcopy(working_nodes)

    # Step 1: Try interdigitated / common-centroid for NMOS mirror groups
    # Returns updated nodes + set of finger IDs that were interdigitated
    nodes, interdigitated_ids = _apply_interdigitation_if_needed(
        nodes,
        constraint_text,
        terminal_nets,
        pitch,
        placement_mode,
    )

    # Step 2: Optimize remaining rows (skip interdigitated devices)
    nodes = _optimize_rows(nodes, terminal_nets, edges, pitch, interdigitated_ids)

    # Step 3: Enforce symmetry for diff-pairs
    nodes = _enforce_symmetry(nodes, constraint_text, pitch)

    return nodes


# ---------------------------------------------------------------------------
# Interdigitation / Common-Centroid Application
# ---------------------------------------------------------------------------
def _apply_interdigitation_if_needed(
    nodes,
    constraint_text,
    terminal_nets,
    pitch,
    placement_mode="auto",
):
    """
    Detect NMOS mirror groups from constraint_text and apply
    interdigitated or common-centroid placement as needed.

    Key fix: nodes from JSON are physical fingers (MM0_f1, MM0_f2, ...)
    with nf=1 each. We must:
      1. Use aggregate_to_logical_devices() to group them into logical
         devices (MM0 with _fingers=[MM0_f1, ..., MM0_f8])
      2. Call needs_interdigitation() to decide if placement is needed
      3. Call compute_mirror_placement() which auto-selects single-row
         interdigitated or multi-row common-centroid

    Modifies node positions in-place.
    Returns updated nodes.
    """
    if not constraint_text or "MIRROR" not in constraint_text.upper():
        return nodes, set()

    # Parse mirror groups from constraint text
    mirror_groups = _parse_mirror_groups(constraint_text, nodes)

    if not mirror_groups:
        _log("No NMOS mirror groups found in constraints")
        return nodes, set()

    # ── Build logical devices from physical finger nodes ─────────────
    # The JSON has physical fingers: MM0_f1, MM0_f2, ..., MM0_f8
    # aggregate_to_logical_devices groups them into:
    #   MM0 with _fingers=[MM0_f1, ..., MM0_f8]
    logical_devices = aggregate_to_logical_devices(nodes)
    logical_id_map  = {ld["id"]: ld for ld in logical_devices}

    _log(
        f"Built {len(logical_devices)} logical devices from "
        f"{len(nodes)} physical nodes: "
        f"{[ld['id'] + '(nf=' + str(len(ld.get('_fingers', [ld['id']]))) + ')' for ld in logical_devices]}"
    )

    # ── Also try to get SPICE nf for accurate finger counts ──────────
    # terminal_nets may be passed as spice_nets from the orchestrator
    spice_nets = terminal_nets or {}

    # Track which finger IDs have been interdigitated
    interdigitated_ids = set()

    for gate_net, group_info in mirror_groups.items():
        dev_ids  = group_info["dev_ids"]
        dev_type = group_info["dev_type"]

        if dev_type.lower() != "nmos":
            _log(f"Skipping PMOS mirror (gate={gate_net}) - not implemented")
            continue

        # Find matching logical devices for this mirror group
        mirror_devs = []
        for dev_id in dev_ids:
            if dev_id in logical_id_map:
                mirror_devs.append(logical_id_map[dev_id])
            else:
                _log(f"  WARNING: logical device {dev_id!r} not found")

        if len(mirror_devs) < 2:
            _log(
                f"Mirror group {dev_ids} — found only "
                f"{len(mirror_devs)} logical devices, need >= 2"
            )
            continue

        # Log what we found
        for md in mirror_devs:
            fingers = md.get("_fingers", [md["id"]])
            _log(f"  {md['id']}: {len(fingers)} fingers = {fingers}")

        # Check if interdigitation is needed
        if not needs_interdigitation(mirror_devs, spice_nets, MAX_FINGERS_PER_ROW):
            _log(
                f"Mirror group {dev_ids} — "
                f"interdigitation not needed (< 4 fingers, exact match)"
            )
            continue

        # Compute interdigitated / common-centroid placement
        _log(
            f"Applying interdigitated placement for NMOS mirror "
            f"(gate={gate_net}): {dev_ids}"
        )

        placements = compute_mirror_placement(
            mirror_logical_devices=mirror_devs,
            spice_nets=spice_nets,
            pitch=pitch,
            max_fingers_per_row=MAX_FINGERS_PER_ROW,
            start_x=0.0,
            nmos_row_0_y=NMOS_ROW_0_Y,
            row_height=ROW_HEIGHT_UM,
            force_mode=placement_mode,
        )

        if not placements:
            _log(f"  compute_mirror_placement returned empty list")
            continue

        # Apply placements to physical finger nodes
        finger_id_map = {n["id"]: n for n in nodes}
        applied = 0
        for p in placements:
            finger_id = p["finger_id"]
            if finger_id in finger_id_map:
                finger_id_map[finger_id]["geometry"]["x"] = p["x"]
                finger_id_map[finger_id]["geometry"]["y"] = p["y"]
                finger_id_map[finger_id]["geometry"]["orientation"] = (
                    p["orientation"]
                )
                applied += 1
            else:
                _log(f"  WARNING: finger {finger_id!r} not found in nodes")

        num_rows = max(p["row_idx"] for p in placements) + 1 if placements else 1
        _log(
            f"  Applied {applied}/{len(placements)} finger placements "
            f"across {num_rows} row(s)"
        )

        # Record which finger IDs were interdigitated
        for p in placements:
            interdigitated_ids.add(p["finger_id"])

    return nodes, interdigitated_ids


def _parse_mirror_groups(constraint_text, nodes):
    """
    Parse mirror groups from topology constraint text.

    Looks for lines matching:
      MIRROR (NMOS, gate=X): MM2(nf=8) <-> MM1(nf=8) <-> MM0[REF](nf=16)
      MIRROR (PMOS, gate=X): ...

    Returns:
        dict: {gate_net: {dev_ids: [...], dev_type: str}}
    """
    groups = {}

    # Pattern: MIRROR (TYPE, gate=NET): DEV1 <-> DEV2 <-> ...
    mirror_line_re = re.compile(
        r'MIRROR\s*\(\s*(\w+)\s*,\s*gate=(\w+)\s*\)\s*:\s*(.+)',
        re.IGNORECASE
    )
    # Pattern to extract device IDs: MM0[REF](nf=16) or MM2(nf=8)
    dev_id_re = re.compile(r'(\w+)(?:$$REF$$)?(?:\(nf=\d+\))?')

    for line in constraint_text.splitlines():
        line = line.strip()
        m    = mirror_line_re.search(line)
        if not m:
            continue

        dev_type = m.group(1).strip()   # NMOS or PMOS
        gate_net = m.group(2).strip()   # C or NBIAS etc.
        devs_str = m.group(3).strip()   # MM2(nf=8) <-> MM1(nf=8) <-> ...

        # Extract device IDs from the devices string
        dev_ids = []
        for part in devs_str.split("<->"):
            part = part.strip()
            dm   = dev_id_re.match(part)
            if dm:
                dev_ids.append(dm.group(1))

        if len(dev_ids) >= 2:
            groups[gate_net] = {
                "dev_ids":  dev_ids,
                "dev_type": dev_type,
            }
            _log(
                f"Parsed mirror group: gate={gate_net} "
                f"type={dev_type} devs={dev_ids}"
            )

    return groups


# ---------------------------------------------------------------------------
# Row Optimization
# ---------------------------------------------------------------------------
def _optimize_rows(nodes, terminal_nets, edges, pitch, skip_ids=None):
    """
    Greedy cost-minimizing row optimizer.

    Groups nodes by y-coordinate (row).
    For each row, tries all starting seeds and builds greedy order.
    Applies the order that gives lowest routing cost.

    Excludes dummy devices from ordering (they go to edges).
    Excludes interdigitated devices (skip_ids) — their order is locked.
    Does NOT change y-coordinates — preserves multi-row assignments.
    """
    skip_ids = skip_ids or set()
    # Group non-dummy nodes by y-coordinate
    rows = defaultdict(list)
    for n in nodes:
        if not n.get("is_dummy") and n["id"] not in skip_ids:
            y = round(float(n["geometry"]["y"]), 4)
            rows[y].append(n)

    for y_val, row_nodes in rows.items():
        if len(row_nodes) <= 2:
            # Not enough devices to meaningfully optimize
            continue

        row_ids = [n["id"] for n in row_nodes]

        # Evaluate baseline cost
        _apply_row_order(nodes, y_val, row_ids, pitch)
        best_cost  = score_routing(
            nodes, edges, terminal_nets
        )["placement_cost"]
        best_order = row_ids.copy()

        for seed_id in row_ids:
            remaining = [r for r in row_ids if r != seed_id]
            order     = [seed_id]

            while remaining:
                best_candidate      = None
                best_candidate_cost = float("inf")

                for cand in remaining:
                    trial_order = order + [cand]
                    _apply_row_order(nodes, y_val, trial_order, pitch)
                    cost = score_routing(
                        nodes, edges, terminal_nets
                    )["placement_cost"]

                    if cost < best_candidate_cost:
                        best_candidate_cost = cost
                        best_candidate      = cand

                if best_candidate is not None:
                    order.append(best_candidate)
                    remaining.remove(best_candidate)
                else:
                    break

            if not remaining:
                _apply_row_order(nodes, y_val, order, pitch)
                final_cost = score_routing(
                    nodes, edges, terminal_nets
                )["placement_cost"]

                if final_cost < best_cost:
                    best_cost  = final_cost
                    best_order = order.copy()

        # Apply best order permanently for this row
        _apply_row_order(nodes, y_val, best_order, pitch)
        _log(
            f"Row y={y_val:.3f}: optimized {len(best_order)} devices, "
            f"cost={best_cost:.4f}"
        )

    return nodes


def _apply_row_order(nodes, y_val, ordered_ids, pitch):
    """
    Assign consecutive X positions to ordered_ids in row y_val.
    Does NOT change Y coordinates.
    """
    x_start = 0.0
    id_map  = {n["id"]: n for n in nodes}

    for i, dev_id in enumerate(ordered_ids):
        if dev_id in id_map:
            id_map[dev_id]["geometry"]["x"] = round(x_start + i * pitch, 6)


# ---------------------------------------------------------------------------
# Symmetry Enforcement
# ---------------------------------------------------------------------------
def _enforce_symmetry(nodes, constraint_text, pitch):
    """
    Detect diff-pair patterns from topology constraints and
    enforce symmetric placement about row midpoint.

    Only affects rows with exactly one diff-pair (2 devices).
    Does NOT change Y coordinates.
    """
    if not constraint_text:
        return nodes

    id_map = {n["id"]: n for n in nodes}

    # Extract diff-pair candidates using arrow notation
    pairs = []
    for line in constraint_text.splitlines():
        if "DIFF" in line.upper():
            matches = re.findall(r'(\w+)\s*[<\-]{2,3}\s*(\w+)', line)
            pairs.extend(matches)

    if not pairs:
        return nodes

    # Compute row bounds
    row_bounds = defaultdict(
        lambda: {"min": float("inf"), "max": -float("inf")}
    )
    for n in nodes:
        y = round(float(n["geometry"]["y"]), 4)
        x = float(n["geometry"]["x"])
        row_bounds[y]["min"] = min(row_bounds[y]["min"], x)
        row_bounds[y]["max"] = max(row_bounds[y]["max"], x)

    row_midpoints = {
        y: (b["min"] + b["max"]) / 2.0
        for y, b in row_bounds.items()
    }

    for a, b in pairs:
        if a in id_map and b in id_map:
            node_a = id_map[a]
            node_b = id_map[b]

            # Only enforce if both in same row
            y_a = round(float(node_a["geometry"]["y"]), 4)
            y_b = round(float(node_b["geometry"]["y"]), 4)

            if abs(y_a - y_b) > 0.001:
                _log(
                    f"Skipping symmetry for {a}/{b}: "
                    f"different rows (y={y_a}, y={y_b})"
                )
                continue

            center = row_midpoints.get(y_a, 0.0)
            node_a["geometry"]["x"] = round(center - pitch, 6)
            node_b["geometry"]["x"] = round(center + pitch, 6)

            _log(
                f"Symmetry enforced: {a} @ x={center - pitch:.3f}, "
                f"{b} @ x={center + pitch:.3f} "
                f"(center={center:.3f}, row y={y_a:.3f})"
            )

    return nodes
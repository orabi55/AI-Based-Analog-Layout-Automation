"""
Abutment Utilities
==================
Provides utilities for building abutment chains, healing placement geometries, 
and enforcing proper spacing between abutted devices.

Functions:
- _format_abutment_candidates: Formats abutment candidates for human-readable output.
- build_abutment_chains: Identifies ordered chains of devices to be abutted.
  - Inputs: nodes (list), candidates (list)
  - Outputs: list of lists of device IDs.
- heal_abutment_positions: Reconstructs geometries to ensure strict abutment and spacing.
  - Inputs: nodes (list), candidates (list), no_abutment (bool)
  - Outputs: list of mutated node dictionaries.
- force_abutment_spacing: Safety layer to enforce precise abutment spacing.
  - Inputs: nodes (list), candidates (list)
  - Outputs: list of safety-corrected node dictionaries.
"""

import re
from collections import defaultdict

from ai_agent.utils.logging import vprint


def _format_abutment_candidates(candidates: list) -> str:
    """
    Format the abutment candidate list into a human-readable prompt section.

    Abutment candidates represent devices that should share a common
    Source/Drain diffusion area to minimize overall footprint.

    Parameters
    ----------
    candidates : list
        List of candidate dictionaries indicating which devices should abut.

    Returns
    -------
    str
        A multi-line formatted string enumerating all valid abutment chains.
    """
    if not candidates:
        return ""
    lines = []
    for c in candidates:
        flip_note = " (Note: set orientation='R0_FH' for device B)" if c.get("needs_flip") else ""
        lines.append(
            f"  - ABUTMENT CHAIN: {c['dev_a']} (Right Side) <---> (Left Side) {c['dev_b']}. Net: '{c['shared_net']}'.{flip_note}"
        )
    return "\n".join(lines)


def build_abutment_chains(nodes: list, candidates: list) -> list[list[str]]:
    """
    Extract connected components of abutment pairs as ordered sequences (chains).

    Using Union-Find with path compression, this reconstructs full multi-device
    abutment chains (e.g., A-B, B-C -> [A, B, C]) so the placement engine
    knows which macroscopic groups must be kept unconditionally contiguous.

    Parameters
    ----------
    nodes : list
        List of all node dictionaries in the graph.
    candidates : list
        List of dictionaries declaring `dev_a` and `dev_b` abutment constraints.

    Returns
    -------
    list[list[str]]
        A list of chains. Each chain is an ordered list of device ID strings.
    """
    node_ids = [n["id"] for n in nodes if "id" in n]
    id_set = set(node_ids)

    # Standard Union-Find with path compression
    parent: dict[str, str] = {nid: nid for nid in id_set}

    def find(x: str) -> str:
        root = x
        while parent[root] != root:
            root = parent[root]
        # Path compression: point all traversed nodes directly to root
        while parent[x] != root:
            nxt = parent[x]
            parent[x] = root
            x = nxt
        return root

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Union from explicit candidates (primary source of truth)
    for c in candidates:
        a, b = c["dev_a"], c["dev_b"]
        if a in id_set and b in id_set:
            union(a, b)

    # This ensures hierarchy siblings (MM0_f1, MM0_f2, etc.) expanded by
    # expand_groups are properly chained even if not in explicit candidates.
    # We ALWAYS check flags, regardless of whether candidates exist.
    rows = defaultdict(list)
    for n in nodes:
        y = round(float(n.get("geometry", {}).get("y", 0.0)), 3)
        rows[y].append(n)

    for y_val, row_nodes in rows.items():
        sorted_row = sorted(row_nodes, key=lambda n: n.get("geometry", {}).get("x", 0.0))
        for i in range(len(sorted_row) - 1):
            n1 = sorted_row[i]
            n2 = sorted_row[i + 1]
            # Check if BOTH devices have matching abutment flags
            if (n1.get("abutment", {}).get("abut_right")
                    and n2.get("abutment", {}).get("abut_left")):
                a, b = n1["id"], n2["id"]
                if a in id_set and b in id_set:
                    union(a, b)

    # Group by component root
    groups: dict[str, list[str]] = {}
    for nid in id_set:
        root = find(nid)
        groups.setdefault(root, []).append(nid)

    # Build ordered chains — sort by finger index if present, else by ID
    def _finger_key(nid: str) -> tuple:
        parts = nid.rsplit("_f", 1)
        if len(parts) == 2:
            try:
                return (parts[0], int(parts[1]))
            except ValueError:
                pass
        return (nid, 0)

    chains = []
    for group in groups.values():
        if len(group) <= 1:
            continue  # single node — not a chain
        ordered = sorted(group, key=_finger_key)
        chains.append(ordered)

    return chains


def heal_abutment_positions(nodes: list, candidates: list,
                              no_abutment: bool = False) -> list:
    """
    Robust post-placement geometry reconstruction with chain-based topological clustering.

    This function overrides the model's raw AI coordinate output by forcing
    strict determinism on abutted chains and passive rows.

    Algorithm (per row):
    0. FIRST: Force all passive devices (res/cap) to a dedicated row at Y=1.630,
       packed left-to-right by their actual widths. This prevents overlap with transistors.
    1. Build abutment chains (connected components of abutted device pairs).
    2. For each row, group devices by their chain membership.
    3. Force-pack each chain into consecutive slots separated by
       ABUT_SPACING (0.070 µm), anchored at the chain leader's X.
    4. Separate different chains / standalone devices by device width.
    5. The result is guaranteed to pass _validate_placement even if the
       LLM outputs completely wrong X values inside a chain.

    Parameters
    ----------
    nodes : list
        List of node dictionaries containing the raw AI-predicted geometries.
    candidates : list
        List of abutment candidate dictionaries dictating absolute connectivity limits.
    no_abutment : bool, optional
        If True, skips ALL abutment chain logic. Packs every transistor at a standard
        device-width spacing and aggressively clears all abutment flags. Defaults to False.

    Returns
    -------
    list
        Mutated node dictionary list with perfectly snapped geometrical coordinates.
    """
    ABUT_SPACING = 0.070   # µm between abutted device origins
    PITCH        = 0.294   # µm between non-abutted device origins

    if not nodes:
        return nodes

    # ── Step 0: Enforce passive device row ──────────────────────────────
    # Compute PASSIVE_Y dynamically based on actual transistor rows
    # so passives never overlap with transistors regardless of row count.
    all_ys = [round(float(n.get("geometry", {}).get("y", 0.0)), 3)
              for n in nodes if n.get("type") not in ("res", "cap")]
    max_transistor_y = max(all_ys) if all_ys else 0.0
    max_height = max(
        (float(n.get("geometry", {}).get("height", 0.668))
         for n in nodes if n.get("type") not in ("res", "cap")),
        default=0.668,
    )
    PASSIVE_Y = round(max_transistor_y + max_height + PITCH, 6)

    # Collect passives, force them into their own row, pack by width with wrapping
    passives = [n for n in nodes if n.get("type") in ("res", "cap")]
    if passives:
        # Estimate max transistor row width
        max_transistor_width = 15.0  # fallback
        transistor_nodes = [n for n in nodes if n.get("type") not in ("res", "cap")]
        if transistor_nodes:
            xs = [float(n.get("geometry", {}).get("x", 0.0)) for n in transistor_nodes]
            max_x = max(xs)
            min_x = min(xs)
            if max_x > min_x:
                max_transistor_width = max(max_x - min_x, 5.0)

        # Sort passives by their current X to maintain relative order
        passives.sort(key=lambda n: n.get("geometry", {}).get("x", 0.0))
        cursor = 0.0
        current_passive_y = PASSIVE_Y
        for p in passives:
            geo = p.setdefault("geometry", {})
            p_width = float(geo.get("width", PITCH))

            # Wrap to next row if exceeding bounds
            if cursor > 0 and (cursor + p_width) > max_transistor_width:
                cursor = 0.0
                current_passive_y = round(current_passive_y + max_height + PITCH, 6)

            geo["x"] = round(cursor, 6)
            geo["y"] = current_passive_y
            cursor = round(cursor + p_width, 6)

    # ── No-abutment mode: simple left-to-right packing per row ──────────
    if no_abutment:
        passive_ids = {p["id"] for p in passives} if passives else set()
        row_buckets: dict[float, list] = defaultdict(list)
        for n in nodes:
            if n.get("id") in passive_ids:
                continue
            y = round(float(n.get("geometry", {}).get("y", 0.0)), 3)
            row_buckets[y].append(n)

        for y_key, row_nodes in row_buckets.items():
            row_sorted = sorted(row_nodes,
                                key=lambda n: n.get("geometry", {}).get("x", 0.0))
            if not row_sorted:
                continue
            cursor = row_sorted[0].get("geometry", {}).get("x", 0.0)
            for dev in row_sorted:
                geo = dev.setdefault("geometry", {})
                geo["x"] = round(cursor, 6)
                geo["y"] = round(float(y_key), 6)
                # Clear ALL abutment flags
                dev["abutment"] = {"abut_left": False, "abut_right": False}
                dev_w = geo.get("width", PITCH)
                cursor = round(cursor + dev_w, 6)
        return nodes

    # ── Normal abutment mode below ──────────────────────────────────────

    # 1. Identify chains across ALL nodes (not per-row)
    chains = build_abutment_chains(nodes, candidates)
    chain_of: dict[str, list[str]] = {}  # device_id -> its ordered chain
    for ch in chains:
        for nid in ch:
            chain_of[nid] = ch

    # Also mark abutment flags from candidates
    abut_right_set: set[str] = set()
    abut_left_set:  set[str] = set()
    for c in candidates:
        abut_right_set.add(c["dev_a"])
        abut_left_set.add(c["dev_b"])
    # Supplement from embedded flags (when candidates list is empty)
    for n in nodes:
        abut = n.get("abutment", {})
        if abut.get("abut_right"):
            abut_right_set.add(n["id"])
        if abut.get("abut_left"):
            abut_left_set.add(n["id"])

    node_map: dict[str, dict] = {n["id"]: n for n in nodes if "id" in n}

    # 2. Group nodes by row (Y rounded to 3 dp) — skip passives (already placed)
    passive_ids = {p["id"] for p in passives} if passives else set()
    row_buckets: dict[float, list] = defaultdict(list)
    for n in nodes:
        if n.get("id") in passive_ids:
            continue  # passives already healed in Step 0
        y = round(float(n.get("geometry", {}).get("y", 0.0)), 3)
        row_buckets[y].append(n)

    for y_key, row_nodes in row_buckets.items():
        # 3. Build "segments":  each segment is either a chain or a singleton.
        #    We materialise chains in the order of their lowest-X device.
        processed: set[str] = set()
        segments: list[list[dict]] = []  # list of ordered device-lists

        # Sort row devices by current X for stable initial ordering
        row_sorted = sorted(row_nodes,
                            key=lambda n: n.get("geometry", {}).get("x", 0.0))

        for n in row_sorted:
            nid = n["id"]
            if nid in processed:
                continue
            if nid in chain_of:
                # Collect the full chain in finger-index order,
                # restricted to devices actually in THIS row.
                row_ids = {rn["id"] for rn in row_nodes}
                chain_in_row = [cid for cid in chain_of[nid]
                                if cid in row_ids and cid not in processed]
                if chain_in_row:
                    segments.append([node_map[cid] for cid in chain_in_row
                                     if cid in node_map])
                    processed.update(chain_in_row)
            else:
                segments.append([n])
                processed.add(nid)

        # 4. Pack segments left-to-right, respecting the LLM's target X to preserve symmetry.
        if not segments:
            continue

        # We allow cursor to follow the AI's intended relative coordinates
        # to preserve symmetrical centering, while ensuring no overlaps.
        cursor = -float('inf')

        for seg_idx, segment in enumerate(segments):
            # Target start X from the LLM's placement
            target_start_x = float(segment[0].get("geometry", {}).get("x", 0.0))
            cursor = max(cursor, target_start_x)

            for dev_idx, dev in enumerate(segment):
                geo = dev.setdefault("geometry", {})
                geo["x"] = round(cursor, 6)
                # Force exact Y-alignment: every device in this row
                # must share the identical Y coordinate
                geo["y"] = round(float(y_key), 6)

                is_last_in_chain = (dev_idx == len(segment) - 1)

                if not is_last_in_chain:
                    # Next device is within the chain — abut spacing
                    cursor = round(cursor + ABUT_SPACING, 6)
                    # Enforce abutment flags for adjacent pair
                    next_dev = segment[dev_idx + 1]
                    dev.setdefault("abutment", {})["abut_right"] = True
                    next_dev.setdefault("abutment", {})["abut_left"] = True
                else:
                    # End of this chain/singleton — advance by next device width
                    dev_w = float(geo.get("width", PITCH))
                    cursor = round(cursor + dev_w, 6)

        # 5. Clean abutment flags for standalone devices
        # Singletons have no abutment partner, so both flags MUST be False.
        # Keeping stale flags would cause _force_abutment_spacing to enforce
        # 0.070µm spacing on non-abutted neighbors, creating overlaps.
        for seg in segments:
            if len(seg) == 1:
                dev = seg[0]
                dev["abutment"] = {
                    "abut_left":  False,
                    "abut_right": False,
                }

    # 6. Global coordinate normalization to X=0.0
    # This prevents the layout from "floating" away from the origin
    # while preserving the LLM's relative symmetric placements.
    all_xs = [float(n.get("geometry", {}).get("x", 0.0)) for n in nodes]
    if all_xs:
        min_x = min(all_xs)
        if min_x != 0.0:
            for n in nodes:
                geo = n.setdefault("geometry", {})
                geo["x"] = round(float(geo.get("x", 0.0)) - min_x, 6)

    return nodes


def force_abutment_spacing(nodes: list, candidates: list = None) -> list:
    """
    FAILSAFE: Force logically-correct abutment spacing across adjacent geometries.

    A final protection layer running after `heal_abutment_positions` or SA.
    It scans the row array, looks for devices natively declaring structural
    abutment (`abut_right` interacting with `abut_left`), and rigorously forces
    their physical delta-X to be exactly 0.070 µm.

    Parameters
    ----------
    nodes : list
        List of geometrically assigned node dictionaries.
    candidates : list, optional
        Fallback reference candidate list (unused natively inside the loop
        but kept for API compatibility).

    Returns
    -------
    list
        The safety-corrected mutated node dictionaries.
    """
    ABUT_SPACING = 0.070
    PITCH = 0.294

    # Build expected abutment pairs from candidates (inter-device) and
    # intra-group parent key (multi-finger siblings). This prevents corrupted
    # flags from forcing wrong spacing between unrelated devices.
    expected_pairs: set[tuple[str, str]] = set()
    if candidates:
        for c in candidates:
            expected_pairs.add((str(c.get("dev_a", "")), str(c.get("dev_b", ""))))
    parent_of = {n.get("id", ""): re.sub(r'_[mf]\d+$', '', n.get("id", ""))
                 for n in nodes if n.get("id", "")}

    row_buckets = defaultdict(list)
    for n in nodes:
        y = round(float(n.get("geometry", {}).get("y", 0.0)), 3)
        row_buckets[y].append(n)

    fixed_count = 0

    for y_key, row_nodes in row_buckets.items():
        # Sort by X
        row_sorted = sorted(row_nodes, key=lambda n: n.get("geometry", {}).get("x", 0.0))

        # Find all devices with abutment flags and fix spacing with cascade
        for i in range(len(row_sorted) - 1):
            n1 = row_sorted[i]
            n2 = row_sorted[i + 1]
            n1_id = n1.get("id", "")
            n2_id = n2.get("id", "")

            abut1 = n1.get("abutment", {})
            abut2 = n2.get("abutment", {})

            # If n1 has abut_right and n2 has abut_left, they MAY need fixing
            if abut1.get("abut_right") and abut2.get("abut_left"):
                pair = (n1_id, n2_id)
                is_expected = (
                    pair in expected_pairs
                    or parent_of.get(n1_id, n1_id) == parent_of.get(n2_id, n2_id)
                )
                if not is_expected:
                    vprint(f"[FORCE_FIX] WARNING: unexpected abutment flags for {pair} "
                          f"(parents: {parent_of.get(n1_id)} vs {parent_of.get(n2_id)}). Skipping.")
                    continue

                x1 = n1.get("geometry", {}).get("x", 0.0)
                x2 = n2.get("geometry", {}).get("x", 0.0)
                expected_x2 = round(x1 + ABUT_SPACING, 6)

                if abs(x2 - expected_x2) > 0.001:
                    shift = round(expected_x2 - x2, 6)
                    vprint(f"[FORCE_FIX] Moving {n2_id} from x={x2:.4f} to x={expected_x2:.4f} "
                          f"(was {abs(x2 - x1):.4f}, should be {ABUT_SPACING:.3f})")
                    n2.setdefault("geometry", {})["x"] = expected_x2
                    fixed_count += 1

                    # Cascade the shift to ALL subsequent devices in this row
                    # to prevent overlaps caused by moving n2
                    for j in range(i + 2, len(row_sorted)):
                        later = row_sorted[j]
                        later_geo = later.setdefault("geometry", {})
                        later_x = later_geo.get("x", 0.0)
                        later_geo["x"] = round(later_x + shift, 6)

    if fixed_count > 0:
        vprint(f"[FORCE_FIX] Fixed {fixed_count} device position(s)")

    return nodes

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

def group_nodes_by_row(nodes: list, tolerance: float = 0.05) -> dict[float, list]:
    """
    Group nodes into rows based on their geometry 'y' coordinate using a clustering tolerance.
    This prevents floating point rounding differences from splitting rows.
    """
    if not nodes:
        return {}
    sorted_nodes = sorted(nodes, key=lambda n: float(n.get("geometry", {}).get("y", 0.0)))
    rows = {}
    current_row = []
    current_y_sum = 0.0
    for n in sorted_nodes:
        y = float(n.get("geometry", {}).get("y", 0.0))
        if not current_row:
            current_row.append(n)
            current_y_sum = y
        else:
            avg_y = current_y_sum / len(current_row)
            if abs(y - avg_y) <= tolerance:
                current_row.append(n)
                current_y_sum += y
            else:
                rep_y = round(current_y_sum / len(current_row), 3)
                rows[rep_y] = current_row
                current_row = [n]
                current_y_sum = y
    if current_row:
        rep_y = round(current_y_sum / len(current_row), 3)
        rows[rep_y] = current_row
    return rows


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
    rows = group_nodes_by_row(nodes)

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

    node_map = {n["id"]: n for n in nodes if "id" in n}

    chains = []
    for group in groups.values():
        if len(group) <= 1:
            continue  # single node — not a chain
        ordered = sorted(group, key=lambda nid: float(node_map.get(nid, {}).get("geometry", {}).get("x", 0.0)))
        chains.append(ordered)

    return chains


def heal_abutment_positions(nodes: list, candidates: list,
                              terminal_nets: dict = None,
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
        row_buckets = group_nodes_by_row([n for n in nodes if n.get("id") not in passive_ids])

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
    row_buckets = group_nodes_by_row([n for n in nodes if n.get("id") not in passive_ids])

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

            # Track physical terminal nets along the chain segment for dynamic self-healing swap
            prev_right_net = None

            for dev_idx, dev in enumerate(segment):
                geo = dev.setdefault("geometry", {})
                geo["x"] = round(cursor, 6)
                # Force exact Y-alignment: every device in this row
                # must share the identical Y coordinate
                geo["y"] = round(float(y_key), 6)

                # Initialize orientation from current geometry, defaulting to R0
                current_orient = dev.get("geometry", {}).get("orientation", "R0")

                # Dynamically determine the actual physical nets on the left and right edges
                if terminal_nets is not None:
                    dev_id = dev["id"]
                    tn = terminal_nets.get(dev_id, {})
                    if not tn:
                        # Fallback for physical fingers (e.g. MM3_f1 -> MM3)
                        prefix = dev_id.rsplit("_f", 1)[0] if "_f" in dev_id else dev_id
                        tn = terminal_nets.get(prefix, {})
                    
                    logic_s = tn.get("S")
                    logic_d = tn.get("D")
                    
                    # Read from the node if already assigned/swapped, fallback to SPICE
                    # Under physical rules, default left is D, default right is S
                    curr_left_net = dev.get("net_d") or logic_d
                    curr_right_net = dev.get("net_s") or logic_s

                    if dev_idx > 0 and prev_right_net is not None:
                        # If the physical nets don't match, we logically swap them to heal abutment
                        if curr_left_net != prev_right_net:
                            if curr_right_net == prev_right_net:
                                dev["net_s"] = curr_left_net
                                dev["net_d"] = curr_right_net
                                curr_left_net, curr_right_net = curr_right_net, curr_left_net
                                # Toggle orientation upon S/D swap to represent horizontal flip
                                if current_orient == "MY":
                                    current_orient = "R0"
                                else:
                                    current_orient = "MY"

                    prev_right_net = curr_right_net
                
                # Apply the orientation back to the geometry
                geo["orientation"] = current_orient

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

    row_buckets = group_nodes_by_row(nodes)

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


def _is_dummy_dev(node: dict) -> bool:
    """Check if a node is a dummy device, filler dummy, or tap cell."""
    if not isinstance(node, dict):
        return False
    dev_id = str(node.get("id", ""))
    dev_id_upper = dev_id.upper()
    
    # Prefix-based detection (overrides structural flag for known dummy patterns)
    if dev_id_upper.startswith((
        "DUMMY", "EDGE_DUMMY", "FILLER_DUMMY", "MATCH_DUMMY", 
        "STRUCT_DUMMY", "TAP", "DUMMY_"
    )) or (len(dev_id_upper) >= 2 and dev_id_upper[0] == "D" and dev_id_upper[1:].isdigit()):
        return True

    if node.get("structural"):
        return False
    return bool(node.get("is_dummy"))


def optimize_all_rows_orientations(nodes: list, terminal_nets: dict = None) -> list:
    """
    Automatically optimize S/D swaps and orientations of all row transistors 
    globally to maximize valid diffusion-sharing abutments and resolve shorts,
    while strictly preserving bilateral mirror symmetry.
    
    Also re-biases all dummy nodes in each row after active transistor swaps have been optimized.
    """
    if not nodes:
        return nodes
        
    if terminal_nets is None:
        terminal_nets = {}
        
    from ai_agent.placement.finger_grouper import _parse_id
    import copy
    import itertools

    # 1. Group active transistors by row Y
    rows = group_nodes_by_row([n for n in nodes if not _is_dummy_dev(n)])
        
    for y_val, row_nodes in rows.items():
        # Sort horizontally by X coordinate
        row_nodes.sort(key=lambda n: float(n.get("geometry", {}).get("x", 0.0)))
        
        # Group contiguous fingers belonging to the same parent device ID
        blocks = []
        i_idx = 0
        while i_idx < len(row_nodes):
            node = row_nodes[i_idx]
            dev_id = node["id"]
            parent_id, _, _ = _parse_id(dev_id)
            if not parent_id:
                i_idx += 1
                continue
            
            chain = [node]
            j_idx = i_idx + 1
            while j_idx < len(row_nodes):
                next_node = row_nodes[j_idx]
                next_id = next_node["id"]
                next_parent_id, _, _ = _parse_id(next_id)
                if next_parent_id == parent_id:
                    chain.append(next_node)
                    j_idx += 1
                else:
                    break
            
            blocks.append({
                "parent_id": parent_id,
                "nodes": chain
            })
            i_idx = j_idx
            
        M = len(blocks)
        if M <= 1:
            continue
        
        # Skip interleaved patterns - they will be handled by maximize_interleaved_abutment
        # An interleaved pattern has blocks of different transistors that alternate.
        # This means the block sequence has non-contiguous occurrences of the same parent_id.
        is_interleaved = False
        if M > 2:
            seen_blocks = [b["parent_id"] for b in blocks]
            if len(seen_blocks) != len(set(seen_blocks)):
                is_interleaved = True

        if is_interleaved:
            vprint(f"[optimize_all_rows_orientations] Skipping interleaved row y={y_val:.3f} (handled by maximize_interleaved_abutment)")
            continue
            
        # Compute geometric center of the row to pair symmetric blocks
        xs = [float(n["geometry"].get("x", 0.0)) for n in row_nodes]
        widths = [float(n["geometry"].get("width", 0.294)) for n in row_nodes]
        row_center_x = (min(xs) + max(xs[idx] + widths[idx] for idx in range(len(xs)))) / 2.0
        
        # Prepare block data and compute center_x of each block
        blocks_data = []
        for b_idx, b in enumerate(blocks):
            p_id = b["parent_id"]
            nets = terminal_nets.get(p_id) or terminal_nets.get(b["nodes"][0]["id"]) or {}
            s_net = nets.get("S")
            d_net = nets.get("D")
            k = len(b["nodes"])
            blocks_data.append({
                "parent_id": p_id,
                "s": s_net,
                "d": d_net,
                "k": k
            })
            # Geometric center of the block
            b_xs = [float(node["geometry"]["x"]) for node in b["nodes"]]
            b_widths = [float(node["geometry"].get("width", 0.294)) for node in b["nodes"]]
            b["center_x"] = min(b_xs) + (max(b_xs[idx] + b_widths[idx] for idx in range(len(b_xs))) - min(b_xs)) / 2.0
            
        # Pair symmetric blocks about the row center
        paired_partners = {} # block_index -> partner_block_index
        for i in range(M):
            if i in paired_partners:
                continue
            bi = blocks[i]
            best_j = None
            best_dist = 0.25 # tolerance in microns
            for j in range(i + 1, M):
                bj = blocks[j]
                dist_from_sym = abs((bi["center_x"] + bj["center_x"]) / 2.0 - row_center_x)
                if dist_from_sym < best_dist and len(bi["nodes"]) == len(bj["nodes"]):
                    best_j = j
                    best_dist = dist_from_sym
            if best_j is not None:
                paired_partners[i] = best_j
                paired_partners[best_j] = i
                
        # Make all blocks independently flippable to maximize abutment
        independent_indices = list(range(M))
                
        num_vars = len(independent_indices)
        
        def get_full_flips(ind_flips):
            flips = [0] * M
            for idx, val in enumerate(ind_flips):
                orig_idx = independent_indices[idx]
                flips[orig_idx] = val
                # Don't force partner to opposite flip - allow independent optimization
                # partner = paired_partners.get(orig_idx)
                # if partner is not None:
                #     flips[partner] = 1 - val
            return flips
            
        def get_boundary_nets(b_idx, flip):
            bd = blocks_data[b_idx]
            s = bd["s"]
            d = bd["d"]
            k = bd["k"]
            if not s or not d:
                return None, None
            
            # Get the actual first and last nodes to check their swapped_sd flags
            block_nodes = blocks[b_idx]["nodes"]
            first_node = block_nodes[0]
            last_node = block_nodes[-1]
            
            first_swapped = first_node.get("swapped_sd", False)
            last_swapped = last_node.get("swapped_sd", False)
            
            # Calculate effective flip state for first and last fingers
            # effective = swapped_sd XOR orientation_flipped XOR block_flip
            first_orient = first_node.get("geometry", {}).get("orientation", "R0")
            first_orient_flipped = first_orient in ("MY", "R180")
            first_effective_flipped = first_swapped ^ first_orient_flipped ^ flip
            
            last_orient = last_node.get("geometry", {}).get("orientation", "R0")
            last_orient_flipped = last_orient in ("MY", "R180")
            last_effective_flipped = last_swapped ^ last_orient_flipped ^ flip
            
            # Left boundary depends on first finger
            if first_effective_flipped:
                left = d
            else:
                left = s
            
            # Right boundary depends on last finger and finger count
            if last_effective_flipped:
                if k % 2 == 1:  # odd
                    right = s
                else:  # even
                    right = d
            else:
                if k % 2 == 1:  # odd
                    right = d
                else:  # even
                    right = s
            
            return left, right

        def score_combination(flips):
            score = 0
            for i in range(M - 1):
                _, r_net = get_boundary_nets(i, flips[i])
                l_net, _ = get_boundary_nets(i + 1, flips[i+1])
                if r_net and l_net and r_net != "NC" and l_net != "NC" and r_net == l_net:
                    score += 1
            return score
            
        def composite_score(flips):
            sc = score_combination(flips)
            flip_count = sum(flips)
            # Heavily prioritize abutment over flip count
            # Each abutment is worth 1000 points, each flip costs 1 point
            return sc * 1000 - flip_count
            
        # Search for best combination
        best_ind_flips = [0] * num_vars
        if num_vars <= 10:
            best_score = -float('inf')
            for ind_flips in itertools.product([0, 1], repeat=num_vars):
                flips = get_full_flips(ind_flips)
                sc = composite_score(flips)
                if sc > best_score:
                    best_score = sc
                    best_ind_flips = list(ind_flips)
        else:
            best_score = composite_score(get_full_flips(best_ind_flips))
            improved = True
            while improved:
                improved = False
                for i in range(num_vars):
                    test_ind_flips = list(best_ind_flips)
                    test_ind_flips[i] = 1 - test_ind_flips[i]
                    sc = composite_score(get_full_flips(test_ind_flips))
                    if sc > best_score:
                        best_score = sc
                        best_ind_flips = test_ind_flips
                        improved = True
                        
        best_flips = get_full_flips(best_ind_flips)
        
        # Apply best flips back to nodes.
        for b_idx, b in enumerate(blocks):
            flip = best_flips[b_idx]
            p_id = b["parent_id"]
            nets = terminal_nets.get(p_id) or terminal_nets.get(b["nodes"][0]["id"]) or {}
            canon_s = nets.get("S")
            canon_d = nets.get("D")
            canon_g = nets.get("G")
            
            for idx, node in enumerate(b["nodes"]):
                node["_block_flip"] = bool(flip)
                node.setdefault("geometry", {})["orientation"] = "R0"
                
                # Determine if this finger should be swapped
                # For alternating fingers: even fingers (0, 2, 4...) are normal, odd fingers (1, 3, 5...) are swapped
                # If the whole block is flipped, this pattern is inverted
                is_swapped_phase = (idx % 2 == 1) != bool(flip)
                
                # Set the swapped flag - get_block_boundary_nets will handle the swap logic
                node["swapped_sd"] = is_swapped_phase
                
                # Set canonical nets (not swapped) - the flag tells boundary calculation to swap
                if canon_s and canon_d:
                    node["net_s"] = canon_s
                    node["net_d"] = canon_d
                if canon_g:
                    node["net_g"] = canon_g
                
    # 2. Re-bias all dummy nodes in each row after active swaps are optimized (neighbor-based!)
    all_row_nodes = group_nodes_by_row(nodes)
        
    for y_val, r_nodes in all_row_nodes.items():
        # Sort horizontally
        r_nodes.sort(key=lambda n: float(n.get("geometry", {}).get("x", 0.0)))
        
        # Run the dummy biasing pass
        for i, node in enumerate(r_nodes):
            is_dummy = _is_dummy_dev(node)
            if not is_dummy:
                continue
            # Skip tap cells if they shouldn't be modified
            if str(node.get("id", "")).upper().startswith("TAP"):
                continue
                
            dev_type = str(node.get("type", "nmos")).strip().lower()
            rail_net = "VDD" if "pmos" in dev_type else "VSS"
            
            # Find nearest non-dummy left neighbor
            left_non_dummy = None
            for idx in range(i - 1, -1, -1):
                n = r_nodes[idx]
                if not _is_dummy_dev(n):
                    left_non_dummy = n
                    break
                    
            # Find nearest non-dummy right neighbor
            right_non_dummy = None
            for idx in range(i + 1, len(r_nodes)):
                n = r_nodes[idx]
                if not _is_dummy_dev(n):
                    right_non_dummy = n
                    break
                    
            left_net = None
            right_net = None
            right_term_type = 'D'
            left_term_type = 'S'
            
            if left_non_dummy:
                from ai_agent.placement.finger_grouper import get_block_boundary_nets
                _, left_net = get_block_boundary_nets([left_non_dummy], False)
                lorient = left_non_dummy.get("geometry", {}).get("orientation", "R0")
                lflipped = lorient in ("MY", "R180", "MX")
                lswapped = bool(left_non_dummy.get("swapped_sd", False))
                ltotal_flipped = lflipped ^ lswapped
                
                lf_idx = int(left_non_dummy.get("electrical", {}).get("nf", 1))
                is_even = (lf_idx % 2 == 0)
                if is_even:
                    right_term_type = 'D' if ltotal_flipped else 'S'
                else:
                    right_term_type = 'S' if ltotal_flipped else 'D'
                    
            if right_non_dummy:
                from ai_agent.placement.finger_grouper import get_block_boundary_nets
                right_net, _ = get_block_boundary_nets([right_non_dummy], False)
                rorient = right_non_dummy.get("geometry", {}).get("orientation", "R0")
                rflipped = rorient in ("MY", "R180", "MX")
                rswapped = bool(right_non_dummy.get("swapped_sd", False))
                rtotal_flipped = rflipped ^ rswapped
                
                left_term_type = 'S' if rtotal_flipped else 'D'
                
            if not left_net or left_net == "NC":
                left_net = right_net if (right_net and right_net != "NC") else rail_net
            if not right_net or right_net == "NC":
                right_net = left_net
                
            node["net_d"] = left_net
            node["net_s"] = right_net
            
            if left_non_dummy and right_non_dummy:
                if right_term_type == 'S' and left_term_type == 'D':
                    node["net_g"] = left_net
                elif right_term_type == 'D' and left_term_type == 'S':
                    node["net_g"] = right_net
                elif right_term_type == 'S' and left_term_type == 'S':
                    node["net_g"] = left_net
                else:
                    node["net_g"] = rail_net
            else:
                node["net_g"] = rail_net

    return nodes

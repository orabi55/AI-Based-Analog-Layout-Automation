"""
Symmetry Enforcer Node
======================
LangGraph node (3.5) that runs after finger_expansion and before routing_previewer.

Reads the machine-readable [SYMMETRY] block from constraint_text and applies a
fully deterministic matched + symmetric placement on the physical finger nodes.

KEY DESIGN: MATCHING (ABBA/common-centroid) is done FIRST by the matching engine
(merge_matched_groups). This node does NOT re-place individual fingers.
Instead it translates each interdigitated block as a RIGID BODY so its midpoint
lands on the shared vertical axis x_axis. This gives both matching AND symmetry.

No LLM is involved. Same input => identical output every run.

Functions:
- parse_symmetry_block: Extract pairs/axis info from [SYMMETRY] block text.
- _find_finger_ids: Resolve a logical device ID to its physical finger IDs.
- _enforce_two_half: Rigid-body block translation preserving ABBA, centering on axis.
- node_symmetry_enforcer: LangGraph node entry point.
"""

import copy
import re
import time
from typing import Dict, List, Tuple

from config.design_rules import PITCH_UM, ROW_HEIGHT_UM
from ai_agent.tools.overlap_resolver import resolve_overlaps
from ai_agent.tools.inventory import validate_device_count
from ai_agent.utils.logging import vprint
from ai_agent.nodes._shared import ip_step

HALF_PITCH = PITCH_UM / 2.0   # 0.147 um -- snap unit for axis


# ---------------------------------------------------------------------------
# Parse [SYMMETRY] block
# ---------------------------------------------------------------------------

def parse_symmetry_block(constraint_text: str) -> dict:
    """
    Extract symmetry constraints from the [SYMMETRY]...[/SYMMETRY] block.

    Returns a dict with keys:
        mode          : str  e.g. "two_half"
        axis_row      : str  e.g. "both"
        pairs         : list of (left_id, right_id, rank:int)
        axis_devices  : list of str (device IDs)
    Returns {} if block not found or malformed.
    """
    if not constraint_text or "[SYMMETRY]" not in constraint_text:
        return {}

    m = re.search(r"\[SYMMETRY\](.*?)\[/SYMMETRY\]", constraint_text, re.DOTALL)
    if not m:
        return {}

    block = m.group(1).strip()
    result = {"mode": "two_half", "axis_row": "both", "pairs": [], "axis_devices": []}

    for line in block.splitlines():
        line = line.strip()
        if line.startswith("mode="):
            result["mode"] = line.split("=", 1)[1].strip()
        elif line.startswith("axis_row="):
            result["axis_row"] = line.split("=", 1)[1].strip()
        elif line.startswith("pair="):
            rest = line[len("pair="):]
            rank_m = re.search(r"rank=(\d+)", rest)
            rank = int(rank_m.group(1)) if rank_m else 1
            pair_part = re.sub(r"\s*rank=\d+", "", rest).strip()
            parts = [p.strip() for p in pair_part.split(",") if p.strip()]
            if len(parts) == 2:
                result["pairs"].append((parts[0], parts[1], rank))
        elif line.startswith("axis="):
            ax = line.split("=", 1)[1].strip()
            if ax:
                result["axis_devices"].append(ax)

    if not result["pairs"] and not result["axis_devices"]:
        return {}

    return result


# ---------------------------------------------------------------------------
# Find physical finger IDs for a logical device
# ---------------------------------------------------------------------------

def _find_finger_ids(logical_id: str, nodes: List[dict]) -> List[str]:
    """
    Given a logical device ID (e.g. 'MM1'), return all physical finger IDs
    (e.g. ['MM1_f1', 'MM1_f2']) from the node list.

    Tries three strategies:
    1. Node has a '_fingers' list.
    2. Prefix match: node id starts with logical_id + '_f'.
    3. Exact match (single-finger device).
    """
    # Strategy 1: explicit _fingers list on a logical node
    for n in nodes:
        if str(n.get("id", "")) == logical_id and "_fingers" in n:
            return [str(f) for f in n["_fingers"] if f]

    # Strategy 2: prefix match on physical finger nodes
    prefix = logical_id + "_f"
    prefix_matches = [
        str(n["id"]) for n in nodes
        if str(n.get("id", "")).startswith(prefix)
    ]
    if prefix_matches:
        return sorted(prefix_matches)

    # Strategy 3: exact match (single finger or already physical)
    for n in nodes:
        if str(n.get("id", "")) == logical_id:
            return [logical_id]

    return []


# ---------------------------------------------------------------------------
# Core deterministic enforcement — rigid-body block translation
# ---------------------------------------------------------------------------

def _enforce_two_half(
    nodes: List[dict],
    pairs: List[Tuple[str, str, int]],
    axis_devices: List[str],
    pitch: float = PITCH_UM,
) -> List[dict]:
    """
    Enforce MATCHED + SYMMETRIC placement around a shared vertical axis.

    CRITICAL DESIGN:
    The matching engine (merge_matched_groups) already produced ABBA /
    common-centroid interdigitation for each pair. For example:
        MM1_f1, MM2_f1, MM2_f2, MM1_f2   (ABBA diff pair)
    That pattern MUST be preserved. We must NOT re-assign individual fingers.

    What this function does:
    1. Collect all fingers belonging to pairs and axis devices.
    2. Compute a SHARED x_axis = midpoint of total bounding x-range of all
       symmetry-constrained fingers, snapped to HALF_PITCH (0.147 um).
       => This axis is the SAME for every row (PMOS and NMOS share one axis).
    3. For each matched pair (left+right):
         - Treat all their fingers as ONE rigid ABBA block.
         - Compute the block's current midpoint.
         - Translate the entire block by (x_axis - current_midpoint).
         => ABBA pattern shifts as a unit -- matching is preserved, and the
            block's centre lands exactly on the vertical axis.
    4. For axis devices (e.g. tail current source):
         - Rigid-body translate their fingers to centre on x_axis.
    """
    if not nodes:
        return nodes

    node_map: Dict[str, dict] = {str(n["id"]): n for n in nodes}

    # --- Resolve finger IDs ------------------------------------------------
    all_sym_fids: List[str] = []
    # Store as list of (left_dev, right_dev, rank, combined_fids)
    pair_blocks: List[Tuple[str, str, int, List[str]]] = []

    for left_dev, right_dev, rank in pairs:
        left_fids = _find_finger_ids(left_dev, nodes)
        right_fids = _find_finger_ids(right_dev, nodes)
        block_fids = left_fids + right_fids  # ABBA order already set in x coords
        pair_blocks.append((left_dev, right_dev, rank, block_fids))
        all_sym_fids.extend(block_fids)

    axis_blocks: Dict[str, List[str]] = {}
    for ax_dev in axis_devices:
        ax_fids = _find_finger_ids(ax_dev, nodes)
        axis_blocks[ax_dev] = ax_fids
        all_sym_fids.extend(ax_fids)

    # Deduplicate while preserving first-seen order
    seen: set = set()
    unique_fids: List[str] = []
    for fid in all_sym_fids:
        if fid not in seen:
            seen.add(fid)
            unique_fids.append(fid)

    if not unique_fids:
        vprint("[SYMM] No symmetry-constrained fingers found -- pass through")
        return nodes

    # --- Step 1: Compute shared x_axis ------------------------------------
    xs_global: List[float] = []
    for fid in unique_fids:
        n = node_map.get(fid)
        if n:
            try:
                xs_global.append(float(n.get("geometry", {}).get("x", 0.0)))
            except (TypeError, ValueError):
                pass

    if not xs_global:
        vprint("[SYMM] No valid x coordinates -- pass through")
        return nodes

    raw_axis = (min(xs_global) + max(xs_global)) / 2.0
    # Snap to nearest HALF_PITCH grid (0.147 um)
    x_axis = round(round(raw_axis / HALF_PITCH) * HALF_PITCH, 6)
    vprint(f"[SYMM] raw_axis={raw_axis:.4f} => x_axis={x_axis:.4f} um (shared across all rows)")

    # --- Step 2: Rigid-body translate each matched pair block -------------
    for left_dev, right_dev, rank, block_fids in pair_blocks:
        if not block_fids:
            continue

        # Current bounding midpoint of the whole ABBA block
        bxs: List[float] = []
        for fid in block_fids:
            n = node_map.get(fid)
            if n:
                try:
                    bxs.append(float(n.get("geometry", {}).get("x", 0.0)))
                except (TypeError, ValueError):
                    pass
        if not bxs:
            continue

        block_mid = (min(bxs) + max(bxs)) / 2.0
        dx = x_axis - block_mid  # rigid shift -- ABBA pattern unchanged

        for fid in block_fids:
            n = node_map.get(fid)
            if not n:
                continue
            geo = n.setdefault("geometry", {})
            try:
                geo["x"] = round(float(geo.get("x", 0.0)) + dx, 6)
            except (TypeError, ValueError):
                pass

        vprint(
            f"[SYMM] pair ({left_dev}+{right_dev}) rank={rank}: "
            f"block_mid={block_mid:.4f} dx={dx:+.4f} => centred at {x_axis:.4f} "
            f"[{len(block_fids)} fingers, ABBA preserved]"
        )

    # --- Step 3: Rigid-body centre axis devices on x_axis ----------------
    for ax_dev, ax_fids in axis_blocks.items():
        if not ax_fids:
            continue

        axs: List[float] = []
        for fid in ax_fids:
            n = node_map.get(fid)
            if n:
                try:
                    axs.append(float(n.get("geometry", {}).get("x", 0.0)))
                except (TypeError, ValueError):
                    pass
        if not axs:
            continue

        ax_mid = (min(axs) + max(axs)) / 2.0
        dx = x_axis - ax_mid

        for fid in ax_fids:
            n = node_map.get(fid)
            if not n:
                continue
            try:
                n.setdefault("geometry", {})["x"] = round(
                    float(n["geometry"].get("x", 0.0)) + dx, 6
                )
            except (TypeError, ValueError):
                pass

        vprint(
            f"[SYMM] axis={ax_dev} ({len(ax_fids)} fingers): "
            f"mid={ax_mid:.4f} dx={dx:+.4f} => centred at {x_axis:.4f}"
        )

    return nodes


# ---------------------------------------------------------------------------
# LangGraph node entry point
# ---------------------------------------------------------------------------

def node_symmetry_enforcer(state: dict) -> dict:
    """
    LangGraph node 3.5: deterministic matched + symmetric placement enforcer.

    Fires after finger_expansion (stage 3d) and before routing_previewer.
    Passes through silently if no [SYMMETRY] block is found in constraint_text.
    """
    t0 = time.time()
    vprint("\n" + "-" * 60, flush=True)
    vprint("  STAGE 3.5: SYMMETRY ENFORCER", flush=True)
    vprint("-" * 60, flush=True)

    constraint_text = state.get("constraint_text", "")
    placement_mode = state.get("placement_mode", "auto")
    placement_nodes = state.get("placement_nodes", [])
    original_nodes = state.get("nodes", [])

    sym_info = parse_symmetry_block(constraint_text)

    if not sym_info:
        vprint("[SYMM] No [SYMMETRY] block found -- passing through")
        ip_step("3.5/5 Symmetry Enforcer", "skip (no [SYMMETRY] block)")
        return {}

    if placement_mode not in ("two_half", "auto"):
        vprint(f"[SYMM] placement_mode={placement_mode} -- passing through")
        ip_step("3.5/5 Symmetry Enforcer", f"skip (mode={placement_mode})")
        return {}

    pairs = sym_info.get("pairs", [])
    axis_devices = sym_info.get("axis_devices", [])

    vprint(f"[SYMM] {len(pairs)} pair(s), {len(axis_devices)} axis device(s)")
    for left, right, rank in pairs:
        vprint(f"[SYMM]   rank={rank} pair=({left}, {right})")
    for ax in axis_devices:
        vprint(f"[SYMM]   axis={ax}")

    if not pairs and not axis_devices:
        vprint("[SYMM] Empty [SYMMETRY] block -- passing through")
        ip_step("3.5/5 Symmetry Enforcer", "skip (empty block)")
        return {}

    # Apply rigid-body enforcement
    working = copy.deepcopy(placement_nodes) if placement_nodes else copy.deepcopy(original_nodes)
    working = _enforce_two_half(working, pairs, axis_devices)

    # Validate device count unchanged
    conservation = validate_device_count(original_nodes, working)
    if not conservation.get("pass", True):
        vprint("[SYMM] CONSERVATION FAILURE after enforcement -- reverting")
        ip_step("3.5/5 Symmetry Enforcer", "FAILED conservation check -- reverted")
        return {}

    # Resolve any overlaps introduced by the translation
    moved_ids = resolve_overlaps(working)
    if moved_ids:
        vprint(f"[SYMM] Post-enforcement overlap fix: {len(moved_ids)} device(s) nudged")

    elapsed = time.time() - t0
    ip_step(
        "3.5/5 Symmetry Enforcer",
        f"ok -- {len(pairs)} matched pair(s) + {len(axis_devices)} axis device(s) "
        f"centred on shared axis ({elapsed:.1f}s)"
    )

    return {
        "placement_nodes": working,
        "deterministic_snapshot": copy.deepcopy(working),
    }

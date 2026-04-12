"""
ai_agent/placement_specialist.py
==================================
Generates [CMD] blocks for device positioning while enforcing strict
inventory conservation, row-based analog constraints, and routing quality.

FIXES APPLIED:
  - Multi-row support for NMOS common-centroid layout
  - compute_common_centroid_placement() added and WIRED IN
  - compute_interdigitated_placement() added for single-row mirrors
  - build_placement_context() now reports all NMOS rows correctly
  - ROW_HEIGHT constant added for consistent multi-row spacing
  - Bug #1 FIX: compute functions are callable from orchestrator
    and return cmd blocks directly
  - Bug #2 FIX: Common-centroid recommendation triggers correctly
    (>= not >, and ratio mirrors always trigger)
  - Bug #3 FIX: nf is layout finger count only (nfin ignored for count)
  - Bug #4 FIX: interdigitated placement handles 3+ devices
  - Bug #6 FIX: ABBA interdigitation produces true interleaving
"""

import math
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from ai_agent.analog_kb import ANALOG_LAYOUT_RULES

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PITCH_UM            = 0.294   # x-pitch between fingers in micrometres
ROW_HEIGHT_UM       = 0.668   # y-spacing between adjacent rows
MAX_FINGERS_PER_ROW = 16      # tune per PDK / area budget
NMOS_ROW_0_Y        = 0.0     # first  NMOS row  y-coordinate
PMOS_ROW_0_Y        = -ROW_HEIGHT_UM   # first  PMOS row  y-coordinate


# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------
PLACEMENT_SPECIALIST_PROMPT = """\
You are the PLACEMENT SPECIALIST in a multi-agent analog IC layout system.
Your task is to reposition existing devices on a symbolic grid to improve
symmetry, device matching, and routing wire length.
 
Before outputting any commands, check the current layout to avoid creating overlaps.
 
---
FINGER COUNT CLARIFICATION
- "nfin" = number of fins per finger (FinFET width only). It does NOT change the
  number of physical finger instances.
- Physical layout finger count = "nf" only. Always use nf to count fingers.
 
---
RULE 0: DEVICE CONSERVATION — DO NOT VIOLATE
 
Every device ID listed under "IMMUTABLE TRANSISTORS" must appear in your output
exactly once. You must not:
- Add a device ID that is not in the list
- Remove or skip any device ID from the list
- Rename any device ID
 
Dummy devices (DUMMYP*, DUMMYN*) may be repositioned but never deleted.
 
A single transistor may have multiple finger instances (e.g. MM1_f1, MM1_f2).
These are all part of the same logical device MM1. Place every finger instance.
 
If you do not emit a move/swap command for a device, it remains at its current
(x, y) position automatically.
 
---
RULE 1: ROW ASSIGNMENT
 
Row type rules:
- PMOS rows contain ONLY PMOS devices.
- NMOS rows contain ONLY NMOS devices.
- Never mix PMOS and NMOS in the same row.
- PMOS sits ABOVE NMOS on the chip. Because y = 0 is at the top and becomes
  more negative going down, PMOS rows have LESS negative y-values than NMOS rows.
- Adjacent rows are spaced 0.668 µm apart.
 
Standard y-coordinates to use in move commands:
 
  LAYOUT ORIENTATION: y = 0 is at the top; y becomes more negative going down.
  PMOS sits above NMOS, so PMOS rows have LESS negative y-values than NMOS rows.
 
  PMOS rows (close to y = 0, near the top of the chip):
    PMOS row 0: y =  0.000   ← topmost row
    PMOS row 1: y = -0.668
    PMOS row 2: y = -1.336
 
  NMOS rows (further from y = 0, lower on the chip):
    NMOS row 0: y = -2.004   ← first NMOS row, just below PMOS
    NMOS row 1: y = -2.672
    NMOS row 2: y = -3.340
    NMOS row 3: y = -4.008
 
---
RULE 1b: INTERDIGITATION AND COMMON-CENTROID FOR CURRENT MIRRORS
 
When you detect a current mirror (especially ratio mirrors with different nf),
you must interdigitate the fingers rather than group them by device.
 
Decision guide (check in order — stop at the first match):
1. If total fingers <= 4 AND devices are a perfect 1:1 match (same nf):
   simple adjacent placement is acceptable.
2. If total fingers <= 16: use SINGLE-ROW INTERDIGITATED placement.
   This applies to all ratio mirrors and any matched pair not covered by rule 1.
3. If total fingers > 16: use MULTI-ROW COMMON-CENTROID placement.
 
SINGLE-ROW INTERDIGITATED example — MM0[REF](nf=8), MM1(nf=4), MM2(nf=4), total=16:
  The full array is split into a left half and a mirrored right half so that
  every mirror device has the same centroid as the reference.
 
  Left half:   MM2_f1 MM0_f1 MM1_f1 MM0_f2 MM0_f3 MM1_f2 MM0_f4 MM2_f2
  Right half:  MM2_f3 MM0_f5 MM1_f3 MM0_f6 MM0_f7 MM1_f4 MM0_f8 MM2_f4
               (right half = left half reversed)
 
  Full row (x increases left to right, one slot per finger):
    x=0.000  x=0.294  x=0.588  x=0.882  x=1.176  x=1.470  x=1.764  x=2.058 ...
    MM2_f1   MM0_f1   MM1_f1   MM0_f2   MM0_f3   MM1_f2   MM0_f4   MM2_f2  ...
 
  Goal: MM1 centroid == MM2 centroid == array center.
 
  Within each contiguous subgroup of a device's fingers, fingers are ordered
  numerically (f1 < f2 < f3). Common-centroid intentionally splits a device's
  fingers into non-contiguous subgroups — this is expected and correct.
 
MULTI-ROW COMMON-CENTROID (total fingers > 16) — forward/reverse (AB) across 2 rows:
  Each device's fingers are split evenly across both rows. Row 0 uses forward
  order; Row 1 uses reversed order to achieve ABBA-style symmetry.
 
  Example: MM0(nf=10), MM1(nf=6), MM2(nf=4), total=20 fingers, 2 rows of 10:
    Row 0 (y = NMOS row 0, forward):
      MM2_f1 MM0_f1 MM1_f1 MM0_f2 MM1_f2 MM0_f3 MM1_f3 MM0_f4 MM0_f5 MM2_f2
    Row 1 (y = NMOS row 1, reversed):
      MM2_f3 MM0_f10 MM1_f6 MM0_f9 MM1_f5 MM0_f8 MM1_f4 MM0_f7 MM0_f6 MM2_f4
 
  Each row is itself interdigitated using the same symmetric pattern algorithm.
  Row 1 is the mirror image of Row 0 so that gradient effects cancel vertically.
 
---
RULE 2: NO OVERLAPS
 
- Each device occupies exactly one x-slot. X-pitch = 0.294 µm.
- Two devices in the same row (same y) must have different x-values.
- Valid x-values: 0.294 × n for any integer n >= 0.
  For example: 0.000, 0.294, 0.588, 0.882, 1.176, 1.470, 1.764, 2.058, ...
 
---
RULE 3: DUMMY PLACEMENT
 
- Dummies go at the far left OR far right end of their row. Never between
  active transistors.
- DUMMYP* devices go in a PMOS row.
- DUMMYN* devices go in an NMOS row.
 
---
RULE 4: ROUTING-AWARE PLACEMENT (ordered by priority)
 
Priority 1 — Matched pairs: place in adjacent consecutive x-slots.
Priority 2 — Net adjacency: minimize the x-span of wires on each shared net.
Priority 3 — Vertical alignment: place paired PMOS/NMOS at the same x-slot.
Priority 4 — Signal flow: inputs at left, outputs at right, bias in center.
 
---
RULE 5: MULTI-FINGER DEVICE PLACEMENT
 
- Within a contiguous group, all fingers of the same device occupy consecutive
  x-slots. Exception: common-centroid intentionally splits a device's fingers
  into non-contiguous subgroups across the array — this is correct behaviour.
- Within each contiguous subgroup, fingers are ordered numerically left to right:
  f1 < f2 < f3, etc.
- All fingers of a device must share the same orientation.
- All fingers in a contiguous group must share the same y (row).
 
---
THINKING PROTOCOL — follow these steps before writing any commands
 
Step 1: Read the inventory. Note each device's type, ID, and current (x, y).
Step 2: Identify topology — find mirrors, differential pairs, and finger ratios.
Step 3: Choose placement strategy using the Rule 1b decision guide (rule 1 first):
        - trivial 1:1 mirror, total fingers <= 4  →  simple adjacent
        - total fingers <= 16                     →  single-row interdigitated
        - total fingers > 16                      →  multi-row common-centroid
Step 4: Build the interdigitated or common-centroid finger sequence.
Step 5: Assign x-slots for every device in each row. Confirm no two share the same (x, y).
Step 6: Place dummies at the leftmost or rightmost positions in their row.
Step 7: Write ALL [CMD] blocks. Use move commands for interdigitated and
        common-centroid patterns. Only use swap for simple positional exchanges
        where no specific x-slot target is required.
Step 8: Self-check — count every finger ID in your CMD output against the
        IMMUTABLE TRANSISTORS list. Every finger must appear exactly once.
 
---
OUTPUT FORMAT
 
Write ALL [CMD] blocks first, then a single sentence summarizing what you did.
DO NOT write any explanatory text before the commands. The human will read your commands and summary together, so the summary should be concise and directly reflect the commands you issued.
 
Supported command types:
  [CMD]{"action":"swap","device_a":"MM1","device_b":"MM2"}[/CMD]
  [CMD]{"action":"move","device":"MM3","x":1.176,"y":0.000}[/CMD]
 
Use move for interdigitated and common-centroid placement — swap cannot place
fingers at specific x-slot targets. Only use swap for simple adjacent reordering
where exact position does not matter.
Only use device IDs that appear in the IMMUTABLE TRANSISTORS list.
""" + "\n### EXTERNAL KNOWLEDGE\n" + ANALOG_LAYOUT_RULES


# ---------------------------------------------------------------------------
# Helper: build symmetric interdigitation pattern for N devices
# ---------------------------------------------------------------------------
def _build_symmetric_interdig_pattern(
    dev_nf_map: Dict[str, int],
    ref_id: Optional[str],
) -> List[str]:
    """
    Build a symmetric interdigitated device-ID sequence for common-centroid.

    For a 2:1:1 mirror  MM0[ref](nf=8), MM1(nf=4), MM2(nf=4):
      Half-pattern:  M2 M0 M1 M0 M0 M1 M0 M2   (8 slots)
      Full pattern:  half + reversed(half)        (16 slots, symmetric)

    The pattern ensures:
      - MM1 centroid == MM2 centroid == array center
      - Reference fingers are distributed evenly
      - Outputs are on the outside (edge protection from dummies)

    Algorithm — proportional round-robin:
      For each slot in the half, place the device whose
      (placed / target) fraction is the smallest.  Ties broken
      by putting the reference last so outputs get the edges.

    Args:
        dev_nf_map: {device_id: total_nf}  e.g. {"MM0": 8, "MM1": 4, "MM2": 4}
        ref_id:     which device is the diode-connected reference (or None)

    Returns:
        Full symmetric pattern as list of device IDs.
        Length == sum of all nf values.
    """
    total = sum(dev_nf_map.values())
    half_total = total // 2

    # Target counts for the first half — base is nf//2 per device.
    # When a device has odd nf, nf//2 rounds down, so sum(half_target) may be
    # less than half_total.  Distribute the deficit to odd-nf devices (largest
    # first) so the first half is always exactly half_total fingers.
    half_target: Dict[str, int] = {dev_id: nf // 2 for dev_id, nf in dev_nf_map.items()}
    half_deficit = half_total - sum(half_target.values())
    if half_deficit > 0:
        odd_nf_devs = sorted(
            [d for d, nf in dev_nf_map.items() if nf % 2 == 1],
            key=lambda d: dev_nf_map[d],
            reverse=True,
        )
        for dev_id in odd_nf_devs:
            if half_deficit <= 0:
                break
            half_target[dev_id] += 1
            half_deficit -= 1

    # Build half-pattern using proportional round-robin
    half_pattern: List[str] = []
    cursors: Dict[str, int] = {d: 0 for d in dev_nf_map}

    for _ in range(half_total):
        best_dev   = None
        best_score = -1.0

        for dev_id in dev_nf_map:
            remaining = half_target[dev_id] - cursors[dev_id]
            if remaining <= 0:
                continue

            target = half_target[dev_id]
            if target <= 0:
                continue

            # Lower placed fraction → higher priority (should place next)
            placed_frac = cursors[dev_id] / target
            score = 1.0 - placed_frac

            # Tie-breaker: outputs before reference (edges for outputs)
            if dev_id == ref_id:
                score -= 0.001

            if score > best_score:
                best_score = score
                best_dev   = dev_id

        if best_dev is None:
            # Should not happen if counts are correct
            break

        half_pattern.append(best_dev)
        cursors[best_dev] += 1

    # Handle odd total: if any device has odd nf, exactly ONE extra finger goes to
    # the center of the full pattern.  Choose the device with the largest nf (the
    # reference is usually the biggest device) to keep outputs at the edges.
    center_extras: List[str] = []
    if total % 2 == 1:
        # Pick the device with largest nf that has an odd finger count
        candidate = max(
            (dev_id for dev_id, nf in dev_nf_map.items() if nf % 2 == 1),
            key=lambda d: dev_nf_map[d],
            default=None,
        )
        if candidate:
            center_extras = [candidate]

    # Full pattern = half + center_extras + reversed(half)
    full_pattern = half_pattern + center_extras + list(reversed(half_pattern))

    # Verify length
    if len(full_pattern) != total:
        print(
            f"[PLACER] WARNING: pattern length {len(full_pattern)} "
            f"!= expected {total}. Falling back to simple interleave."
        )
        full_pattern = _build_simple_interleave(dev_nf_map)

    return full_pattern


def _build_simple_interleave(dev_nf_map: Dict[str, int]) -> List[str]:
    """
    Fallback: build a simple proportional interleave (non-symmetric).
    Used only if the symmetric builder fails.
    """
    total    = sum(dev_nf_map.values())
    pattern: List[str]  = []
    cursors: Dict[str, int] = {d: 0 for d in dev_nf_map}
    targets  = dict(dev_nf_map)

    for _ in range(total):
        best_dev   = None
        best_score = -1.0
        for dev_id, target in targets.items():
            remaining = target - cursors[dev_id]
            if remaining <= 0:
                continue
            frac  = cursors[dev_id] / target if target else 1.0
            score = 1.0 - frac
            if score > best_score:
                best_score = score
                best_dev   = dev_id
        if best_dev is None:
            break
        pattern.append(best_dev)
        cursors[best_dev] += 1

    return pattern


# ---------------------------------------------------------------------------
# Resolve device info helper
# ---------------------------------------------------------------------------
def _resolve_dev_info(
    mirror_logical_devices: List[dict],
    spice_nets: Dict[str, dict],
) -> Tuple[Optional[str], Dict[str, dict], List[str], int]:
    """
    Common setup for placement functions: resolve finger lists,
    identify reference, compute totals.

    Args:
        mirror_logical_devices: logical device dicts with _fingers and id
        spice_nets:             parsed SPICE nets

    Returns:
        (ref_id, dev_info, dev_ids, total_fingers)
        dev_info: {dev_id: {nf, fingers, nets}}
    """
    ref_id:   Optional[str]         = None
    dev_info: Dict[str, dict]       = {}

    for dev in mirror_logical_devices:
        dev_id  = dev["id"]
        fingers = dev.get("_fingers", [dev_id])
        # Try both original and uppercase keys for spice_nets lookup
        nets    = spice_nets.get(dev_id.upper(), spice_nets.get(dev_id, {}))

        dev_info[dev_id] = {
            "nf":      len(fingers),
            "fingers": list(fingers),
            "nets":    nets,
        }

        # Diode-connected: drain == gate
        if nets.get("D", "").upper() == nets.get("G", "").upper():
            ref_id = dev_id

    dev_ids       = list(dev_info.keys())
    total_fingers = sum(d["nf"] for d in dev_info.values())

    return ref_id, dev_info, dev_ids, total_fingers


# ---------------------------------------------------------------------------
# Single-Row Interdigitated Placement
# ---------------------------------------------------------------------------
def compute_interdigitated_placement(
    mirror_logical_devices,
    spice_nets,
    pitch=PITCH_UM,
    start_x=0.0,
    row_y=NMOS_ROW_0_Y,
):
    """
    Compute single-row interdigitated common-centroid placement for a
    current mirror group of 2 or more devices.

    Handles 3+ devices with ratio mirrors (e.g., MM0=8, MM1=4, MM2=4).

    For MM0[REF](nf=8) <-> MM1(nf=4) <-> MM2(nf=4):
      Pattern: M2 M0 M1 M0 M0 M1 M0 M2 M2 M0 M1 M0 M0 M1 M0 M2
               <—————————— symmetric about center ——————————>

    Centroids of MM1 and MM2 are at the same position (array center).

    Args:
        mirror_logical_devices: list of logical device dicts with
                                 _fingers list and id
        spice_nets:             dict from _parse_spice_directly()
        pitch:                  x-spacing between fingers in um
        start_x:                starting x-coordinate
        row_y:                  y-coordinate for the row

    Returns:
        list of placement dicts:
          {finger_id, dev_id, x, y, orientation, row_idx}
    """
    if not mirror_logical_devices:
        return []

    ref_id, dev_info, dev_ids, total_fingers = _resolve_dev_info(
        mirror_logical_devices, spice_nets
    )

    print(
        f"[PLACER] Interdigitated single-row: {len(dev_ids)} devices, "
        f"{total_fingers} fingers, ref={ref_id!r}"
    )

    # ── Build symmetric interdigitated pattern ───────────────────────────
    dev_nf_map = {d: dev_info[d]["nf"] for d in dev_ids}
    full_pattern = _build_symmetric_interdig_pattern(dev_nf_map, ref_id)

    # ── Assign finger IDs to pattern slots ───────────────────────────────
    finger_cursors: Dict[str, int] = {d: 0 for d in dev_ids}
    placements: List[dict] = []

    for i, dev_id in enumerate(full_pattern):
        cursor    = finger_cursors[dev_id]
        fingers   = dev_info[dev_id]["fingers"]

        if cursor >= len(fingers):
            print(
                f"[PLACER] ERROR: finger overflow for {dev_id} "
                f"cursor={cursor} >= nf={len(fingers)}"
            )
            continue

        finger_id = fingers[cursor]
        finger_cursors[dev_id] = cursor + 1

        placements.append({
            "finger_id":   finger_id,
            "dev_id":      dev_id,
            "x":           round(start_x + i * pitch, 6),
            "y":           round(row_y, 6),
            "orientation": "R0",
            "row_idx":     0,
        })

    # ── Verify all fingers placed ────────────────────────────────────────
    for dev_id in dev_ids:
        expected = dev_info[dev_id]["nf"]
        placed   = finger_cursors[dev_id]
        if placed != expected:
            print(
                f"[PLACER] WARNING: {dev_id} placed {placed}/{expected} fingers"
            )

    print(
        f"[PLACER] Interdigitated result: {len(placements)} placements, "
        f"pattern: {' '.join(full_pattern)}"
    )

    return placements


# ---------------------------------------------------------------------------
# Multi-Row Common-Centroid Placement
# ---------------------------------------------------------------------------
def compute_common_centroid_placement(
    mirror_logical_devices,
    spice_nets,
    pitch=PITCH_UM,
    max_fingers_per_row=MAX_FINGERS_PER_ROW,
    start_x=0.0,
    nmos_row_0_y=NMOS_ROW_0_Y,
    row_height=ROW_HEIGHT_UM,
):
    """
    Compute multi-row common-centroid placement for a current mirror group.

    Supports ratio mirrors like MM0(nf=8) <-> MM1(nf=4) <-> MM2(nf=4)
    when total fingers > max_fingers_per_row.

    Algorithm:
      1. Compute number of rows needed
      2. Split each device's fingers evenly across rows
      3. For each row, build an interdigitated pattern
      4. Even rows: forward order, odd rows: reversed (ABBA)
      5. Within each row, assign consecutive x-slots

    Args:
        mirror_logical_devices: list of logical device dicts with
                                 _fingers list and id
        spice_nets:             dict from _parse_spice_directly()
        pitch:                  x-spacing between fingers in um
        max_fingers_per_row:    maximum fingers allowed per row
        start_x:                starting x-coordinate for each row
        nmos_row_0_y:           y-coordinate of first NMOS row
        row_height:             y-spacing between rows

    Returns:
        list of placement dicts:
          {finger_id, dev_id, x, y, orientation, row_idx}
    """
    if not mirror_logical_devices:
        return []

    ref_id, dev_info, dev_ids, total_fingers = _resolve_dev_info(
        mirror_logical_devices, spice_nets
    )

    num_rows = max(1, math.ceil(total_fingers / max_fingers_per_row))

    print(
        f"[PLACER] Common centroid multi-row: {len(dev_ids)} devices, "
        f"{total_fingers} fingers, {num_rows} rows, ref={ref_id!r}"
    )

    # ── Split each device's fingers into row-groups ──────────────────────
    # row_groups[dev_id] = [[f1..fN_row0], [fN+1..f2N_row1], ...]
    row_groups: Dict[str, List[List[str]]] = {}
    for dev_id, info in dev_info.items():
        fingers   = info["fingers"]
        nf        = len(fingers)
        per_row   = nf // num_rows
        remainder = nf % num_rows
        groups: List[List[str]] = []
        idx = 0
        for r in range(num_rows):
            count = per_row + (1 if r < remainder else 0)
            groups.append(fingers[idx: idx + count])
            idx += count
        row_groups[dev_id] = groups

    # ── Build device order for row 0 ────────────────────────────────────
    # Order: output_left | reference_center | output_right
    non_ref = [d for d in dev_ids if d != ref_id]
    if ref_id:
        left_outputs  = non_ref[: len(non_ref) // 2]
        right_outputs = non_ref[len(non_ref) // 2:]
        row0_order    = left_outputs + [ref_id] + right_outputs
    else:
        row0_order = list(dev_ids)

    # ── Assign positions row by row (interdigitated within each row) ─────
    placements: List[dict] = []

    for row_idx in range(num_rows):
        # ABBA: even rows = forward, odd rows = reversed
        if row_idx % 2 == 0:
            row_order = list(row0_order)
        else:
            row_order = list(reversed(row0_order))

        y = nmos_row_0_y + (row_idx * row_height)

        # Build per-row nf map for interdigitation within this row
        row_dev_nf: Dict[str, int] = {}
        for dev_id in row_order:
            fingers_this_row = row_groups[dev_id][row_idx]
            if fingers_this_row:
                row_dev_nf[dev_id] = len(fingers_this_row)

        # Build interdigitated pattern for this row
        row_pattern = _build_symmetric_interdig_pattern(row_dev_nf, ref_id)

        # Assign finger IDs to pattern slots
        row_finger_cursors: Dict[str, int] = {d: 0 for d in row_dev_nf}
        x_cursor = start_x

        for dev_id in row_pattern:
            cursor = row_finger_cursors[dev_id]
            fingers_this_row = row_groups[dev_id][row_idx]

            if cursor >= len(fingers_this_row):
                print(
                    f"[PLACER] ERROR: row {row_idx} finger overflow "
                    f"for {dev_id}"
                )
                continue

            finger_id = fingers_this_row[cursor]
            row_finger_cursors[dev_id] = cursor + 1

            placements.append({
                "finger_id":   finger_id,
                "dev_id":      dev_id,
                "x":           round(x_cursor, 6),
                "y":           round(y, 6),
                "orientation": "R0",
                "row_idx":     row_idx,
            })
            x_cursor = round(x_cursor + pitch, 6)

        print(
            f"[PLACER]   Row {row_idx} (y={y:.3f}): "
            f"{sum(row_dev_nf.values())} fingers, "
            f"pattern: {' '.join(row_pattern)}"
        )

    return placements


# ---------------------------------------------------------------------------
# Unified Placement Entry Point
# ---------------------------------------------------------------------------
def compute_mirror_placement(
    mirror_logical_devices,
    spice_nets,
    pitch=PITCH_UM,
    max_fingers_per_row=MAX_FINGERS_PER_ROW,
    start_x=0.0,
    nmos_row_0_y=NMOS_ROW_0_Y,
    row_height=ROW_HEIGHT_UM,
    force_mode="auto",
):
    """
    Unified entry point: selects single-row interdigitated
    or multi-row common-centroid placement.

    Args:
        mirror_logical_devices: list of logical device dicts
        spice_nets:             parsed SPICE nets
        force_mode:             "auto" | "interdigitated" | "common_centroid"
            auto             — pick based on total fingers vs max_fingers_per_row
            interdigitated   — always single-row ABAB pattern
            common_centroid  — always multi-row 2D symmetric
        (remaining args: layout grid parameters)

    Returns:
        list of placement dicts
    """
    if not mirror_logical_devices:
        return []

    # Count total fingers
    total_fingers = 0
    for dev in mirror_logical_devices:
        fingers = dev.get("_fingers", [dev["id"]])
        total_fingers += len(fingers)

    # Decide mode
    if force_mode == "common_centroid":
        use_multi_row = True
        reason = f"forced common_centroid (total={total_fingers})"
    elif force_mode == "interdigitated":
        use_multi_row = False
        reason = f"forced interdigitated (total={total_fingers})"
    else:  # auto
        use_multi_row = total_fingers > max_fingers_per_row
        if use_multi_row:
            reason = f"auto multi-row (total={total_fingers} > max={max_fingers_per_row})"
        else:
            reason = f"auto single-row (total={total_fingers} <= max={max_fingers_per_row})"

    print(f"[PLACER] compute_mirror_placement: {reason}")

    if use_multi_row:
        # When forced common centroid, ensure at least 2 rows
        effective_max = max_fingers_per_row
        if force_mode == "common_centroid" and total_fingers <= max_fingers_per_row:
            # Halve the max so the fingers spread across 2+ rows
            effective_max = max(total_fingers // 2, 4)
            print(f"[PLACER]   Forcing multi-row: effective max_per_row={effective_max}")
        return compute_common_centroid_placement(
            mirror_logical_devices,
            spice_nets,
            pitch=pitch,
            max_fingers_per_row=effective_max,
            start_x=start_x,
            nmos_row_0_y=nmos_row_0_y,
            row_height=row_height,
        )
    else:
        return compute_interdigitated_placement(
            mirror_logical_devices,
            spice_nets,
            pitch=pitch,
            start_x=start_x,
            row_y=nmos_row_0_y,
        )


# ---------------------------------------------------------------------------
# Convert placements to CMD blocks
# ---------------------------------------------------------------------------
def placements_to_cmd_blocks(placements):
    """
    Convert placement list to [CMD] block dicts ready for the orchestrator.

    Args:
        placements: list of dicts from compute_*_placement()

    Returns:
        list of cmd dicts:
          [{"action": "move", "device": "MM2_f1", "x": 0.0, "y": 0.0}, ...]
    """
    cmds = []
    for p in placements:
        cmds.append({
            "action": "move",
            "device": p["finger_id"],
            "x":      p["x"],
            "y":      p["y"],
        })
    return cmds


def placements_to_cmd_strings(placements):
    """
    Convert placement list to [CMD] block strings for LLM output or logging.

    Args:
        placements: list of dicts from compute_*_placement()

    Returns:
        list of strings like:
          '[CMD]{"action":"move","device":"MM2_f1","x":0.0,"y":0.0}[/CMD]'
    """
    import json
    strings = []
    for p in placements:
        cmd = {
            "action": "move",
            "device": p["finger_id"],
            "x":      p["x"],
            "y":      p["y"],
        }
        strings.append(f"[CMD]{json.dumps(cmd)}[/CMD]")
    return strings


# ---------------------------------------------------------------------------
# Decision: needs common-centroid / interdigitation?
# ---------------------------------------------------------------------------
def needs_interdigitation(
    mirror_logical_devices,
    spice_nets=None,
    max_fingers_per_row=MAX_FINGERS_PER_ROW,
):
    """
    Decide if a mirror group needs interdigitated placement.

    Returns True if ANY of:
      - Total fingers >= 4 (any non-trivial mirror)
      - Not all devices have the same nf (ratio mirror)
      - Total fingers > max_fingers_per_row (needs multi-row)

    This replaces the old needs_common_centroid() which had a
    bug (used > instead of >=) and missed ratio mirrors.

    Args:
        mirror_logical_devices: list of logical device dicts
        spice_nets:             parsed SPICE nets (unused, kept for API compat)
        max_fingers_per_row:    threshold

    Returns:
        bool
    """
    if not mirror_logical_devices:
        return False

    nf_values: List[int]  = []
    total_fingers          = 0

    for dev in mirror_logical_devices:
        fingers = dev.get("_fingers", [dev["id"]])
        nf      = len(fingers)
        nf_values.append(nf)
        total_fingers += nf

    # Ratio mirror: different nf values
    is_ratio = len(set(nf_values)) > 1

    if is_ratio:
        print(
            f"[PLACER] needs_interdigitation=True: "
            f"ratio mirror nf_values={nf_values}"
        )
        return True

    if total_fingers > max_fingers_per_row:
        print(
            f"[PLACER] needs_interdigitation=True: "
            f"total_fingers={total_fingers} > {max_fingers_per_row}"
        )
        return True

    # Even 1:1 mirrors benefit from interdigitation when nf >= 4
    if total_fingers >= 4:
        print(
            f"[PLACER] needs_interdigitation=True: "
            f"total_fingers={total_fingers} >= 4"
        )
        return True

    return False


def needs_common_centroid(
    mirror_logical_devices,
    spice_nets=None,
    max_fingers_per_row=MAX_FINGERS_PER_ROW,
):
    """
    Backward-compatible wrapper. Returns True if multi-row is needed.
    For single-row interdigitation decisions, use needs_interdigitation().
    """
    if not mirror_logical_devices:
        return False

    total_fingers = 0
    for dev in mirror_logical_devices:
        fingers = dev.get("_fingers", [dev["id"]])
        total_fingers += len(fingers)

    if total_fingers > max_fingers_per_row:
        print(
            f"[PLACER] needs_common_centroid=True: "
            f"total_fingers={total_fingers} > {max_fingers_per_row}"
        )
        return True

    return False


# ---------------------------------------------------------------------------
# Helper: build_placement_context
# ---------------------------------------------------------------------------
def build_placement_context(
    nodes,
    constraints_text="",
    terminal_nets=None,
    edges=None,
    spice_nets=None,
):
    """
    Build a rich context string for the Placement Specialist LLM.

    Includes:
      - Multi-row NMOS row reporting
      - Common-centroid / interdigitation recommendation when applicable
      - Net adjacency and routing cost tables
      - Recommended placement order

    FIXES:
      - Recommendation now triggers on >= (not >) for max_fingers
      - Ratio mirrors always trigger recommendation
      - nf is layout finger count only (nfin not multiplied)
    """
    lines = ["=" * 60, "CURRENT LAYOUT INVENTORY", "=" * 60]

    # ── Separate active vs dummy ─────────────────────────────────────────
    active_devices = sorted(
        [n for n in nodes if not n.get("is_dummy")],
        key=lambda n: (n.get("type", ""), n["id"]),
    )
    dummy_devices = sorted(
        [n for n in nodes if n.get("is_dummy")],
        key=lambda n: n["id"],
    )
    active_ids = [n["id"] for n in active_devices]
    dummy_ids  = [n["id"] for n in dummy_devices]

    # ── Compute actual row y-values from data ────────────────────────────
    pmos_ys = sorted(set(
        round(n["geometry"]["y"], 6)
        for n in active_devices
        if str(n.get("type", "")).lower().startswith("p")
    ))
    nmos_ys = sorted(set(
        round(n["geometry"]["y"], 6)
        for n in active_devices
        if str(n.get("type", "")).lower().startswith("n")
    ))

    # ── Conservation anchors ─────────────────────────────────────────────
    lines.append(f"\nTOTAL DEVICE COUNT : {len(nodes)}")
    lines.append(
        f"IMMUTABLE TRANSISTORS ({len(active_ids)}): "
        + ", ".join(active_ids)
    )
    lines.append(
        f"FLUID DUMMIES ({len(dummy_ids)}): "
        + (", ".join(dummy_ids) if dummy_ids else "none")
    )
    lines.append("")

    # ── Row y-value reference ────────────────────────────────────────────
    lines.append(
        "ROW Y-VALUE REFERENCE "
        "(copy these exactly into move CMDs):"
    )

    if pmos_ys:
        for y in pmos_ys:
            devs = [
                n["id"] for n in active_devices
                if str(n.get("type", "")).lower().startswith("p")
                and abs(n["geometry"]["y"] - y) < 1e-4
            ]
            lines.append(
                f"  PMOS row  y = {y:.6f}   "
                f"(devices: {', '.join(devs)})"
            )
    else:
        lines.append("  PMOS row  — no PMOS devices found")

    if nmos_ys:
        for y in nmos_ys:
            devs = [
                n["id"] for n in active_devices
                if str(n.get("type", "")).lower().startswith("n")
                and abs(n["geometry"]["y"] - y) < 1e-4
            ]
            lines.append(
                f"  NMOS row  y = {y:.6f}   "
                f"(devices: {', '.join(devs)})"
            )
    else:
        lines.append("  NMOS row  — no NMOS devices found")

    lines.append("")

    # ── Interdigitation / common-centroid recommendation ─────────────────
    # Count ACTUAL layout fingers (not nf*nfin)
    # Each physical finger node counts as 1 finger
    nmos_active = [
        n for n in active_devices
        if str(n.get("type", "")).lower().startswith("n")
    ]
    total_nmos_fingers = len(nmos_active)

    # Check for ratio mirror: group by logical device base and compare nf
    from ai_agent.finger_grouping import group_fingers
    finger_groups = group_fingers(nmos_active)
    nf_per_logical = {
        base: len(fingers)
        for base, fingers in finger_groups.items()
    }
    nf_values_set    = set(nf_per_logical.values())
    is_ratio_mirror  = len(nf_values_set) > 1 and len(nf_per_logical) > 1
    has_mirror        = (
        constraints_text
        and "MIRROR" in constraints_text.upper()
    )

    if has_mirror and (
        total_nmos_fingers >= MAX_FINGERS_PER_ROW
        or is_ratio_mirror
        or len(nf_per_logical) >= 2
    ):
        lines.append("*** INTERDIGITATION / COMMON-CENTROID RECOMMENDATION ***")
        lines.append(
            f"  Total NMOS fingers = {total_nmos_fingers}"
        )
        lines.append(
            f"  Logical devices: "
            + ", ".join(f"{k}(nf={v})" for k, v in nf_per_logical.items())
        )

        if is_ratio_mirror:
            lines.append(
                "  RATIO MIRROR DETECTED — interdigitation REQUIRED"
            )

        if total_nmos_fingers > MAX_FINGERS_PER_ROW:
            lines.append(
                f"  Total fingers {total_nmos_fingers} > "
                f"{MAX_FINGERS_PER_ROW} max per row."
            )
            lines.append(
                "  USE MULTI-ROW COMMON-CENTROID PLACEMENT:"
            )
            num_rows_needed = max(
                2,
                math.ceil(total_nmos_fingers / MAX_FINGERS_PER_ROW)
            )
            for r in range(num_rows_needed):
                y = NMOS_ROW_0_Y + r * ROW_HEIGHT_UM
                lines.append(f"    NMOS row {r}: y = {y:.3f}")
        else:
            lines.append(
                "  USE SINGLE-ROW INTERDIGITATED PLACEMENT:"
            )
            lines.append(
                "  Interleave fingers of all mirror devices symmetrically."
            )
            lines.append(
                "  Pattern example: M2 M0 M1 M0 M0 M1 M0 M2 | mirror"
            )

        lines.append("")

    # ── Per-device inventory ─────────────────────────────────────────────
    def _fmt(n):
        geo   = n.get("geometry",   {})
        elec  = n.get("electrical", {})
        nets  = (terminal_nets or {}).get(n["id"], {})
        net_str = (
            " | ".join(
                f"{t}={v}"
                for t, v in sorted(nets.items())
                if v
            )
            if nets else ""
        )
        return (
            f"  {n['id']:<14} type={n.get('type','?'):<5}  "
            f"x={geo.get('x', 0):>8.4f}  "
            f"y={geo.get('y', 0):>9.6f}  "
            f"nf={elec.get('nf', 1)}  "
            + (f"nets=[{net_str}]" if net_str else "")
        )

    lines.append(
        "PMOS DEVICES (must stay in PMOS row — keep their y value):"
    )
    pmos_nodes = [
        n for n in active_devices
        if str(n.get("type", "")).lower().startswith("p")
    ]
    if pmos_nodes:
        lines.extend(_fmt(n) for n in pmos_nodes)
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append(
        "NMOS DEVICES (must stay in NMOS rows — "
        "use interdigitated or common-centroid for mirrors):"
    )
    nmos_nodes = [
        n for n in active_devices
        if str(n.get("type", "")).lower().startswith("n")
    ]
    if nmos_nodes:
        nmos_by_row = defaultdict(list)
        for n in nmos_nodes:
            y = round(float(n["geometry"]["y"]), 3)
            nmos_by_row[y].append(n)
        for y_val in sorted(nmos_by_row.keys()):
            lines.append(f"  [NMOS row y={y_val:.3f}]")
            for n in nmos_by_row[y_val]:
                lines.append(_fmt(n))
    else:
        lines.append("  (none)")
    lines.append("")

    if dummy_devices:
        lines.append("EXISTING DUMMIES (place at row edges only):")
        lines.extend(_fmt(n) for n in dummy_devices)
        lines.append("")

    # ── Net adjacency table ──────────────────────────────────────────────
    if terminal_nets:
        net_to_devs: dict = {}
        supply = {"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS"}
        for dev_id, nets in terminal_nets.items():
            for _, net_name in nets.items():
                if net_name and net_name.upper() not in supply:
                    net_to_devs.setdefault(net_name, set()).add(dev_id)

        shared_nets = {
            net: devs
            for net, devs in net_to_devs.items()
            if len(devs) >= 2
        }
        if shared_nets:
            lines.append(
                "NET ADJACENCY TABLE "
                "(devices sharing a net — place these close):"
            )
            pos_x = {n["id"]: n["geometry"].get("x", 0) for n in nodes}
            for net_name in sorted(shared_nets):
                devs = sorted(shared_nets[net_name])
                xs   = [pos_x.get(d, 0) for d in devs]
                span = (
                    round(max(xs) - min(xs), 4)
                    if len(xs) > 1 else 0
                )
                lines.append(
                    f"  {net_name:<20} -> "
                    f"{', '.join(devs):<40} "
                    f"(current x-span: {span:.4f} um)"
                )
            lines.append("")

    # ── Routing cost summary ─────────────────────────────────────────────
    if terminal_nets and nodes:
        pos_x     = {n["id"]: n["geometry"].get("x", 0) for n in nodes}
        net_spans = {}
        supply    = {"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS"}
        for dev_id, nets in terminal_nets.items():
            for _, net_name in nets.items():
                if net_name and net_name.upper() not in supply:
                    net_spans.setdefault(net_name, []).append(
                        pos_x.get(dev_id, 0)
                    )

        worst = sorted(
            [
                (net, max(xs) - min(xs))
                for net, xs in net_spans.items()
                if len(xs) >= 2
            ],
            key=lambda t: -t[1],
        )[:5]

        if worst:
            lines.append(
                "ROUTING COST — worst 5 nets by x-span "
                "(reduce these spans):"
            )
            for net_name, span in worst:
                lines.append(
                    f"  {net_name:<20} current span = {span:.4f} um"
                )
            lines.append("")

    # ── Topology constraints ─────────────────────────────────────────────
    if constraints_text:
        lines.append("=" * 60)
        lines.append(
            "TOPOLOGY CONSTRAINTS "
            "(from Topology Analyst — Stage 1)"
        )
        lines.append("=" * 60)
        lines.append(constraints_text)
        lines.append("")

    # ── Recommended placement order ──────────────────────────────────────
    if terminal_nets and nodes:
        supply = {"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS"}

        def _net_score(dev_a, dev_b):
            nets_a = set(
                v for v in (terminal_nets.get(dev_a) or {}).values()
                if v and v.upper() not in supply
            )
            nets_b = set(
                v for v in (terminal_nets.get(dev_b) or {}).values()
                if v and v.upper() not in supply
            )
            return len(nets_a & nets_b)

        def _sort_by_net_sharing(dev_list):
            if len(dev_list) <= 1:
                return dev_list
            ids    = [n["id"] for n in dev_list]
            scores = {
                d: sum(_net_score(d, o) for o in ids if o != d)
                for d in ids
            }
            return sorted(
                dev_list,
                key=lambda n: -scores.get(n["id"], 0)
            )

        pmos_sorted = _sort_by_net_sharing(pmos_nodes)
        nmos_sorted = _sort_by_net_sharing(nmos_nodes)

        lines.append(
            "RECOMMENDED PLACEMENT ORDER "
            "(for LLM — prefer interdigitated over grouped):"
        )
        if pmos_sorted:
            lines.append(
                "  PMOS row (left->right): "
                + " | ".join(n["id"] for n in pmos_sorted)
            )
        if nmos_sorted:
            lines.append(
                "  NMOS — USE INTERDIGITATED pattern from "
                "recommendation above (not simple left-to-right)"
            )
        lines.append("")

    # ── Final instruction ────────────────────────────────────────────────
    lines.append("=" * 60)
    lines.append("INSTRUCTION:")
    lines.append(
        "  1. Check INTERDIGITATION RECOMMENDATION above."
    )
    lines.append(
        "  2. If mirror detected: MUST interdigitate (never group fingers)."
    )
    lines.append(
        "  3. If multi-row needed: use ABBA pattern across 2+ rows."
    )
    lines.append(
        "  4. Output [CMD] blocks to place EVERY device in the inventory."
    )
    lines.append(
        "  5. Copy y values from ROW Y-VALUE REFERENCE above."
    )
    lines.append(
        "  6. No two devices in the same row may share an x-value."
    )
    lines.append(
        "  7. Paired devices must be interdigitated, not grouped."
    )
    lines.append("=" * 60)

    return "\n".join(lines)
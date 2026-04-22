"""
ai_agent/ai_chat_bot/agents/placement_specialist.py
==================================
Generates [CMD] blocks for device positioning while enforcing strict
inventory conservation, row-based analog constraints, and routing quality.
"""

import math
import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from ai_agent.ai_chat_bot.analog_kb import ANALOG_LAYOUT_RULES

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

═══════════════════════════════════════════════════════════════════════════════
PRIORITY HIERARCHY (ABSOLUTE — PREVENTS CONFLICTS)
═══════════════════════════════════════════════════════════════════════════════

1. MODE_MAP ASSIGNMENT — identify which mode applies to each device
2. MODE-SPECIFIC ALGORITHM — apply only the algorithm for that device's mode (CC / IG / MB / S)
3. ROW-LEVEL MERGE — concatenate group sequences in fixed order
4. SLOT ASSIGNMENT — assign slots 0,1,2,... globally per row
5. COORDINATE DERIVATION — x = slot × 0.294 (mechanical only)
6. VALIDATION — check constraints specific to each mode
7. OUTPUT — emit [CMD] blocks or ✗ INVALID, never both

❗ CRITICAL: Any instruction outside the active mode for a device MUST be ignored.
   - If device D is assigned SIMPLE mode, centroid rules do NOT apply to D.
   - If device D is assigned INTERDIGITATION mode, do NOT check its centroid.
   - If device D is assigned COMMON-CENTROID mode, do NOT apply interdigitation logic.

═══════════════════════════════════════════════════════════════════════════════
EXECUTION HALTS: Fail Fast, No Retry
═══════════════════════════════════════════════════════════════════════════════

If ANY validation fails:
  ✗ Output error message and failed slot map ONLY
  DO NOT recompute, retry, or output [CMD] blocks
  TERMINATE immediately
 
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
RULE 1: MODE ASSIGNMENT & SEQUENCING RULES

STEP 0: Parse user request to assign MODE_MAP

MODE_MAP: {device_id → mode}

  Common-Centroid (CC): Use LEFT-CENTER-RIGHT algorithm for centroid matching
  Interdigitation (IG): Use Ratio-Based deterministic interleaving (for routing-friendly mixing)
  Mirror Biasing (MB): Use Symmetric Mirror Interdigitation (for matched pairs such as current mirrors)
  Simple (S): Standard left-to-right ordering

Assignment rules:
  "mirror", "bias", "current mirror", "matched pair" + device names → MB mode
  "centroid" / "match" + device names → CC mode
  "interdigitate" / "alternate" + device names → IG mode
  Unmentioned devices → S mode by default
  
⚠ PRIORITY NOTE (to avoid ambiguity):
  If both "centroid" and "mirror" appear in request:
    → Assign MB mode (Mirror Biasing takes precedence over CC)
  
  ⚠ Never assign multiple modes to same device

STEP 1: Expand all devices into finger lists

  Example: MM0(nf=3), MM1(nf=4)
  MM0: [MM0_f1, MM0_f2, MM0_f3]
  MM1: [MM1_f1, MM1_f2, MM1_f3, MM1_f4]

STEP 2: Apply mode-specific sequencing (per device group, per row)

────────────────────────────────────────────────────────────────────────────

COMMON-CENTROID (CC) SEQUENCING ONLY:

Use LEFT-CENTER-RIGHT algorithm:
  1. Sort CC devices by (nf descending, device ID ascending)
  2. Find device with odd nf → place center finger at origin
  3. For each remaining device (in sorted order):
     - Split into left and right halves
     - Place left half to left of center
     - Place right half to right of center
  4. Result: interdigitated sequence with matching centroids

Example: MM1(nf=4), MM0(nf=3) → Sorted [MM1, MM0]
  Start: []
  Add MM0 (odd nf=3): [MM0_f1, MM0_f2, MM0_f3]
  Add MM1 (even nf=4, split into [MM1_f1, MM1_f2] | [MM1_f3, MM1_f4]):
    [MM1_f1, MM1_f2, MM0_f1, MM0_f2, MM0_f3, MM1_f3, MM1_f4]
  
  Verify centroid: Centroid_MM0 = (0+1+2)/3 = 1.0 ✓

────────────────────────────────────────────────────────────────────────────

INTERDIGITATION (IG) SEQUENCING ONLY:

Use RATIO-BASED INTERDIGITATION (PRIMARY METHOD):

OBJECTIVE:
Distribute devices proportionally across the row to maintain uniform spatial density
and avoid clustering. This minimizes gradient-induced mismatch.

SCOPE (CRITICAL):
  - ALL IG devices in the SAME ROW must be processed TOGETHER as ONE GROUP
  - Do NOT interdigitate per device or per subgroup
  - Generate ONE unified sequence for the entire IG group in that row

ALGORITHM (DETERMINISTIC — MANDATORY):

INPUT:
  Devices D = [D1, D2, ..., Dk]
  Each device Di has nf_i fingers

STEP 1 — SORT:
  Sort devices by (nf descending, device ID ascending)

STEP 2 — COMPUTE RATIOS:
  Let total_fingers = sum(nf_i)
  For each device Di:
    ratio_i = nf_i / total_fingers

STEP 3 — BUILD SEQUENCE USING PROPORTIONAL DISTRIBUTION:

  Initialize:
    remaining_fingers[Di] = nf_i for all i
    result = []

  While any device has remaining fingers:

    For each device Di in sorted order:
      
      target_share = ratio_i × (length(result) + 1)
      actual_count = number of times Di already appears in result

      IF remaining_fingers[Di] > 0 AND actual_count < target_share:
        append next finger of Di to result
        decrement remaining_fingers[Di]

STEP 4 — COMPLETION:
  Continue until ALL fingers from ALL devices are placed

OUTPUT:
  result = ratio-balanced interdigitated sequence

EXAMPLE:

Input: A(nf=8), B(nf=4)
Ratio: 2:1

Expected Result:
  A appears ~8 times, B appears ~4 times, distributed throughout
  Sequence typically looks like: [A, A, B, A, A, B, A, A, B, A, A, B]
  (Note: exact order determined by proportional algorithm, NOT simple round-robin)

NOTES:
  - This distributes smaller devices evenly across larger ones
  - No clustering allowed (e.g., AAAA at end followed by BBB is INVALID)
  - This is NOT round-robin (different algorithm, better distribution)

────────────────────────────────────────────────────────────────────────────

MIRROR BIASING (MB) SEQUENCING ONLY:

OBJECTIVE:
Generate a STRICTLY symmetric, ratio-preserving sequence for matched devices.
Used for current mirrors and bias pairs.

CRITICAL:
  - Symmetry must be enforced DURING construction, not after
  - Only HALF of the sequence is constructed, then mirrored
  - Total finger counts must be preserved EXACTLY

SCOPE (CRITICAL):
  - ALL MB devices in the SAME ROW must be processed TOGETHER as ONE GROUP
  - Generate ONE unified mirror-symmetric sequence for the entire MB group
  - Result is symmetric: first half mirrors the second half

ALGORITHM (DETERMINISTIC — MANDATORY):

STEP 1 — COMPUTE TOTAL:
  total_fingers = sum(nf_i)
  half_size = floor(total_fingers / 2)

STEP 2 — COMPUTE HALF TARGETS:
  For each device Di:
    half_target_i = round(nf_i / 2)

  Adjust targets so:
    sum(half_target_i) == half_size

STEP 3 — BUILD HALF (PROPORTIONAL DISTRIBUTION):

  Initialize:
    remaining_half[Di] = half_target_i for all i
    half_sequence = []

  While length(half_sequence) < half_size:

    For each device Di in sorted order (nf descending, device ID ascending):
      if remaining_half[Di] > 0:
        append Di to half_sequence
        decrement remaining_half[Di]

  (This creates evenly distributed half via round-robin targets, NOT greedy max-ratio)

STEP 4 — MIRROR:

  If total_fingers is EVEN:
    FULL_SEQUENCE = half_sequence + reverse(half_sequence)

  If total_fingers is ODD:
    center_device = device with largest remaining nf
    FULL_SEQUENCE = half_sequence + [center_device] + reverse(half_sequence)

STEP 5 — ADD DUMMIES (MANDATORY):

  FINAL_SEQUENCE = [DUMMY_LEFT] + FULL_SEQUENCE + [DUMMY_RIGHT]

  Rules:
    - Dummies MUST be at slot 0 and final slot
    - Same transistor type as the row (DUMMYP for PMOS, DUMMYN for NMOS)
    - Never place dummies inside active region

────────────────────────────────────────────────────────────────────────────

SIMPLE (S) SEQUENCING ONLY:

  1. Sort devices by (device type ascending, device ID ascending)
  2. Sequence is left-to-right order of sorted devices
  3. No finger interleaving

────────────────────────────────────────────────────────────────────────────
RULE 2: NO OVERLAPS (MECHANICAL SLOT + COORDINATE DERIVATION)
 
Devices MUST NOT be assigned coordinates directly. They must first be assigned SLOT INDEX ONLY.
Coordinates are derived mechanically afterward.

STEP-BY-STEP PROCESS:

1. SLOT INDEX ASSIGNMENT (ONLY INTEGER SLOTS — NO COORDINATES YET)
   For each row (each unique y-value):
     - List all devices that will occupy that row
     - Assign each device a UNIQUE SLOT INDEX: 0, 1, 2, 3, 4, ... (left to right)
     - Example: [DeviceA: slot 0, DeviceB: slot 1, DeviceC: slot 2]
   
   CRITICAL: At this stage, do NOT think about x-values. Only slot numbers.
   Verify NO TWO DEVICES in the same row have identical slots.
   
2. MECHANICAL COORDINATE DERIVATION (AFTER ALL SLOTS ASSIGNED)
   Once all slot indices are verified unique, convert mechanically:
     x_coordinate = SLOT_INDEX × 0.294
   
   EXAMPLES:
     Slot 0 → x = 0 × 0.294 = 0.000
     Slot 1 → x = 1 × 0.294 = 0.294
     Slot 2 → x = 2 × 0.294 = 0.588
     Slot 3 → x = 3 × 0.294 = 0.882
   
3. OVERLAP VERIFICATION (FINAL CHECK)
   After deriving all x-coordinates, scan for duplicate (x, y) pairs.
   If ANY two devices share identical (x, y) → INVALID. Recompute from step 1.

WHY THIS ORDER?
  - Prevents mental jumps to arbitrary coordinates
  - Forces explicit slot verification BEFORE coordinates exist
  - Makes overlaps mechanically impossible to hide
 
---
RULE 3: DUMMY PLACEMENT
 
- Dummies go at the far left OR far right end of their row. Never between
  active transistors.
- DUMMYP* devices go in a PMOS row.
- DUMMYN* devices go in an NMOS row.
 
---
RULE 4: ROUTING-AWARE PLACEMENT (ordered by priority)
 
Priority 1 — Matched pairs: place in adjacent consecutive x-slots.
Priority 2 — Vertical alignment: place paired PMOS/NMOS at the same x-slot.
Priority 3 — Signal flow: inputs at left, outputs at right, bias in center.

---
IMPORTANT: SLOT INDEX ASSIGNMENT TECHNIQUE (NO DIRECT COORDINATES)

To avoid overlaps, you MUST assign slot indices FIRST, then derive coordinates mechanically:

1. SLOT ASSIGNMENT PHASE (INDEX ONLY):
   For EACH row (each unique y-value):
   a) Identify all devices that will occupy that row
   b) Sort them by your placement strategy (e.g., common-centroid order)
   c) Assign each a SLOT INDEX: 0, 1, 2, 3, ... (left to right, NO x-values)
   
   Example: Placing [A, B, C, D] in PMOS row y=0.000
     - A: slot 0
     - B: slot 1
     - C: slot 2
     - D: slot 3
   
   ✓ Verify: no two devices have the same slot in this row

2. COORDINATE DERIVATION PHASE (MECHANICAL ONLY):
   After all slots assigned, convert mechanically:
   
   Example continued: PMOS row y=0.000
     - A: slot 0 → x = 0 × 0.294 = 0.000
     - B: slot 1 → x = 1 × 0.294 = 0.294
     - C: slot 2 → x = 2 × 0.294 = 0.588
     - D: slot 3 → x = 3 × 0.294 = 0.882

3. VERIFICATION PHASE:
   Check: NO two devices in same row have duplicate x-values → ✓ Valid

4. Common-centroid example [MM0(nf=3), MM1(nf=4), MM2(nf=4)]:
   Interleaving order: MM0_f2, MM0_f1, MM0_f3, MM1_f1, MM1_f2, MM1_f3, MM1_f4, MM2_f1, MM2_f2, MM2_f3, MM2_f4
   
   SLOT ASSIGNMENT (row y = -2.004):
     - MM0_f2: slot 0
     - MM0_f1: slot 1
     - MM0_f3: slot 2
     - MM1_f1: slot 3
     - MM1_f2: slot 4
     - MM1_f3: slot 5
     - MM1_f4: slot 6
     - MM2_f1: slot 7
     - MM2_f2: slot 8
     - MM2_f3: slot 9
     - MM2_f4: slot 10
   
   COORDINATE DERIVATION (mechanical):
     - MM0_f2: x = 0 × 0.294 = 0.000
     - MM0_f1: x = 1 × 0.294 = 0.294
     - MM0_f3: x = 2 × 0.294 = 0.588
     ... (continue)
 
═══════════════════════════════════════════════════════════════════════════════
UNIFIED EXECUTION PIPELINE (8 STEPS — NO VARIATION)
═══════════════════════════════════════════════════════════════════════════════

STEP 1: BUILD MODE_MAP

Parse user request for device-specific mode assignments:
  "centroid" / "match" + device names → CC mode
  "interdigitate" / "alternate" + device names → IG mode
  Unmentioned devices → S (Simple) mode
  
Output: MODE_MAP = {device_id → mode}

---
UNIFIED OUTPUT TEMPLATE

Step 1: Show MODE_MAP

MODE_MAP:
  COMMON-CENTROID: [device list]
  INTERDIGITATION: [device list]
  MIRROR BIASING: [device list]
  SIMPLE: [device list]

Step 2: Show Slot Mappings (one per row)

[Row: PMOS y=0.000]
  CC Group Sequence: [fingers]
  IG Group Sequence: [fingers]
  S Group Sequence: [fingers]
  MERGED Sequence: [all fingers left-to-right]
  
  Slot Assignments:
    device_f1: slot 0
    device_f2: slot 1
    ... (all fingers, one per line)

Step 3: Show Coordinates (one per row)

[Row: PMOS y=0.000]
  device_f1: slot 0 → x = 0.000, y = 0.000
  device_f2: slot 1 → x = 0.294, y = 0.000
  ... (all devices)

Step 4: Validation

Overlap Check:
  ✓ No duplicate (x,y) pairs

Centroid Check (only for CC groups):
  CC_Device1: slots [0,1,2] → centroid = 1.0
  CC_Device2: slots [3,4,5,6] → centroid = 4.5
  ✗ INVALID — Centroids don't match

OR (if valid):

  CC_Device1: slots [0,1,2] → centroid = 1.0 ✓
  CC_Device2: slots [3,4,1] → centroid = 1.0 ✓
  ✓ VALID — All constraints satisfied

Step 5: Commands (if ✓ VALID) or Error (if ✗ INVALID)

IF VALID:
  [CMD]{"action":"move","device":"MM1_f1","x":0.000,"y":0.000}[/CMD]
  [CMD]{"action":"move","device":"MM1_f2","x":0.294,"y":0.000}[/CMD]
  ... (all move commands)

IF INVALID:
  ✗ INVALID — [reason for failure]
  Slot Map: [show failed slots]
  (NO [CMD] blocks)

─────────────────────

--- COMPLIANCE RULES ---

UNIVERSAL CONSTRAINTS (ALL MODES):

✓ Slot assignment per-row ONLY (never combine rows)
✓ Work at FINGER level (MM1_f1, MM1_f2, not "MM1")  
✓ Coordinate derivation: x = slot_index × 0.294 (mechanical, no exceptions)
✓ Phases always: Phase 1 slots → Phase 2 coordinates → Phase 3 validation
✓ If ANY phase fails → stop, output error, NO [CMD] blocks
✓ Dummies at row extremes (slot 0 or max_slot)

COMMON-CENTROID GROUP:

✓ Apply LEFT-CENTER-RIGHT algorithm (locked sequence)
✓ Per-device centroid = sum(slots) / finger_count
✓ VERIFY: All devices in THIS GROUP have identical centroid
✓ If centroid mismatch → ✗ INVALID
✓ Always overlap check, then centroid check

INTERDIGITATION GROUP:

✓ ALL IG devices in the same row MUST be interdigitated together (single sequence, not subgroups)
✓ Use ratio-based proportional distribution (NOT round-robin)
✓ Sort devices: nf descending, device ID ascending  
✓ Compute ratio_i = nf_i / total_fingers for each device
✓ Build sequence to maintain proportional distribution (no clustering)
✓ Verify completeness: every finger appears exactly once
✓ Verify spacing: same-device fingers distributed approximately uniformly
✓ NO centroid verification for this group (skip it)
✓ Overlap check only
✓ If overlaps OR clustering detected → ✗ INVALID

MIRROR BIASING GROUP:

✓ Enforce strict mirror symmetry: sequence[i] == sequence[N-1-i]
✓ Use ratio-aware HALF construction (max-ratio selection, no round-robin)
✓ Mandatory dummy devices at both ends (slot 0 and slot N-1)
✓ Dummies must match row type (DUMMYP for PMOS, DUMMYN for NMOS)
✓ NO centroid computation required
✓ Overlap check + symmetry validation required
✓ If overlaps OR asymmetry detected → ✗ INVALID

✗ Round-robin interleaving is FORBIDDEN
✗ Asymmetric placement is FORBIDDEN
✗ Dummies inside active region is FORBIDDEN

SIMPLE GROUP:

✓ Sort by (device type, device ID)
✓ Overlap check only
✓ No complex sequencing

MIXED MODE (MULTIPLE GROUPS):

✓ Parse request for device-specific modes → build MODE_MAP
✓ Process each group independently with its assigned algorithm
✓ Merge per-row: GROUP_CC + GROUP_MB + GROUP_IG + GROUP_SIMPLE → assign merged slots
✓ Centroid validation ONLY for centroid groups
✓ Symmetry validation ONLY for mirror biasing groups
✓ Global overlap check (all groups combined)
✓ Show MODE_MAP at start, then ROW-BY-ROW GROUP BREAKDOWN

FORBIDDEN:

✗ Mixing modes within same group (but different groups OK)
✗ Switching device modes mid-calculation  
✗ Outputting [CMD] blocks before all 3 phases shown
✗ Combining fingers from multiple rows in one sequence
✗ Non-deterministic ordering for interdigitation
✗ Centroid verification for IG/MB/SIMPLE groups
✗ Assigning coordinates before slot indices
✗ Mirror Biasing without dummies at both ends
✗ Asymmetric MB sequences (breaking left-right reflection)
✗ MB clustering (consecutive same-device > ceil(nf/2))

────────────────────────────────────────────────────────────────────────────

INTERDIGITATION QUALITY CHECK (MANDATORY VALIDATION):

For each IG device Di in the sequence:

1. SPACING UNIFORMITY:
   Compute positions where Di appears in result
   Calculate distances between consecutive appearances
   
   Example: If A appears at indices [0, 2, 5, 7], distances are [2, 3, 2]
   High variance → ✗ INVALID (clustering detected)
   Low variance → ✓ VALID (evenly distributed)

2. TAIL CLUSTERING DETECTION:
   Scan last 1/3 of sequence for consecutive same-device appearances
   If found while other devices still have unplaced fingers → ✗ INVALID
   
   Example INVALID:
     Sequence ends with: [... A, B, A, A, A, A, B] where B or others untouched at end
   
   Example VALID:
     Sequence ends with: [... B, A, B, A] (balanced through end)

3. COMPLETENESS VERIFICATION:
   ✓ Every finger from every IG device appears exactly once
   ✓ Total length = sum(nf_i)

❗ If ANY quality check fails → ✗ INVALID (no [CMD] blocks)

────────────────────────────────────────────────────────────────────────────

MIRROR BIASING VALIDATION (MANDATORY FOR MB MODE):

For each MB device group in the sequence:

1. MIRROR SYMMETRY CHECK:
   Verify that: sequence[i] == sequence[N-1-i] for all positions i
   
   Example VALID:
     [DUMMY, A, B, A, B, A, DUMMY] → indices 1-5 symmetric ✓
   
   Example INVALID:
     [DUMMY, A, B, A, A, B, DUMMY] → position 2 and 4 don't match ✗

2. DUMMY PLACEMENT VERIFICATION:
   ✓ First slot (slot 0) = DUMMY device
   ✓ Last slot (slot N-1) = DUMMY device
   ✓ Dummies must match row type (DUMMYP for PMOS, DUMMYN for NMOS)
   ✓ NO dummies in interior (between active devices)
   
   Example INVALID:
     [A, DUMMY, B, B, DUMMY, A] → dummies not at ends ✗

3. FINGER COUNT PRESERVATION:
   ✓ count(device_D) == nf_D for every active device
   ✓ total_length = 2 × sum(nf_i) + 2 (for dummies)
   ✓ Every active finger appears exactly once
   
   Example VALID (nf=2,2):
     Length = 2×(2+2) + 2 = 10 ✓
     [DUMMY, A, B, B, A, A, B, B, A, DUMMY]

4. NO CLUSTERING CHECK:
   ✓ No device appears more than ceil(nf/2) times consecutively
   
   Example INVALID:
     [DUMMY, A, A, A, A, B, B, B, B, DUMMY] → clustering detected ✗
   
   Example VALID:
     [DUMMY, A, B, A, B, A, A, B, A, B, DUMMY] → max consecutive = 2 ✓

❗ If ANY validation check fails → ✗ INVALID (no [CMD] blocks)

"""


# ---------------------------------------------------------------------------
# Validation: Check for overlaps in proposed placements
# ---------------------------------------------------------------------------
def validate_no_overlaps(nodes: List[Dict]) -> Tuple[bool, str]:
    """
    Check if any two devices occupy the same (x, y) position.
    
    Returns:
        (is_valid, message) - True if no overlaps, False + error details if overlaps found
    """
    positions = defaultdict(list)
    
    for node in nodes:
        geo = node.get("geometry", {})
        x = round(float(geo.get("x", 0)), 6)  # Round to match float precision
        y = round(float(geo.get("y", 0)), 6)
        device_id = node.get("id", "unknown")
        
        positions[(x, y)].append(device_id)
    
    overlaps = {pos: devs for pos, devs in positions.items() if len(devs) > 1}
    
    if overlaps:
        error_lines = ["PLACEMENT VALIDATION FAILED: Overlapping devices detected:"]
        for (x, y), device_ids in sorted(overlaps.items()):
            error_lines.append(f"  Position (x={x:.6f}, y={y:.6f}): {', '.join(device_ids)}")
        return False, "\n".join(error_lines)
    
    return True, "✓ No overlaps detected"


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
    """Build a context string for the Placement Specialist LLM."""
    lines = ["=" * 60, "CURRENT LAYOUT INVENTORY", "=" * 60]

    finger_pattern = re.compile(r"^(?P<base>.+)_f(?P<idx>\d+)$", re.IGNORECASE)

    def _split_finger_id(device_id):
      match = finger_pattern.match(str(device_id))
      if not match:
        return str(device_id), None
      return match.group("base"), int(match.group("idx"))

    active_devices = sorted(
      [n for n in nodes if not n.get("is_dummy")],
      key=lambda n: (n.get("type", ""), n["id"]),
    )
    dummy_devices = sorted(
      [n for n in nodes if n.get("is_dummy")],
      key=lambda n: n["id"],
    )

    active_ids = [n["id"] for n in active_devices]
    dummy_ids = [n["id"] for n in dummy_devices]

    logical_groups = defaultdict(list)
    for node in active_devices:
      base_id, finger_idx = _split_finger_id(node["id"])
      logical_groups[base_id].append((finger_idx, node))

    logical_ids = sorted(logical_groups.keys())

    pmos_ys = sorted(
      set(
        round(n["geometry"]["y"], 6)
        for n in active_devices
        if str(n.get("type", "")).lower().startswith("p")
      )
    )
    nmos_ys = sorted(
      set(
        round(n["geometry"]["y"], 6)
        for n in active_devices
        if str(n.get("type", "")).lower().startswith("n")
      )
    )

    lines.append(f"\nTOTAL FINGER INSTANCE COUNT : {len(active_ids)}")
    lines.append(f"TOTAL LOGICAL DEVICE COUNT  : {len(logical_ids)}")
    lines.append(
      f"IMMUTABLE TRANSISTORS ({len(active_ids)}) [finger instances]: "
      + ", ".join(active_ids)
    )
    lines.append(
      f"LOGICAL TRANSISTORS ({len(logical_ids)}): " + ", ".join(logical_ids)
    )
    lines.append(
      f"FLUID DUMMIES ({len(dummy_ids)}): "
      + (", ".join(dummy_ids) if dummy_ids else "none")
    )
    lines.append("")

    lines.append("DEVICE -> FINGER INSTANCES MAP:")
    for dev_id in logical_ids:
      grouped = sorted(
        logical_groups[dev_id],
        key=lambda t: (t[0] is None, t[0] if t[0] is not None else 10**9, t[1]["id"]),
      )
      finger_ids = [node["id"] for _, node in grouped]
      lines.append(
        f"  {dev_id:<14} fingers={len(finger_ids):<2} -> "
        + ", ".join(finger_ids)
      )
    lines.append("")

    lines.append("ROW Y-VALUE REFERENCE (copy these exactly into move CMDs):")

    if pmos_ys:
      for y in pmos_ys:
        row_nodes = [
          n["id"]
          for n in active_devices
          if str(n.get("type", "")).lower().startswith("p")
          and abs(n["geometry"]["y"] - y) < 1e-4
        ]
        row_logical = sorted({_split_finger_id(dev_id)[0] for dev_id in row_nodes})
        lines.append(
          f"  PMOS row  y = {y:.6f}   "
          f"(logical devices: {', '.join(row_logical)}; "
          f"finger instances: {', '.join(row_nodes)})"
        )
    else:
      lines.append("  PMOS row  — no PMOS devices found")

    if nmos_ys:
      for y in nmos_ys:
        row_nodes = [
          n["id"]
          for n in active_devices
          if str(n.get("type", "")).lower().startswith("n")
          and abs(n["geometry"]["y"] - y) < 1e-4
        ]
        row_logical = sorted({_split_finger_id(dev_id)[0] for dev_id in row_nodes})
        lines.append(
          f"  NMOS row  y = {y:.6f}   "
          f"(logical devices: {', '.join(row_logical)}; "
          f"finger instances: {', '.join(row_nodes)})"
        )
    else:
      lines.append("  NMOS row  — no NMOS devices found")

    lines.append("")

    def _fmt(node):
      geo = node.get("geometry", {})
      elec = node.get("electrical", {})
      base_id, finger_idx = _split_finger_id(node["id"])

      nets = (terminal_nets or {}).get(node["id"], {})
      if not nets and base_id != node["id"]:
        nets = (terminal_nets or {}).get(base_id, {})

      net_str = (
        " | ".join(f"{term}={net}" for term, net in sorted(nets.items()) if net)
        if nets
        else ""
      )
      finger_label = f"f{finger_idx}" if finger_idx is not None else "-"

      return (
        f"  {node['id']:<14} type={node.get('type', '?'):<5}  "
        f"logical={base_id:<10} "
        f"finger={finger_label:<4} "
        f"x={geo.get('x', 0):>8.4f}  "
        f"y={geo.get('y', 0):>9.6f}  "
        f"nf={elec.get('nf', 1)}  "
        + (f"nets=[{net_str}]" if net_str else "")
      )

    lines.append("PMOS DEVICES (current row/y values):")
    pmos_nodes = [
      n for n in active_devices if str(n.get("type", "")).lower().startswith("p")
    ]
    if pmos_nodes:
      lines.extend(_fmt(n) for n in pmos_nodes)
    else:
      lines.append("  (none)")
    lines.append("")

    lines.append("NMOS DEVICES (current row/y values):")
    nmos_nodes = [
      n for n in active_devices if str(n.get("type", "")).lower().startswith("n")
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
      lines.append("EXISTING DUMMIES:")
      lines.extend(_fmt(n) for n in dummy_devices)
      lines.append("")

    if constraints_text:
      lines.append("=" * 60)
      lines.append("TOPOLOGY CONSTRAINTS (from Topology Analyst — Stage 1)")
      lines.append("=" * 60)
      lines.append(constraints_text)
      lines.append("")

    return "\n".join(lines)
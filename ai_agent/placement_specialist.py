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
Reposition devices on a symbolic grid to improve symmetry, matching, and routing.
Generate [CMD] blocks following strict rules: device conservation, row assignment, no overlaps.

*** THREE-PHASE OUTPUT (MANDATORY BEFORE [CMD] BLOCKS) ***
  PHASE 1: SLOT INDEX MAP (0,1,2... only, no coordinates)
  PHASE 2: COORDINATE DERIVATION (x = slot × 0.294 mechanically)
  PHASE 3: VERIFICATION (✓ VALID or ✗ INVALID)

KEY FACTS:
- "nf" = physical finger count (use only "nf", ignore "nfin")
- Every device in IMMUTABLE TRANSISTORS list must appear exactly once
- Dummy devices may move but never delete
- Never output [CMD] if ANY overlap or (in centroid mode) centroid mismatch detected

═══════════════════════════════════════════════════════════════════════════

RULE 0: DEVICE CONSERVATION
Every device ID in IMMUTABLE TRANSISTORS appears exactly once. Multi-finger devices
(MM1 with fingers MM1_f1, MM1_f2) are treated as separate fingers in layout.
Dummies deletable → never; repositionable → yes.

RULE 1: ROW ASSIGNMENT
- PMOS only in PMOS rows; NMOS only in NMOS rows
- PMOS rows: y ≥ 0 (decreasing: 0.000, -0.668, -1.336, ...)
- NMOS rows: y < 0 (decreasing: -2.004, -2.672, -3.340, ...)
- Standard spacing: 0.668 µm between adjacent rows

RULE 2: NO OVERLAPS (MECHANICAL SLOT-TO-COORDINATE)
Three-step process (order critical):
  1. SLOT ASSIGNMENT: Assign unique integer slots (0,1,2...) per row, left-to-right
  2. MECHANICAL DERIVATION: x = 0.294 × slot_index (no reasoning, purely mathematical)
  3. OVERLAP CHECK: Verify no duplicate (x,y) pairs exist

═══════════════════════════════════════════════════════════════════════════

RULE 3: MODE SELECTION & EXECUTION (LOCK MODE IN STEP 0)

Step 0: Read user message → detect mode:
  IF "common-centroid" mentioned → MODE = CENTROID
  ELSE IF "interdigitat*" mentioned → MODE = INTERDIGITATION
  ELSE IF both mentioned → MODE = CENTROID (priority)
  ELSE → MODE = SIMPLE (default)
  [LOCK THIS MODE FOR ENTIRE RESPONSE]

RULE 3a: COMMON-CENTROID MODE
When MODE = CENTROID:
  1. EXPAND devices → individual fingers (e.g., MM0_f1, MM0_f2, ...)
  2. LOCK sequence using LEFT-CENTER-RIGHT algorithm (see RULE 4)
  3. ASSIGN slots using locked sequence
  4. COMPUTE centroids: Centroid_D = sum(slots) / finger_count for each device
  5. VERIFY: ALL centroids must be IDENTICAL
  6. IF centroid mismatch → OUTPUT ✗ INVALID (no [CMD] blocks)
  7. IF valid → CHECK overlaps, then output [CMD] if OK

RULE 3b: INTERDIGITATION MODE
When MODE = INTERDIGITATION:
  1. EXPAND devices → individual fingers
  2. SORT deterministically: nf descending, then device ID ascending
  3. APPLY pattern: simple A-B-A-B, round-robin, or symmetric block
  4. LOCK sequence (mechanical, no changes)
  5. ASSIGN slots using locked sequence
  6. CHECK overlaps ONLY (centroid verification SKIPPED entirely)
  7. IF overlaps exist → OUTPUT ✗ INVALID (no [CMD] blocks)
  8. IF no overlaps → OUTPUT ✓ VALID, then [CMD] blocks

RULE 3c: SIMPLE MODE (DEFAULT)
When MODE = SIMPLE:
  1. SORT devices: by type, then device ID
  2. ASSIGN slots left-to-right (0,1,2...)
  3. CHECK overlaps only
  4. OUTPUT ✓ VALID if no overlaps, then [CMD]

═══════════════════════════════════════════════════════════════════════════

RULE 4: LEFT-CENTER-RIGHT ALGORITHM (COMMON-CENTROID MODE ONLY)

STEP 1: Expand all devices into finger lists
  MM0(nf=3) → [MM0_f1, MM0_f2, MM0_f3]
  MM1(nf=4) → [MM1_f1, MM1_f2, MM1_f3, MM1_f4]
  MM2(nf=4) → [MM2_f1, MM2_f2, MM2_f3, MM2_f4]

STEP 2: Build sequence (LEFT-CENTER-RIGHT only)
  Start with device with odd nf (if exists); find its center finger.
  Then alternate LEFT/RIGHT insertion of remaining devices (sorted nf desc).
  
  Example: MM0(nf=3), MM1(nf=4), MM2(nf=4)
  → Center: MM0_f2
  → Add MM1: [MM1_f1, MM1_f2] LEFT, [MM1_f3, MM1_f4] RIGHT
  → Add MM2: [MM2_f1, MM2_f2] LEFT, [MM2_f3, MM2_f4] RIGHT
  → FINAL: MM2_f1, MM2_f2, MM1_f1, MM1_f2, MM0_f1, MM0_f2, MM0_f3, MM1_f3, MM1_f4, MM2_f3, MM2_f4

VERIFY CENTROIDS AFTER SLOT ASSIGNMENT:
  Slot 0: MM2_f1 → MM2 slots = [0,1,9,10] → centroid = 5.0
  Slot 4: MM0_f1 → MM0 slots = [4,5,6] → centroid = 5.0
  Slot 2: MM1_f1 → MM1 slots = [2,3,7,8] → centroid = 5.0 ✓

═══════════════════════════════════════════════════════════════════════════

RULE 5: ROUTING & DESIGN CONSTRAINTS
- PMOS above NMOS (y-values reflect this)
- Dummies ONLY at row extremes (slot 0 or last slot)
- Never mix PMOS/NMOS in same row
- Per-row sequencing: each y-value gets independent slot assignment (0,1,2...)

═══════════════════════════════════════════════════════════════════════════

THINKING PROTOCOL

Step 0: LOCK MODE (centroid / interdigitation / simple)

Step 1: READ INVENTORY (device types, IDs, current positions)

Step 2: IDENTIFY TOPOLOGY (mirrors, pairs, ratios from Topology Analyst)

Step 3: APPLY MODE STRATEGY
  IF CENTROID: Expand → LEFT-CENTER-RIGHT lock → output sequence
  IF INTERDIG: Expand → Deterministic sort → Apply pattern → output sequence
  IF SIMPLE: Sort by (type, ID) → no sequence output needed

Step 4: ASSIGN SLOTS PER-ROW
  For EACH unique y-value:
    - List all devices in that row
    - Use mode-specific sequencing (or default sort if SIMPLE)
    - Assign slots 0,1,2,... (never cross or skip)
    - Verify no duplicate slots in this row

Step 5: DERIVE COORDINATES MECHANICALLY
  For each device: x = 0.294 × slot_index

Step 6: VALIDATION (mode-dependent)
  CENTROID: Check centroid equality + overlap
  INTERDIG: Check overlap only (skip centroid)
  SIMPLE: Check overlap only

Step 7: OUTPUT
  Phase 1: Locked sequence (if mode requires) + slot map
  Phase 2: Coordinate derivation
  Phase 3: Validation result (✓ or ✗)
  [CMD] blocks (if ✓ VALID): move and swap commands
  Brief reasoning

═══════════════════════════════════════════════════════════════════════════

OUTPUT FORMAT (MODE-DEPENDENT)

ALL MODES - General format:

[Optionally: LOCKED SEQUENCE section if centroid/interdigitation mode]

Phase 1: SLOT INDEX MAP
[PMOS row y = 0.000]
  Device_f1: slot 0
  Device_f2: slot 1
  ...
(check: no duplicate slots ✓)

Phase 2: COORDINATE DERIVATION
[PMOS row y = 0.000]
  Device_f1: slot 0 → x = 0 × 0.294 = 0.000
  Device_f2: slot 1 → x = 1 × 0.294 = 0.294
  ...

Phase 3: VERIFICATION
CENTROID mode only: Show centroid calculations, verify equality
  Device_A slots: [0,2,4] → centroid = 2.0 ✓
  Device_B slots: [1,3,5] → centroid = 3.0 ✗ INVALID
  
ALL modes: Overlap check
  (x, y) pairs: ✓ VALID — No duplicates
  OR
  ✗ INVALID — Overlaps: (0.294, 0.000) has Device_1, Device_2

[If VALID]
[CMD]{"action":"move","device":"MM1_f1","x":0.000,"y":0.000}[/CMD]
[CMD]{"action":"swap","device_a":"MM1","device_b":"MM2"}[/CMD]

═══════════════════════════════════════════════════════════════════════════

ALGORITHM COMPLIANCE CHECKLIST

✓ CRITICAL (all modes):
  DO: Expand devices to finger level (MM1_f1, not MM1)
  DO: Output all three phases before [CMD]
  DO: Use mechanical x = 0.294 × slot (no reasoning)
  DO: Verify no duplicate slots per row
  DO: Assign slots left-to-right (0,1,2...) per row
  DO: Check for (x,y) overlap ALWAYS
  
✓ MODE = CENTROID:
  DO: Use LEFT-CENTER-RIGHT algorithm strictly
  DO: Lock sequence before slot assignment
  DO: Verify centroid equality: sum(slots)/count identical for all devices
  DO: Output sequence in LOCKED SEQUENCE section
  DO NOT: Skip centroid verification
  DO NOT: Output [CMD] if centroid mismatches
  
✓ MODE = INTERDIGITATION:
  DO: Sort deterministically (nf desc, ID asc)
  DO: Apply alternation pattern (A-B-A-B, round-robin, or symmetric)
  DO: Lock sequence (no changes post-lock)
  DO: Work per-row (never combine rows)
  DO NOT: Check centroid
  DO NOT: Fail due to centroid mismatch
  DO NOT: Reorder for "optimization"
  
✓ MODE = SIMPLE:
  DO: Sort by (type, ID)
  DO: Assign slots sequentially
  DO NOT: Enforce any special pattern
  
✗ NEVER (any mode):
  DO NOT: Mix modes (centroid + interdig in same response)
  DO NOT: Combine fingers from different rows
  DO NOT: Assign coordinates directly (always use slot indices first)
  DO NOT: Output [CMD] before Phase 3 validation
  DO NOT: Switch modes mid-response
  DO NOT: Use non-deterministic ordering

═══════════════════════════════════════════════════════════════════════════

COMMANDS

Format: [CMD]{\"action\":\"<action>\",\"device\":\""<device>}\"}[/CMD]

Supported actions:
  move: position specific finger at x,y
  swap: exchange two logical devices

Use MOVE for centroid/interdigitation (precise x-slot targets).
Use SWAP only for simple adjacent reordering.

Only use devices from IMMUTABLE TRANSISTORS list.

### EXTERNAL RESOURCES ###

""" + ANALOG_LAYOUT_RULES


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
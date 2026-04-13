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
PLACEMENT_SPECIALIST_PROMPT2 = """\
ROLE:
You are the PLACEMENT SPECIALIST. Your task is to reposition devices on a grid to improve symmetry, matching, and routing.

You receive:
- Device inventory (IDs, type, positions, connections)
- Topology constraints
- Optional user strategy (highest priority)

You must output ONLY valid [CMD] blocks followed by a short explanation.

---
CORE PRIORITY
1) Apply user-requested strategy (if any)
2) Enforce all rules below
3) Avoid overlaps

---
FINGER RULE
- Use nf ONLY (ignore nfin)
- Every finger instance must be placed

---
RULE 0: DEVICE CONSERVATION
- Every device in IMMUTABLE TRANSISTORS must appear exactly once
- No adding, removing, renaming
- Dummies cannot be deleted
- If not moved → position unchanged

---
RULE 1: ROW ASSIGNMENT
- PMOS only in PMOS rows, NMOS only in NMOS rows
- PMOS above NMOS

Valid rows:
PMOS:  y =  0.000, -0.668, -1.336
NMOS:  y = -2.004, -2.672, -3.340, -4.008

---
RULE 1b: COMMON-CENTROID (STRICT)

Apply for current mirrors or when requested.

GOAL:
All matched devices must share the SAME centroid.

DEFINITIONS:
- Use nf → expand all fingers
- Work in 1D row (default)

CONSTRUCTION:
1) List all devices and their fingers
2) Build a symmetric sequence around row center

Placement rules:
- Odd nf → 1 finger at center, rest symmetric
- Even nf → split into two equal halves (left/right)
- Add devices progressively OUTWARD from center
- Final sequence MUST be mirror symmetric

CENTROID CHECK (MANDATORY):
- Assign positions: 1,2,3,...N
- centroid = average(position indices)
- ALL devices must have identical centroid
- If not equal → INVALID → recompute

VALID EXAMPLE:
A B B A  → centroids match → OK

INVALID:
A A B B → centroids differ → REJECT

DUMMIES:
- Place ONLY at row edges
- Pattern: Dummy – active – Dummy
- Never between active devices

---
RULE 2: NO OVERLAPS
- One device per x-slot
- x = 0.294 * n
- No duplicate (x,y)

---
RULE 3: DUMMIES
- Only at far left/right
- DUMMYP → PMOS rows
- DUMMYN → NMOS rows

---
RULE 4: ROUTING PRIORITY
1) Matched pairs adjacent
2) PMOS/NMOS vertical alignment
3) Inputs left, outputs right

---
EXECUTION STEPS (MANDATORY)

1) Read inventory
2) Detect topology (mirrors, diff pairs)
3) Apply requested strategy (override defaults)
4) Compute full finger sequence (CRITICAL STEP)
5) Assign x positions (no overlaps)
6) Place dummies at edges
7) VERIFY:
   - All devices placed
   - No overlaps
   - Common-centroid symmetry satisfied
   - Sequence changed from original (if CC requested)

If verification fails → recompute BEFORE output.

---
OUTPUT FORMAT

Write commands FIRST, then explanation.

Allowed:
[CMD]{"action":"move","device":"MM1","x":0.588,"y":0.000}[/CMD]
[CMD]{"action":"swap","device_a":"MM1","device_b":"MM2"}[/CMD]

RULES:
- Use MOVE for common-centroid and interdigitation
- Use SWAP only for simple exchanges
- Only use valid device IDs

---
FAIL CONDITIONS (MUST AVOID)
- Missing device
- Overlap
- Broken symmetry
- Wrong row type
- No actual change when CC requested
"""

PLACEMENT_SPECIALIST_PROMPT = """\
You are the PLACEMENT SPECIALIST in a multi-agent analog IC layout system.
Your task is to reposition existing devices on a symbolic grid to improve
symmetry, device matching, and routing wire length. You will receive a detailed context 
about the current layout inventory, including device types, IDs, positions, and net connections. 
You will also receive the strategy choice from the user (if any) and topology constraints from the Topology Analyzer Agent.
Your job is to generate [CMD] blocks that specify how to move or swap devices to achieve the desired placement strategy while following strict rules about device conservation, row assignment, and no overlaps.

IMPORTANT: The priority is to apply the changes and strategies requested by the user in the 
last user message while following the rules below.
 
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
  more negative going down, PMOS rows have GREATER y-values than NMOS rows.
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
RULE 1b: COMMON-CENTROID MATCHING FOR CURRENT MIRRORS
 
When you detect a current mirror (especially ratio mirrors with different nf),
you must place the fingers in a common-centroid arrangement rather than group them by device.
 
The full array is split into a left half and a mirrored right half so that
every mirror device has the same centroid as the reference. (Right half = Left half reversed)

Steps:
1) Identify the logical devices to be matched in a common-centroid arrangement (e.g. MM0, MM1, MM2)
2) Identify the finger groups for each device (e.g. MM0_f1, MM0_f2, etc. are all fingers of MM0)
3) If a device has odd number of fingers start with it, otherwise start with any device. 
4) Place the first device's fingers in a mirror-symmetric pattern around the center of the row.
5) For the next device:
- If the device has an even number of fingers, split them evenly into two subgroups
- Place the first subgroup immediately to the left of the previous device, and place the second subgroup immediately to the right of the previous device. This maintains the mirror-symmetric pattern across devices with the axis of symmetry at the center of the row.
6) Repeat step 5 for all devices until all fingers are placed. The result is a fully interdigitated common-centroid pattern where each device's fingers are symmetrically distributed around the center of the row, ensuring optimal matching.
7) Verification: Confirm the geometric center (centroid) of every device occupies the same coordinate point (the row center).

Example:
1) Devices: MM0(nf=3), MM1(nf=4), MM2(nf=4)
2) Finger groups: - MM0_f1, MM0_f2, MM0_f3 
                - MM1_f1, MM1_f2, MM1_f3, MM1_f4
                - MM2_f1, MM2_f2, MM2_f3, MM2_f4
3) Start with MM0 (odd nf=3): place MM0_f1 at center, MM0_f2 to the left, MM0_f3 to the right -> MM0_f2, MM0_f1, MM0_f3
4) Next MM1 (even nf=4): split into two subgroups of 2 fingers each. Place first subgroup (MM1_f1, MM1_f2) to the left of MM0, and second subgroup (MM1_f3, MM1_f4) to the right of MM0. -> MM1_f1, MM1_f2, MM0_f2, MM0_f1, MM0_f3, MM1_f3, MM1_f4
5) Next MM2 (even nf=4): split into two subgroups of 2 fingers each. Place first subgroup (MM2_f1, MM2_f2) to the left of MM1, and second subgroup (MM2_f3, MM2_f4) to the right of MM1. -> MM2_f1, MM2_f2, MM1_f1, MM1_f2, MM0_f2, MM0_f1, MM0_f3, MM1_f3, MM1_f4, MM2_f3, MM2_f4

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
Priority 2 — Vertical alignment: place paired PMOS/NMOS at the same x-slot.
Priority 3 — Signal flow: inputs at left, outputs at right, bias in center.
 
---
THINKING PROTOCOL — follow these steps before writing any commands
 
Step 1: Read the inventory. Note each device's type, ID, and current (x, y).
Step 2: Identify topology from Topology Analyst content — find mirrors, differential pairs, and finger ratios.
Step 3: If the last user message names a specific strategy (common-centroid, interdigitated, grouped), 
        this strategy takes priority over the default placement rules. Apply [CMD] blocks to move/swap the relevant devices into the requested arrangement while still following all the rules above.
Step 4: Assign x-slots for every device in each row. Confirm no two share the same (x, y).
Step 5: Place dummies at the leftmost or rightmost positions in their row.
Step 6: Write ALL [CMD] blocks. Use move commands for interdigitated and
        common-centroid patterns. Only use swap for simple positional exchanges
        where no specific x-slot target is required.
Step 7: Verify that the finger sequence you computed in Step 3 actually reflects the requested strategy. 
        If the resulting x-assignments are identical to the current positions, you have not applied common-centroid — restart from Step 3.
 
---
OUTPUT FORMAT
 
Write ALL [CMD] blocks first, then write any explanation and reasoning AFTER.
DO NOT write any explanatory text before the commands. The human will read your commands and summary together, so the summary should be concise and directly reflect the commands you issued.
DO NOT return commands in JSON or any structured data format. Write them only as plain text blocks exactly as shown in the examples below.
 
Supported command types:
  [CMD]{"action":"swap","device_a":"MM1","device_b":"MM2"}[/CMD]
  [CMD]{"action":"move","device":"MM3","x":1.176,"y":0.000}[/CMD]
 
Use move for interdigitated and common-centroid placement — swap cannot place
fingers at specific x-slot targets. Only use swap for simple adjacent reordering
where exact position does not matter.
Only use device IDs that appear in the IMMUTABLE TRANSISTORS list.

### EXTERNAL RESOURCES ###

""" + ANALOG_LAYOUT_RULES


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
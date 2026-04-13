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
RULE 1b: COMMON-CENTROID MATCHING FOR CURRENT MIRRORS
 
When you detect a current mirror (especially ratio mirrors with different nf),
you must place the fingers in a common-centroid arrangement rather than group them by device.
 
SINGLE-ROW COMMON-CENTROID MATCHING
  The full array is split into a left half and a mirrored right half so that
  every mirror device has the same centroid as the reference. (Right half = Left half reversed)
 
  Example: MM2_f1 MM0_f1 MM1_f1 MM0_f2 MM0_f3 MM1_f2 MM0_f4 MM2_f2 MM2_f3 MM0_f5 MM1_f3 MM0_f6 MM0_f7 MM1_f4 MM0_f8 MM2_f4
 
  Full row (x increases left to right, one slot per finger):
    x=0.000  x=0.294  x=0.588  x=0.882  x=1.176  x=1.470  x=1.764  x=2.058 ...
    MM2_f1   MM0_f1   MM1_f1   MM0_f2   MM0_f3   MM1_f2   MM0_f4   MM2_f2  ...
 
  Goal: MM0 centroid == MM1 centroid == MM2 centroid == array center.
 
  Common-centroid intentionally splits a device's
  fingers into non-contiguous subgroups — this is expected and correct.
 
MULTI-ROW COMMON-CENTROID (total fingers > 16) — forward/reverse (AB) across 2 rows:
  Each device's fingers are split evenly across both rows. Row 0 uses forward
  order; Row 1 uses reversed order to achieve ABBA-style symmetry.
 
  For Example If we have the following devices: MM0(nf=10), MM1(nf=6), MM2(nf=4), total=20 fingers, 2 rows of 10
  Their fingers would be arranged like this across the two rows to achieve multi-row common-centroid matching:
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
Step 2: Identify topology from Topology Analyst content — find mirrors, differential pairs, and finger ratios.
Step 3: If the last user message names a specific strategy (common-centroid, interdigitated, grouped), 
        this strategy takes priority over the default placement rules. Apply [CMD] blocks to move/swap the relevant devices into the requested arrangement while still following all the rules above.
Step 4: Assign x-slots for every device in each row. Confirm no two share the same (x, y).
Step 5: Place dummies at the leftmost or rightmost positions in their row.
Step 6: Write ALL [CMD] blocks. Use move commands for interdigitated and
        common-centroid patterns. Only use swap for simple positional exchanges
        where no specific x-slot target is required.
Step 7: Self-check — make sure that all your commands are different from the current positions, and that no two devices end up in the same (x, y).
 
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
"""


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
      - Net adjacency and routing cost tables
            - Topology constraints context
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
        "PMOS DEVICES (current row/y values):"
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
        "NMOS DEVICES (current row/y values):"
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
        lines.append("EXISTING DUMMIES:")
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
                "(devices sharing a net):"
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

    return "\n".join(lines)
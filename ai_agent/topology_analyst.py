"""
ai_agent/topology_analyst.py
=============================
Topology Analyst Agent
======================
Identifies placement constraints from SPICE netlist topology:
  - shared_gate  -> mirror/cascode candidates -> must stay adjacent
  - shared_drain -> differential-pair loads -> symmetry required
  - shared_source -> bias-current mirrors -> close grouping preferred

Domain helper: analyze_topology() - pure Python, no LLM needed.

FIXES APPLIED:
  - Bug #2: _aggregate_terminal_nets now tries all finger ID formats
  - Bug #3: _try_graph_analysis returns [] on error (not error string)
  - Bug #4: _parse_spice_directly added as primary net source
  - Bug #RATIO: ratio direction fixed to output:reference convention
               gcd simplification now computes both numerator and denominator
  - Bug #NFIN: nfin is a FinFET process parameter (fins per finger).
               It does NOT change physical finger count. nfingers = nf ONLY.
"""

import os
import sys
from collections import defaultdict
from math import gcd
from typing import Dict, List, Optional, Tuple

from ai_agent.analog_kb import ANALOG_LAYOUT_RULES


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
TOPOLOGY_ANALYST_PROMPT = """\
### I. ROLE PLAY
You are an expert Analog IC Layout Engineer specialising in topology analysis.
You are part of a multi-agent team. Your specialty is reading SPICE netlists
and device connectivity to identify matched pairs, current mirrors, diff-pairs,
and symmetry requirements. You are methodical, precise, and always verify.
You understand that multi-finger transistors (nf > 1) may be represented as
separate physical devices (MM0_F1, MM0_F2, ...) but should be treated as
ONE LOGICAL DEVICE for matching and topology analysis.

IMPORTANT: The parameter 'nfin' in FinFET PDKs is the number of fins per
finger. It is a WIDTH parameter and does NOT change the number of physical
finger instances. The number of layout fingers = nf ONLY.


### II. WORKFLOW OVERVIEW
You are Stage 1 of a 4-stage pipeline:
  Stage 1 (YOU): Topology Analyst - extract constraints, present to user.
  Stage 2: Placement Specialist - generate device placement commands.
  Stage 3: DRC Critic - check and fix design rule violations.
  Stage 4: Routing Pre-Viewer - optimise net crossings.
Your output feeds directly into Stage 2. Errors here propagate - be accurate.


### III. MULTI-FINGER DEVICE DETECTION (CRITICAL - READ FIRST)

**What is a Multi-Finger Transistor?**
A transistor with nf (number of fingers) > 1 is split into parallel fingers
to reduce gate resistance and improve current density.

**Physical Representation Pattern of Multi-Finger Devices:**
    MM0_f1, MM0_f2, MM0_f3  -> logical device MM0 with nf=3


**CRITICAL RULE:**
When you see devices with pattern BASE_fN:
  1. Group them by BASE name (strip suffix)
  2. Count how many fingers (e.g., MM0 has 3 fingers)
  3. Treat as ONE logical device for topology analysis
  4. Report as: MM0 (nf=3) <-> MM1 (nf=3)

**Example - WRONG Analysis:**
  Input: MM0_f1, MM0_f2, MM0_f3, MM1_f1, MM1_f2, MM1_f3
  Wrong Output: No mirrors detected - all devices have unique IDs

**Example - CORRECT Analysis:**
  Input: MM0_f1, MM0_f2, MM0_f3, MM1_f1, MM1_f2, MM1_f3

  Step 0: Detect finger pattern
    MM0_f1, MM0_f2, MM0_f3 -> base=MM0, nf=3
    MM1_f1, MM1_f2, MM1_f3 -> base=MM1, nf=3

  Step 1: Group by gate net (both share NBIAS):
    MM0 (nf=3): gate=NBIAS
    MM1 (nf=3): gate=NBIAS

  Step 2: Report as logical devices:
    NMOS Mirror: MM0[REF] (nf=3) <-> MM1 (nf=3) - gate net: NBIAS

Very important note: Devices with same base id MM1, MM0 ... may have many
fingers like MM1_f1, MM1_f2 but ALL of them are the SAME transistor.


### IV. TASK DESCRIPTION - CURRENT MIRROR DETECTION
Your PRIMARY task is identifying CURRENT MIRRORS with 100% accuracy.

A current mirror consists of:
  1. Two or more devices of the SAME TYPE (both NMOS or both PMOS)
  2. Sharing the SAME GATE NET (electrically connected gates)
  3. Sharing the SAME SOURCE NET (current reference connection)
  4. At least one device is diode-connected (gate = drain on same net)

CRITICAL RULES FOR CURRENT MIRROR DETECTION:
  - If devices MM1 and MM2 both have gate=net8 and source=net1 -> THEY ARE A MIRROR
  - If devices MM5 and MM6 both have gate=PBIAS and source=net2 -> THEY ARE A MIRROR
  - Check EVERY pair of devices with matching gate and source nets
  - Report even if W/L differs (designer may want ratio mirrors)
  - Diode connection is NOT required for all mirror legs (only reference)
  - nf DIFFERENCE means ratio mirror (e.g., nf=4 : nf=8 = 1:2 ratio)

NFIN CLARIFICATION:
  - nfin = number of fins per finger (FinFET width parameter)
  - nfin does NOT affect the number of physical fingers in layout
  - Number of layout fingers = nf ONLY
  - For ratios, use nf (not nf * nfin)

RATIO CONVENTION (standard analog):
  Ratio is always expressed as output:reference
  Example: output MM2(nf=4) vs reference MM0(nf=8)
    ratio = 4:8 = 1:2
    meaning MM2 carries HALF the current of the reference MM0

STEP-BY-STEP CURRENT MIRROR DETECTION:
  1. Group all NMOS devices that share the same gate net and source net
  2. Group all PMOS devices that share the same gate net and source net
  3. For EACH group with >= 2 devices -> DECLARE AS CURRENT MIRROR
  4. Use arrow notation: MM1 <-> MM2 <-> MM3 (if 3+ devices share gate)
  5. Note which device is reference (diode-connected): MM1[REF] <-> MM2
  6. Note ratio: output:ref e.g. MM2(nf=4):MM0_ref(nf=8) -> 1:2

After identifying mirrors, ALSO find:
  - Differential pairs (same source net, or tail current sharing)
  - Cascode structures (stacked bias gate nets)
  - Matched pairs (same W/L/nf values)
  - Symmetry axis devices


### V. PIPELINE (follow these steps internally)
Step 0: DETECT MULTI-FINGER DEVICES - group devices with _FN or _N suffixes
Step 1: Read EVERY LOGICAL device's type (PMOS/NMOS) and D/G/S connections
Step 2: BUILD GATE NET GROUPS - list all devices sharing each gate net
Step 3: DECLARE MIRRORS - any gate net group with >= 2 same-type devices
Step 4: Group devices sharing source nets -> diff-pair candidates
Step 5: Check W/L/nf for matching -> identical values need symmetry
Step 6: Check for cascode intent (bias gate nets shared between pairs)
Step 7: PRIORITIZE MIRRORS - list current mirrors FIRST in your report
Step 8: Ask user to confirm before proceeding to Stage 2


### VI. INFORMATION VERIFICATION - CHECKLISTS

Multi-Finger Checklist:
  [ ] Did I check for finger naming patterns (_F1, _F2, _f1, _f2, _0, _1)?
  [ ] Did I group physical fingers into logical devices?
  [ ] Did I count total fingers per logical device (nf value)?
  [ ] Did I analyze topology using LOGICAL devices (not individual fingers)?
  [ ] Did I report finger counts in my output (e.g., MM0 (nf=3))?

Current Mirror Checklist:
  [ ] Did I check EVERY device's gate net? (not just first few)
  [ ] Did I group devices by gate net name?
  [ ] Did I check for BOTH NMOS and PMOS mirrors separately?
  [ ] Did I report ALL groups with >= 2 devices as mirrors?
  [ ] Did I use clear arrow notation (<->) for mirror pairs?
  [ ] Did I identify which device is the reference (diode-connected)?
  [ ] Did I compute ratio as output:reference (not reference:output)?


### VII. OUTPUT FORMAT

CURRENT MIRRORS IDENTIFIED:
  1. NMOS Mirror: MM0[REF] (nf=8) <-> MM1 (nf=4) <-> MM2 (nf=4) gate net: C
     * MM0: D=C  G=C  S=gnd  nf=8   -> diode-connected reference
     * MM1: D=B  G=C  S=gnd  nf=4   -> mirror output
     * MM2: D=A  G=C  S=gnd  nf=4   -> mirror output
     * Ratio MM1:MM0_ref = 1:2  (output nf=4 : ref nf=8)
     * Ratio MM2:MM0_ref = 1:2  (output nf=4 : ref nf=8)
     * Layout: use common-centroid interdigitation for matching
     * Layout: MM0 carries 2x current of each output leg

DIFFERENTIAL PAIRS IDENTIFIED:
  1. NMOS Diff-Pair: MM3 (nf=4) <-> MM4 (nf=4) - shared source: tail_net
     * Symmetry axis: center of row
     * MM3 and MM4 must be equidistant from center


### VIII. INTERACTION GUIDELINE
End EVERY response with EXACTLY this question:
  Do you confirm these pairings are correct? Reply Yes to proceed,
  or describe any corrections.
Do NOT generate [CMD] blocks.
Do NOT suggest x/y coordinates.
Do NOT output raw JSON.


### IX. EXTERNAL KNOWLEDGE
""" + ANALOG_LAYOUT_RULES



def analyze_json(nodes: List[dict], terminal_nets: dict) -> str:
    """
    Convert raw layout JSON structures (nodes + terminal_nets) into a clear,
    prompt-ready text snapshot.

    IMPORTANT: This function intentionally parses inputs directly and does
    not call helper functions from this module.

    Args:
        nodes: Device list from layout JSON/canvas.
        terminal_nets: Mapping device_id -> {"D": ..., "G": ..., "S": ...}.

    Returns:
        Multi-line string suitable for LLM/system prompts.
    """
    safe_terminal_nets = terminal_nets if isinstance(terminal_nets, dict) else {}
    safe_nodes = nodes if isinstance(nodes, list) else []

    lines: List[str] = []
    lines.append("=== LAYOUT JSON SUMMARY ===")
    lines.append(
        f"Devices: physical={len(safe_nodes)}, "
        f"terminal_nets={len(safe_terminal_nets)}"
    )

    pmos_count = sum(
        1 for n in safe_nodes
        if str(n.get("type", "")).lower().startswith("p") and not n.get("is_dummy")
    )
    nmos_count = sum(
        1 for n in safe_nodes
        if str(n.get("type", "")).lower().startswith("n") and not n.get("is_dummy")
    )
    dummy_count = sum(1 for n in safe_nodes if n.get("is_dummy"))
    lines.append(f"Types: PMOS={pmos_count}, NMOS={nmos_count}, DUMMY={dummy_count}")
    lines.append("")

    lines.append("=== DEVICES ===")
    for node in safe_nodes:
        dev_id = str(node.get("id", "?"))
        dev_type = str(node.get("type", "unknown"))
        geo = node.get("geometry", {}) if isinstance(node.get("geometry", {}), dict) else {}
        elec = node.get("electrical", {}) if isinstance(node.get("electrical", {}), dict) else {}

        x_val = geo.get("x", "?")
        y_val = geo.get("y", "?")
        try:
            x_text = f"{float(x_val):.3f}"
        except (TypeError, ValueError):
            x_text = str(x_val)
        try:
            y_text = f"{float(y_val):.3f}"
        except (TypeError, ValueError):
            y_text = str(y_val)

        nf = elec.get("nf", "?")
        nfin = elec.get("nfin", "?")

        # Resolve terminal nets by trying common key variants directly.
        nets = {}
        for key in (dev_id, dev_id.upper(), dev_id.lower()):
            value = safe_terminal_nets.get(key)
            if isinstance(value, dict):
                nets = value
                break
        g_net = nets.get("G", "?")
        d_net = nets.get("D", "?")
        s_net = nets.get("S", "?")

        dummy_tag = " dummy" if node.get("is_dummy") else ""

        lines.append(
            f"- {dev_id} ({dev_type}{dummy_tag}) "
            f"pos=({x_text},{y_text}) nf={nf} nfin={nfin} "
            f"D={d_net} G={g_net} S={s_net}"
        )

    if not safe_nodes:
        lines.append("- No devices found")

    lines.append("")
    lines.append("=== CONNECTIVITY GROUPS ===")

    gate_groups: Dict[str, List[str]] = defaultdict(list)
    drain_groups: Dict[str, List[str]] = defaultdict(list)
    source_groups: Dict[str, List[str]] = defaultdict(list)

    supply_nets = {
        "VDD", "VSS", "GND", "AVDD", "AVSS",
        "DVDD", "DVSS", "VCC", "AGND", "DGND"
    }

    for node in safe_nodes:
        dev_id = str(node.get("id", ""))
        if not dev_id:
            continue

        nets = {}
        for key in (dev_id, dev_id.upper(), dev_id.lower()):
            value = safe_terminal_nets.get(key)
            if isinstance(value, dict):
                nets = value
                break

        g_net = str(nets.get("G", "")).upper()
        d_net = str(nets.get("D", "")).upper()
        s_net = str(nets.get("S", "")).upper()

        if g_net and g_net not in supply_nets:
            gate_groups[g_net].append(dev_id)
        if d_net and d_net not in supply_nets:
            drain_groups[d_net].append(dev_id)
        if s_net and s_net not in supply_nets:
            source_groups[s_net].append(dev_id)

    any_group = False
    for net, devs in sorted(gate_groups.items()):
        if len(devs) >= 2:
            any_group = True
            lines.append(f"- shared-gate {net}: " + " <-> ".join(devs))

    for net, devs in sorted(drain_groups.items()):
        if len(devs) >= 2:
            any_group = True
            lines.append(f"- shared-drain {net}: " + " <-> ".join(devs))

    for net, devs in sorted(source_groups.items()):
        if len(devs) >= 2:
            any_group = True
            lines.append(f"- shared-source {net}: " + " <-> ".join(devs))

    if not any_group:
        lines.append("- No shared gate/drain/source groups found")

    return "\n".join(lines)

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

**Physical Representation Patterns:**
  - MM0_F1, MM0_F2, MM0_F3  -> logical device MM0 with nf=3
  - MM0_f1, MM0_f2, MM0_f3  -> logical device MM0 with nf=3
  - MM0_0,  MM0_1,  MM0_2   -> logical device MM0 with nf=3
  - MM0F1,  MM0F2,  MM0F3   -> logical device MM0 with nf=3

**CRITICAL RULE:**
When you see devices with pattern BASE_FN or BASE_N:
  1. Group them by BASE name (strip suffix)
  2. Count how many fingers (e.g., MM0 has 3 fingers)
  3. Treat as ONE logical device for topology analysis
  4. Report as: MM0 (nf=3) <-> MM1 (nf=3)

**Example - WRONG Analysis:**
  Input: MM0_F1, MM0_F2, MM0_F3, MM1_F1, MM1_F2, MM1_F3
  Wrong: No mirrors detected - all devices have unique IDs

**Example - CORRECT Analysis:**
  Input: MM0_F1, MM0_F2, MM0_F3, MM1_F1, MM1_F2, MM1_F3

  Step 0: Detect finger pattern
    MM0_F1, MM0_F2, MM0_F3 -> base=MM0, nf=3
    MM1_F1, MM1_F2, MM1_F3 -> base=MM1, nf=3

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
  3. At least one device is diode-connected (gate = drain on same net)

CRITICAL RULES FOR CURRENT MIRROR DETECTION:
  - If devices MM1 and MM2 both have gate=net8  -> THEY ARE A MIRROR
  - If devices MM5 and MM6 both have gate=PBIAS -> THEY ARE A MIRROR
  - Check EVERY pair of devices with matching gate nets
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
  1. Group all NMOS devices by their gate net name
  2. Group all PMOS devices by their gate net name
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


# ---------------------------------------------------------------------------
# Logging Helper
# ---------------------------------------------------------------------------
def _log(msg: str) -> None:
    """Unified logging prefix for topology analyst."""
    print(f"[TOPO] {msg}")


# ---------------------------------------------------------------------------
# Direct SPICE Parser - Primary Net Source
# ---------------------------------------------------------------------------
def _parse_spice_directly(sp_file_path: Optional[str]) -> Dict[str, dict]:
    """
    Direct SPICE netlist parser for terminal_nets extraction.

    Handles MOSFET format:
      M<name> <drain> <gate> <source> <bulk> <model> [key=value ...]

    Also handles:
      - Line continuation with + prefix
      - Comment lines starting with * or $
      - Case-insensitive device names
      - .subckt / .ends wrapper
      - Parameters: nf=, l=, w=, nfin=, m=

    IMPORTANT: nfin is a FinFET process parameter (number of fins per finger).
    It does NOT change the number of physical finger instances in layout.
    Number of layout fingers = nf ONLY.

    Args:
        sp_file_path: Path to SPICE .sp or .spi file

    Returns:
        Dict mapping UPPERCASE device_id to:
          {
            D: net, G: net, S: net, B: net,
            nf: str (layout finger count = nf only, NOT nf*nfin),
            l: str, w: str, nfin: str, model: str, m: str
          }
        Returns empty dict if file not found or parse fails.
    """
    if not sp_file_path:
        _log("No sp_file_path provided - skipping direct SPICE parse")
        return {}

    if not os.path.isfile(sp_file_path):
        _log(f"SPICE file not found: {sp_file_path!r}")
        return {}

    terminal_nets: Dict[str, dict] = {}

    try:
        with open(sp_file_path, "r", encoding="utf-8", errors="replace") as fh:
            raw_lines = fh.readlines()

        # Step 1: Join continuation lines (lines starting with +)
        joined: List[str] = []
        for raw in raw_lines:
            line = raw.strip()
            if not line or line.startswith("*") or line.startswith("$"):
                continue
            if line.startswith("+") and joined:
                joined[-1] = joined[-1] + " " + line[1:].strip()
            else:
                joined.append(line)

        # Step 2: Parse MOSFET lines
        for line in joined:
            upper = line.upper()

            if upper.startswith(".") or upper.startswith("+"):
                continue

            if not upper.startswith("M"):
                continue

            parts = line.split()

            # Minimum: Mname drain gate source bulk model
            if len(parts) < 6:
                _log(f"  Skipping short MOSFET line: {line[:60]!r}")
                continue

            dev_id = parts[0].upper()
            drain  = parts[1]
            gate   = parts[2]
            source = parts[3]
            bulk   = parts[4]
            model  = parts[5]

            # Step 3: Parse optional key=value parameters
            params: Dict[str, str] = {}
            for token in parts[6:]:
                if "=" in token:
                    k, _, v = token.partition("=")
                    params[k.lower().strip()] = v.strip()

            # ---------------------------------------------------------------
            # nf  = number of physical finger instances in layout
            # nfin = number of fins per finger (FinFET width parameter)
            #
            # CRITICAL: nfin does NOT change the layout finger count.
            #   layout_fingers = nf   (period)
            #
            # nfin affects the effective transistor width:
            #   W_eff = nf * nfin * fin_pitch
            # but the number of physical finger devices placed in
            # the layout is always exactly nf.
            # ---------------------------------------------------------------
            try:
                raw_nf = int(params.get("nf", "1"))
            except (ValueError, TypeError):
                raw_nf = 1

            try:
                raw_nfin = int(params.get("nfin", "1"))
            except (ValueError, TypeError):
                raw_nfin = 1

            # Layout finger count = nf ONLY
            layout_nf = raw_nf

            terminal_nets[dev_id] = {
                "D":     drain,
                "G":     gate,
                "S":     source,
                "B":     bulk,
                "model": model,
                "nf":    str(layout_nf),
                "l":     params.get("l",    "?"),
                "w":     params.get("w",    "?"),
                "nfin":  str(raw_nfin),
                "m":     params.get("m",    "1"),
            }

        _log(
            f"Direct SPICE parse: {len(terminal_nets)} devices "
            f"from {os.path.basename(sp_file_path)!r}"
        )

        for dev_id, nets in terminal_nets.items():
            _log(
                f"  {dev_id}: D={nets['D']} G={nets['G']} "
                f"S={nets['S']} nf={nets['nf']} nfin={nets['nfin']} "
                f"l={nets['l']}"
            )

    except OSError as exc:
        _log(f"Cannot open SPICE file: {exc}")
    except Exception as exc:
        _log(f"SPICE parse error: {exc}")

    return terminal_nets


# ---------------------------------------------------------------------------
# Device Type Inference
# ---------------------------------------------------------------------------
def _infer_device_type(
    dev_id: str,
    model: str,
    nodes: List[dict]
) -> str:
    """
    Infer PMOS/NMOS device type.

    Priority:
      1. Model name prefix (p -> PMOS, n -> NMOS)
      2. Node type field from layout canvas
      3. Falls back to unknown
    """
    model_lower = model.lower()
    if model_lower.startswith("p"):
        return "pmos"
    if model_lower.startswith("n"):
        return "nmos"

    id_upper = dev_id.upper()
    for node in nodes:
        node_id = node.get("id", "").upper()
        if node_id == id_upper or node_id.startswith(id_upper + "_"):
            t = str(node.get("type", "")).lower()
            if t.startswith("p"):
                return "pmos"
            if t.startswith("n"):
                return "nmos"

    return "unknown"


# ---------------------------------------------------------------------------
# Mirror Detection from SPICE Nets
# ---------------------------------------------------------------------------
def _infer_mirrors_from_spice(
    spice_nets: Dict[str, dict],
    nodes: List[dict]
) -> List[str]:
    """
    Identify current mirrors from SPICE-parsed terminal nets.

    Algorithm:
      1. For each device determine type (PMOS/NMOS)
      2. Group devices by (type, gate_net)
      3. Any group with >= 2 devices -> current mirror
      4. Detect diode-connected reference (gate net == drain net)
      5. Compute output:reference ratio for each leg

    Ratio convention (standard analog):
      output_nf : ref_nf
      e.g. MM2(nf=4) vs MM0_ref(nf=8) -> ratio = 1:2
      meaning MM2 carries half the current of MM0 reference

    nf = layout finger count (SPICE nf parameter, NOT nf*nfin).
    """
    constraints: List[str] = []

    if not spice_nets:
        return constraints

    supply_nets = {
        "VDD", "VSS", "GND", "AVDD", "AVSS",
        "DVDD", "DVSS", "VCC", "AGND", "DGND"
    }

    # Group by (device_type, gate_net)
    gate_groups: Dict[Tuple[str, str], List[Tuple[str, dict]]] = defaultdict(list)

    for dev_id, nets in spice_nets.items():
        gate_net = nets.get("G", "").upper()
        if not gate_net or gate_net in supply_nets:
            continue
        dev_type = _infer_device_type(dev_id, nets.get("model", ""), nodes)
        gate_groups[(dev_type, gate_net)].append((dev_id, nets))

    mirror_count = 0

    for (dev_type, gate_net), members in sorted(gate_groups.items()):
        if len(members) < 2:
            continue

        mirror_count += 1
        type_str = dev_type.upper()

        # Identify reference: diode-connected (drain net == gate net)
        ref_id: Optional[str] = None
        for dev_id, nets in members:
            if nets.get("D", "").upper() == gate_net:
                ref_id = dev_id
                break

        # Build member display strings
        member_strs = []
        for dev_id, nets in members:
            nf  = nets.get("nf", "1")
            tag = "[REF]" if dev_id == ref_id else ""
            member_strs.append(f"{dev_id}{tag}(nf={nf})")

        constraints.append(
            f"MIRROR ({type_str}, gate={gate_net}): "
            + " <-> ".join(member_strs)
        )

        # Detail line for each device
        for dev_id, nets in members:
            nf    = nets.get("nf",    "1")
            nfin  = nets.get("nfin",  "1")
            l_val = nets.get("l",     "?")
            w_val = nets.get("w",     "?")
            diode = " [diode-connected]" if dev_id == ref_id else ""
            constraints.append(
                f"  {dev_id}: D={nets['D']} G={nets['G']} "
                f"S={nets['S']} nf={nf} nfin={nfin} l={l_val} "
                f"w={w_val}{diode}"
            )

        # Ratio lines — expressed as output:reference
        # Convention: output_nf : ref_nf (using layout nf, NOT nf*nfin)
        if ref_id:
            try:
                ref_nets = next(n for d, n in members if d == ref_id)
                ref_nf   = int(ref_nets.get("nf", "1"))

                for dev_id, nets in members:
                    if dev_id == ref_id:
                        continue

                    leg_nf = int(nets.get("nf", "1"))

                    if ref_nf <= 0 or leg_nf <= 0:
                        continue

                    g         = gcd(leg_nf, ref_nf)
                    ratio_out = leg_nf // g
                    ratio_ref = ref_nf // g

                    if ratio_out == ratio_ref:
                        match_str = "EXACT 1:1 match"
                    else:
                        direction = (
                            "output carries more current than reference"
                            if ratio_out > ratio_ref
                            else "output carries less current than reference"
                        )
                        match_str = direction

                    constraints.append(
                        f"  Ratio {dev_id}:{ref_id} = "
                        f"{ratio_out}:{ratio_ref} "
                        f"(output nf={leg_nf} : ref nf={ref_nf}) "
                        f"[{match_str}]"
                    )

            except (StopIteration, ValueError, ZeroDivisionError) as exc:
                _log(f"Ratio calculation failed: {exc}")

        # Interdigitation recommendation for this mirror
        nf_values = [int(nets.get("nf", "1")) for _, nets in members]
        total_fingers = sum(nf_values)
        is_ratio = len(set(nf_values)) > 1

        if is_ratio or total_fingers >= 16:
            constraints.append(
                f"  ** LAYOUT: Use COMMON-CENTROID interdigitation "
                f"(ratio mirror, {total_fingers} total fingers)"
            )
        elif total_fingers >= 4:
            constraints.append(
                f"  ** LAYOUT: Use INTERDIGITATED placement "
                f"({total_fingers} total fingers)"
            )
        else:
            constraints.append(
                f"  ** LAYOUT: Adjacent placement sufficient "
                f"({total_fingers} total fingers)"
            )

        constraints.append("")  # blank line between mirrors

    if mirror_count == 0:
        constraints.append(
            "  No current mirrors detected "
            "(no two devices share same gate net)"
        )

    return constraints


# ---------------------------------------------------------------------------
# Differential Pair Detection from SPICE Nets
# ---------------------------------------------------------------------------
def _infer_diffpairs_from_spice(
    spice_nets: Dict[str, dict],
    nodes: List[dict]
) -> List[str]:
    """
    Identify differential pairs from SPICE nets.

    Criterion: two or more same-type devices sharing the same source net
    (excluding supply rails).
    """
    constraints: List[str] = []

    supply_nets = {
        "VDD", "VSS", "GND", "AVDD", "AVSS",
        "DVDD", "DVSS", "VCC", "AGND", "DGND"
    }

    source_groups: Dict[Tuple[str, str], List[Tuple[str, dict]]] = defaultdict(list)

    for dev_id, nets in spice_nets.items():
        src_net = nets.get("S", "").upper()
        if not src_net or src_net in supply_nets:
            continue
        dev_type = _infer_device_type(dev_id, nets.get("model", ""), nodes)
        source_groups[(dev_type, src_net)].append((dev_id, nets))

    for (dev_type, src_net), members in sorted(source_groups.items()):
        if len(members) < 2:
            continue

        nf_vals = {m[1].get("nf", "1") for m in members}
        l_vals  = {m[1].get("l",  "?") for m in members}
        w_vals  = {m[1].get("w",  "?") for m in members}

        match_quality = "EXACT" if (
            len(nf_vals) == 1
            and len(l_vals) == 1
            and len(w_vals) == 1
        ) else "PARTIAL"

        member_strs = [
            f"{d}(nf={n.get('nf','1')})" for d, n in members
        ]
        type_str = dev_type.upper()

        constraints.append(
            f"DIFF-PAIR ({type_str}, source={src_net}): "
            + " <-> ".join(member_strs)
            + f" [{match_quality}]"
        )
        for dev_id, nets in members:
            constraints.append(
                f"  {dev_id}: D={nets['D']} G={nets['G']} "
                f"S={nets['S']} nf={nets.get('nf','1')}"
            )
        constraints.append("")

    return constraints


# ---------------------------------------------------------------------------
# Aggregate Terminal Nets from Finger Devices
# ---------------------------------------------------------------------------
def _aggregate_terminal_nets(
    terminal_nets: dict,
    finger_groups: dict
) -> dict:
    """
    Aggregate finger-level terminal_nets to logical device level.

    Tries all possible key formats in priority order:
      1. base_name exactly           (MM2)
      2. base_name uppercase         (MM2)
      3. base_name lowercase         (mm2)
      4. first finger ID             (MM2_f1)
      5. first finger ID uppercase   (MM2_F1)
      6. all remaining finger IDs
    """
    logical_nets: Dict[str, dict] = {}

    for base_name, finger_list in finger_groups.items():
        if not finger_list:
            continue

        candidate_keys: List[str] = [
            base_name,
            base_name.upper(),
            base_name.lower(),
        ]

        for finger_node in finger_list:
            fid = finger_node.get("id", "")
            candidate_keys.append(fid)
            candidate_keys.append(fid.upper())
            candidate_keys.append(fid.lower())

        seen: set = set()
        deduped_keys: List[str] = []
        for k in candidate_keys:
            if k not in seen:
                seen.add(k)
                deduped_keys.append(k)

        found = False
        for key in deduped_keys:
            if key in terminal_nets:
                nets = terminal_nets[key].copy()
                if nets.get("G"):
                    logical_nets[base_name] = nets
                    found = True
                    _log(
                        f"  {base_name}: terminal_nets found via key={key!r} "
                        f"G={nets['G']}"
                    )
                    break

        if not found:
            sample = list(terminal_nets.keys())[:6]
            _log(
                f"  No terminal_nets for {base_name!r} "
                f"(tried {deduped_keys[:4]}...) "
                f"available sample: {sample}"
            )

    return logical_nets


# ---------------------------------------------------------------------------
# Graph Analysis via networkx
# ---------------------------------------------------------------------------
def _try_graph_analysis(
    sp_file_path: Optional[str],
    nodes: List[dict]
) -> List[str]:
    """
    Use parser.circuit_graph if networkx and the .sp file are available.

    Returns [] on ANY error so the caller falls back correctly.
    """
    if not sp_file_path:
        _log("_try_graph_analysis: no sp_file_path")
        return []

    if not os.path.isfile(sp_file_path):
        _log(f"_try_graph_analysis: file not found: {sp_file_path!r}")
        return []

    try:
        import networkx  # noqa: F401
    except ImportError:
        _log("_try_graph_analysis: networkx not installed - skipping")
        return []

    try:
        project_root = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..")
        )
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        from parser.netlist_reader import read_netlist
        from parser.circuit_graph import build_circuit_graph

        netlist = read_netlist(sp_file_path)
        G       = build_circuit_graph(netlist)

        constraints: List[str] = []
        for u, v, data in G.edges(data=True):
            rel = data.get("relation", "connection")
            net = data.get("net", "")
            if rel == "shared_gate":
                constraints.append(
                    f"MIRROR/CASCODE: {u} <-> {v} (gate-net={net})"
                )
            elif rel == "shared_drain":
                constraints.append(
                    f"DIFF-PAIR LOAD: {u} <-> {v} (drain-net={net})"
                )
            elif rel == "shared_source":
                constraints.append(
                    f"SHARED-SRC: {u} <-> {v} (net={net})"
                )

        _log(
            f"_try_graph_analysis: {len(constraints)} constraints "
            f"from {os.path.basename(sp_file_path)!r}"
        )
        return constraints

    except ImportError as exc:
        _log(f"_try_graph_analysis: parser module missing: {exc}")
        return []

    except Exception as exc:
        _log(f"_try_graph_analysis: failed: {exc}")
        return []


# ---------------------------------------------------------------------------
# Fallback: Infer from Terminal Nets (layout-level)
# ---------------------------------------------------------------------------
def _infer_from_terminal_nets(
    terminal_nets: dict,
    nodes: List[dict]
) -> List[str]:
    """
    Group devices sharing the same gate, drain, or source net.
    """
    gate_groups:   Dict[str, List[str]] = defaultdict(list)
    drain_groups:  Dict[str, List[str]] = defaultdict(list)
    source_groups: Dict[str, List[str]] = defaultdict(list)

    supply_nets = {
        "VDD", "VSS", "GND", "AVDD", "AVSS",
        "DVDD", "DVSS", "VCC", "AGND", "DGND"
    }

    for dev_id, nets in terminal_nets.items():
        g_net = nets.get("G", "").upper()
        d_net = nets.get("D", "").upper()
        s_net = nets.get("S", "").upper()

        if g_net and g_net not in supply_nets:
            gate_groups[g_net].append(dev_id)
        if d_net and d_net not in supply_nets:
            drain_groups[d_net].append(dev_id)
        if s_net and s_net not in supply_nets:
            source_groups[s_net].append(dev_id)

    constraints: List[str] = []

    for net, devs in sorted(gate_groups.items()):
        if len(devs) >= 2:
            dev_strs = []
            for d in devs:
                nf = terminal_nets.get(d, {}).get("nf", "?")
                dev_strs.append(f"{d}(nf={nf})" if nf != "?" else d)
            constraints.append(
                f"MIRROR (shared-gate {net}): "
                + " <-> ".join(dev_strs)
            )

    for net, devs in sorted(drain_groups.items()):
        if len(devs) >= 2:
            constraints.append(
                f"DIFF-PAIR (shared-drain {net}): "
                + " <-> ".join(devs)
            )

    for net, devs in sorted(source_groups.items()):
        if len(devs) >= 2:
            constraints.append(
                f"SHARED-SRC (net {net}): "
                + " <-> ".join(devs)
            )

    return constraints


# ---------------------------------------------------------------------------
# SPICE Device Summary Reporter
# ---------------------------------------------------------------------------
def _report_spice_devices(
    spice_nets: Dict[str, dict],
    constraints: List[str]
) -> None:
    """
    Append a formatted device inventory block to the constraints list.
    """
    constraints.append("=== DEVICES FROM SPICE NETLIST ===")

    for dev_id, nets in sorted(spice_nets.items()):
        nf    = nets.get("nf",    "1")
        nfin  = nets.get("nfin",  "1")
        l_val = nets.get("l",     "?")
        w_val = nets.get("w",     "?")
        model = nets.get("model", "?")
        diode = (
            " [diode]"
            if nets.get("D", "").upper() == nets.get("G", "").upper()
            else ""
        )
        constraints.append(
            f"  {dev_id}: D={nets['D']} G={nets['G']} "
            f"S={nets['S']} B={nets['B']} "
            f"model={model} nf={nf} nfin={nfin} l={l_val} w={w_val}{diode}"
        )

    constraints.append("")


# ---------------------------------------------------------------------------
# Main Topology Analysis Entry Point
# ---------------------------------------------------------------------------
def analyze_topology(
    nodes: List[dict],
    terminal_nets: dict,
    sp_file_path: Optional[str] = None
) -> str:
    """
    Extract topology constraints from SPICE netlist and/or layout data.

    Priority order for net data:
      PATH A - Direct SPICE parse (_parse_spice_directly)   most reliable
      PATH B - Graph analysis (networkx)                    structural
      PATH B - terminal_nets from layout canvas             may be stale
    """
    from ai_agent.finger_grouping import (
        group_fingers,
        aggregate_to_logical_devices,
    )

    constraints: List[str] = []
    used_spice = False

    _log(
        f"analyze_topology: {len(nodes)} nodes, "
        f"{len(terminal_nets)} terminal_nets, "
        f"sp_file={sp_file_path!r}"
    )

    # =====================================================================
    # PATH A: Direct SPICE parse - most reliable source
    # =====================================================================
    spice_nets = _parse_spice_directly(sp_file_path)

    if spice_nets:
        used_spice = True
        _log(f"PATH A: Using direct SPICE parse ({len(spice_nets)} devices)")

        _report_spice_devices(spice_nets, constraints)

        constraints.append("=== CURRENT MIRRORS ===")
        mirror_constraints = _infer_mirrors_from_spice(spice_nets, nodes)
        if mirror_constraints:
            constraints.extend(mirror_constraints)
        else:
            constraints.append("  None detected")
        constraints.append("")

        diff_constraints = _infer_diffpairs_from_spice(spice_nets, nodes)
        if diff_constraints:
            constraints.append("=== DIFFERENTIAL PAIRS ===")
            constraints.extend(diff_constraints)

    # =====================================================================
    # PATH B: Fallback - graph analysis + layout terminal_nets
    # =====================================================================
    else:
        _log("PATH B: SPICE file unavailable - using fallback analysis")

        finger_groups  = group_fingers(nodes)
        logical_nodes  = aggregate_to_logical_devices(nodes)

        multi_finger = {
            base: fingers
            for base, fingers in finger_groups.items()
            if len(fingers) > 1
        }

        if multi_finger:
            constraints.append("=== MULTI-FINGER DEVICES (from layout) ===")
            for base_name, finger_list in multi_finger.items():
                finger_ids = [f["id"] for f in finger_list]
                constraints.append(
                    f"  {base_name} (nf={len(finger_list)}): "
                    f"{', '.join(finger_ids)}"
                )
            constraints.append("")

        logical_terminal_nets = _aggregate_terminal_nets(
            terminal_nets, finger_groups
        )

        graph_constraints = _try_graph_analysis(sp_file_path, logical_nodes)
        if graph_constraints:
            _log(
                f"PATH B: graph analysis: "
                f"{len(graph_constraints)} constraints"
            )
            constraints.extend(graph_constraints)

        elif logical_terminal_nets:
            _log(
                f"PATH B: terminal_nets fallback "
                f"({len(logical_terminal_nets)} logical devices)"
            )
            constraints.extend(
                _infer_from_terminal_nets(logical_terminal_nets, logical_nodes)
            )

        elif terminal_nets:
            _log("PATH B: last-resort raw terminal_nets inference")
            constraints.extend(
                _infer_from_terminal_nets(terminal_nets, nodes)
            )

        else:
            constraints.append(
                "  No net data available "
                "(no SPICE file, no terminal_nets, no graph)"
            )

    # =====================================================================
    # ROW SUMMARY - always computed from layout nodes
    # =====================================================================
    logical_nodes = aggregate_to_logical_devices(nodes)

    pmos_ids = [
        n["id"] for n in logical_nodes
        if str(n.get("type", "")).lower().startswith("p")
    ]
    nmos_ids = [
        n["id"] for n in logical_nodes
        if str(n.get("type", "")).lower().startswith("n")
        and not n.get("is_dummy")
    ]

    # =====================================================================
    # BUILD FINAL OUTPUT TEXT
    # =====================================================================
    lines: List[str] = []

    if pmos_ids:
        lines.append(
            f"PMOS row ({len(pmos_ids)} logical device(s)): "
            + ", ".join(pmos_ids[:12])
            + (" ..." if len(pmos_ids) > 12 else "")
        )

    if nmos_ids:
        lines.append(
            f"NMOS row ({len(nmos_ids)} logical device(s)): "
            + ", ".join(nmos_ids[:12])
            + (" ..." if len(nmos_ids) > 12 else "")
        )

    if not pmos_ids and not nmos_ids:
        lines.append(
            f"Device rows: {len(logical_nodes)} logical devices "
            f"(type unknown - check node type field)"
        )

    source_label = (
        f"[source: {os.path.basename(sp_file_path)}]"
        if used_spice and sp_file_path
        else "[source: layout terminal_nets fallback]"
    )
    lines.append(f"\nTopology constraints {source_label}:")

    if constraints:
        for c in constraints[:40]:
            lines.append(f"  {c}")
        if len(constraints) > 40:
            lines.append(f"  ... ({len(constraints) - 40} more lines)")
    else:
        lines.append("  No topology constraints extracted.")
        file_status = (
            "found"
            if sp_file_path and os.path.isfile(sp_file_path)
            else "not found"
        )
        lines.append(
            f"  Debug: sp_file={file_status}, "
            f"terminal_nets={len(terminal_nets)} entries, "
            f"nodes={len(nodes)}"
        )

    result = "\n".join(lines)
    _log(f"analyze_topology result: {len(result)} chars")
    return result
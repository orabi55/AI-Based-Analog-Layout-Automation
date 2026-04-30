"""
File Description:
This module defines the Placement Specialist agent and its core logic for generating layout commands. It includes tools for computing matching groups, deterministic row assignments, and building the detailed context required for high-quality analog placement.

Functions:
- create_placement_specialist_agent:
    - Role: Configures the placement specialist agent with a specific framework and middleware stack.
    - Inputs: 
        - middlewares (List[object], optional): List of SkillMiddleware or other objects.
    - Outputs: (Dict[str, object]) Agent configuration dictionary.
- validate_no_overlaps:
    - Role: Validates that no two devices in the placement share identical X, Y coordinates.
    - Inputs: 
        - nodes (List[Dict]): List of placed device nodes.
    - Outputs: (Tuple[bool, str]) Success flag and validation summary.
- _load_all_skills:
    - Role: Pre-loads all layout skills from the markdown repository to provide the LLM with comprehensive domain knowledge.
    - Inputs: None
    - Outputs: (str) Combined text of all layout skills.
- _compute_matching_and_rows:
    - Role: Executes the full finger_grouper pipeline to determine matching clusters, ABBA interdigitation, and bin-packed row assignments.
    - Inputs: 
        - nodes (list), edges (list), terminal_nets (dict), no_abutment (bool).
    - Outputs: (tuple) containing group nodes, finger map, row summary, matching section, finger inventory, and merged blocks.
- build_placement_context:
    - Role: Constructs a comprehensive, structured text context for the Placement Specialist agent, including inventory, mandatory rows, and matching rules.
    - Inputs: 
        - nodes, constraints_text (str), terminal_nets (dict), edges (list), spice_nets (list), no_abutment (bool).
    - Outputs: (str) The complete context string for the LLM prompt.
"""

import math
import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple


# System Prompt

PLACEMENT_SPECIALIST_PROMPT = """\
You are the PLACEMENT SPECIALIST in a multi-agent analog IC layout system.

Your task is to assign transistor fingers to a symbolic 2D grid while optimizing:

- Device matching accuracy
- Symmetry (horizontal and vertical)
- Electrical proximity of connected nets
- Parasitic and density balance (placement-level only)

You operate deterministically:
Same input → identical output.

────────────────────────────────────────────
1. CORE DESIGN PRINCIPLE
────────────────────────────────────────────

Placement is a CONSTRAINT SATISFACTION problem.

You DO NOT:
- execute skills sequentially
- overwrite strategies
- concatenate group layouts

You DO:
- compile all constraints (strategies + skills)
- resolve them in strict priority order
- produce a single globally consistent placement

NOTE:
- Strategies represent DESIRED constraints, not guaranteed constraints
- Lower-priority constraints may be relaxed if required by higher-priority ones

────────────────────────────────────────────
2. INPUTS
────────────────────────────────────────────

You receive:

- DEVICE SETS (with nf finger counts)
- TOPOLOGY_GROUPS (electrical grouping)
- STRATEGIES (global constraint specifications)
- SKILL_MAP (local group-level constraint overrides)

────────────────────────────────────────────
3. CONSTRAINT HIERARCHY (ABSOLUTE PRIORITY)
────────────────────────────────────────────

Priority order (highest → lowest):

1) DEVICE CONSERVATION
2) BIAS_CHAIN
3) DIFFERENTIAL_PAIR
4) BIAS_MIRROR
5) COMMON_CENTROID
6) PROXIMITY_NET
7) MATCHED_ENVIRONMENT
8) INTERDIGITATION
9) DIFFUSION_SHARING
10) SIMPLE ORDERING

RULE:
Lower priority constraints may be relaxed ONLY if required to satisfy higher priority constraints.

────────────────────────────────────────────
4. SKILL-MIDDLEWARE CONTRACT
────────────────────────────────────────────

GLOBAL vs LOCAL SKILLS:

GLOBAL skills (always active):
- bias_chain: active when CURRENT_FLOW_GRAPH contains edges
- multirow_placement: active when circuit has multiple row levels

LOCAL skills (group-scoped only):
- bias_mirror
- differential_pair
- common_centroid
- interdigitate
- matched_environment
- diffusion_sharing

Execution rules:
- Global skills apply in Steps 2–3
- Local skills apply in Step 3 per-group only
- Do NOT apply local skills globally
- Do NOT skip global skills due to SKILL_MAP

────────────────────────────────────────────

SKILL RULES:

- Each group may have at most ONE skill
- Skills apply only within assigned group
- Skills define internal structure only
- Skills cannot violate higher-priority constraints

────────────────────────────────────────────

STRUCTURAL CONFLICT RULE:

- differential_pair and common_centroid are mutually exclusive per device set
- bias_mirror overrides both and replaces structure

If DP exists inside CC:
→ split CC domain OR downgrade CC to symmetry constraint

Ordering dominance:
MB > DP > CC > IG

If multiple skills match:
→ select highest priority only

Skill priority:
bias_mirror > differential_pair > common_centroid > interdigitate > multirow_placement

────────────────────────────────────────────
5. GLOBAL EXECUTION PIPELINE (DETERMINISTIC CSP SOLVER)
────────────────────────────────────────────

STEP 0 — PARSE INPUT

PRE-CHECK:
For each group G in SKILL_MAP:
IF skill(G) == differential_pair AND any device has SKILL_HINT:common_centroid:
→ OUTPUT ✗ INVALID:
"Group [G] has conflicting DP and CC assignments. Resolve upstream."

Do NOT resolve internally.

Then:
- Extract devices, groups, strategies, skills

────────────────────────────────────────────

STEP 1 — CONSTRAINT COMPILATION

- Convert skills → local constraints
- Convert strategies → global constraints
- Merge into constraint graph:
  HARD_CONSTRAINTS + SOFT_CONSTRAINTS

────────────────────────────────────────────

STEP 2 — TOPOLOGY STRUCTURING

Apply BIAS_CHAIN and MULTIROW:

IF bias_chain active:
- row assignment derived ONLY from bias_chain levels
- multirow becomes alignment constraint only

bias_chain overrides multirow ordering

Create vertical ordering skeleton

────────────────────────────────────────────

STEP 3 — GROUP INTERNAL STRUCTURING

For each group:

IF skill exists:
→ apply skill constraints internally only

ELSE:
→ apply strategy constraints as soft guidance

────────────────────────────────────────────

REFINEMENT MODELS:

matched_environment:
- computed from edge_distance + local_density AFTER Step 5
- post-placement refinement only

diffusion_sharing:
- computed from adjacency AFTER Step 5
- post-placement compaction only

Do NOT block Steps 1–5.

────────────────────────────────────────────

STEP 4 — GLOBAL PLACEMENT SOLVER

- Merge all groups into single layout
- Solve constraint graph in priority order

NOT concatenation → constraint reconciliation

TIE-BREAKING (deterministic):

1. Higher group priority first
2. device_id ascending
3. finger index ascending (f0 before f1)
4. leftmost slot first

────────────────────────────────────────────

STEP 5 — SLOT ASSIGNMENT (NO DIRECT COORDINATES)

- Assign integer slots per row (0, 1, 2...)
- Preserve ordering strictly
- Ensure uniqueness
- DUMMY PLACEMENT: Dummies go at the far left OR far right end of their row. Never between active transistors.

────────────────────────────────────────────

STEP 6 — COORDINATE MAPPING (MECHANICAL DERIVATION)

After all slots assigned, convert mechanically:
x = SLOT_INDEX × 0.294
y = row_index

────────────────────────────────────────────
6. VALIDATION RULES (NON-NEGOTIABLE)
────────────────────────────────────────────

GLOBAL:
✓ Each finger appears exactly once
✓ No duplicate slots
✓ No duplicate (x,y) pairs (verify mechanically derived coordinates)

TOPOLOGY:
✓ NMOS/PMOS separation preserved
✓ Bias chain ordering satisfied
✓ DP symmetry preserved

SYMMETRY:
✓ MB exact symmetry
✓ CC centroid tolerance ≤ 0.5 slot
✓ DP strict mirroring

CONNECTIVITY:
✓ High-weight nets spatially clustered

FAIL → ✗ INVALID ONLY

────────────────────────────────────────────
7. OUTPUT FORMAT
────────────────────────────────────────────

1) SKILL_MAP
2) STRATEGY_CONSTRAINTS
3) TOPOLOGY_LEVEL_ASSIGNMENT
4) FINAL ORDER PER ROW
5) SLOT MAP
6) COORDINATES
7) VALIDATION REPORT

IF VALID:

Emit commands in exact JSON format:

[CMD]{"action":"move","device":"MM1_f1","x":0.000,"y":0.000}[/CMD]
[CMD]{"action":"move","device":"MM1_f2","x":0.294,"y":0.000}[/CMD]

Rules:
- one line per finger
- exact JSON inside [CMD] tags
- no ranges
- single contiguous block after validation

IF INVALID:

✗ INVALID
reason summary
no commands

────────────────────────────────────────────
8. FORBIDDEN OPERATIONS
────────────────────────────────────────────

✗ Sequential skill execution
✗ Strategy overwrite
✗ Group concatenation
✗ Bias chain violation
✗ DP asymmetry
✗ Cross-row group splitting
✗ Ignoring connectivity

DUMMY RULE:

✓ Allowed ONLY for:
- matched_environment requirement
- symmetry closure (MB/CC)

────────────────────────────────────────────
9. EXECUTION RULE
────────────────────────────────────────────

- Single-pass constraint solver
- No retries

Hard constraint failure (1–4) → immediate ✗ INVALID

Soft constraint failure (5–10):
→ log RELAXATION EVENT
→ continue solving

Output:
- valid placement OR
- ✗ INVALID only if hard constraint violated

────────────────────────────────────────────
10. MANDATORY ROW ASSIGNMENT RULE
────────────────────────────────────────────

The context you receive contains a section titled:
  "PRE-COMPUTED ROW ASSIGNMENT (MANDATORY — copy Y values exactly)"

YOU MUST:
- Use the EXACT Y values from that table for every [CMD] you emit
- Never collapse all PMOS into one row and all NMOS into one row
  unless the table explicitly shows only 1 PMOS and 1 NMOS row
- If the table shows 3 NMOS rows (y=0.000, y=0.668, y=1.336),
  you MUST place devices at those three Y levels
- The row assignment was done deterministically to achieve a near-square
  aspect ratio; ignoring it produces a wide, non-square layout

X coordinates: compute from slot index
  x = slot_index × 0.294  (non-abutted)
  x = slot_index × 0.070  (abutted within matched block)

────────────────────────────────────────────
11. MATCHED BLOCK RULE (MANDATORY)
────────────────────────────────────────────

The context also contains a section:
  "FIXED MATCHED BLOCKS (pre-interdigitated — treat as single units)"

For each block listed:
- Assign ONE origin X (the leftmost slot of the entire block)
- DO NOT assign individual X values to each finger within the block
- The [CMD] for a matched block is the FIRST finger at origin_x;
  all other fingers will be placed automatically at origin_x + n×pitch
  by the deterministic finger expander that runs after you

YOU MUST emit ONE [CMD] per matched block (not one per finger):
[CMD]{"action":"move","device":"BLOCK_ID","x":origin_x,"y":row_y}[/CMD]
"""

# ---------------------------------------------------------------------------
# Agent creation helper
# ---------------------------------------------------------------------------
def create_placement_specialist_agent(middlewares: Optional[List[object]] = None) -> Dict[str, object]:
  """Create placement specialist agent config with middleware stack.

  Uses 'plain' framework (direct LLM invocation) because the ReAct framework
  produced 0 CMD blocks — the agent was consuming tool calls but never emitting
  the final [CMD] placement output. Plain mode gives full control to the LLM
  to produce CMDs after receiving a comprehensive context with pre-computed
  matching groups, row assignments, and skill knowledge.
  """
  return {
    "name": "placement_specialist",
    "framework": "plain",          # ReAct was broken — produced 0 CMDs
    "system_prompt": PLACEMENT_SPECIALIST_PROMPT,
    "middlewares": list(middlewares or []),
  }


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
def _load_all_skills() -> str:
    """Pre-load all SKILLS markdown bodies and return them as a combined string.

    This replaces progressive disclosure (load_skill tool) with full pre-loading,
    ensuring the LLM has ALL domain knowledge upfront rather than having to call
    a tool it never actually calls.
    """
    from pathlib import Path
    skills_dir = Path(__file__).resolve().parents[2] / "skills"
    if not skills_dir.is_dir():
        return ""

    blocks = []
    for md_path in sorted(skills_dir.glob("*.md")):
        try:
            text = md_path.read_text(encoding="utf-8")
            if text.startswith("---"):
                parts = text.split("---", 2)
                body = parts[2].strip() if len(parts) == 3 else text.strip()
            else:
                body = text.strip()
            blocks.append(f"### SKILL: {md_path.stem}\n{body}")
        except Exception:
            continue
    return "\n\n".join(blocks)


def _is_dummy_node(node: dict) -> bool:
    node_id = str(node.get("id", ""))
    return bool(
        node.get("is_dummy")
        or node_id.startswith(("FILLER_DUMMY_", "DUMMY_matrix_", "EDGE_DUMMY"))
    )


def _compute_matching_and_rows(nodes, edges, terminal_nets, no_abutment=False):
    """Run the finger_grouper pipeline to get matching info and row assignments.

    Returns:
        (group_nodes, finger_map, row_summary_str, matching_section_str)
    """
    try:
        from ai_agent.placement.finger_grouper import (
            group_fingers as fg_group_fingers,
            detect_matching_groups,
            build_matching_section,
            merge_matched_groups,
            pre_assign_rows,
            build_finger_group_section,
        )

        active_nodes = [n for n in nodes if not _is_dummy_node(n)]

        # Step 1: collapse fingers to groups
        group_nodes, group_edges, finger_map = fg_group_fingers(active_nodes, edges or [])

        # Step 2: build group-level terminal nets (needed for matching)
        grp_member_ids: dict = {}
        for gid, members in finger_map.items():
            grp_member_ids[gid] = [m.get("id", "") for m in members]

        # Aggregate terminal nets at group level
        _POWER = {"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS", ""}
        group_terminal_nets: dict = {}
        for gid, member_ids in grp_member_ids.items():
            g_nets, d_nets, s_nets = [], [], []
            for mid in member_ids:
                tn = (terminal_nets or {}).get(mid, {})
                if tn.get("G", "") and tn["G"].upper() not in _POWER:
                    g_nets.append(tn["G"])
                if tn.get("D", "") and tn["D"].upper() not in _POWER:
                    d_nets.append(tn["D"])
                if tn.get("S", "") and tn["S"].upper() not in _POWER:
                    s_nets.append(tn["S"])
            # Most common net for each terminal
            def _most_common(lst):
                if not lst: return ""
                from collections import Counter
                return Counter(lst).most_common(1)[0][0]
            group_terminal_nets[gid] = {
                "G": _most_common(g_nets),
                "D": _most_common(d_nets),
                "S": _most_common(s_nets),
            }

        # Step 3: detect matching
        matching_info = detect_matching_groups(group_nodes, group_edges)

        # Step 4: merge matched pairs (ABBA interdigitation)
        group_nodes, group_edges, finger_map, merged_blocks = merge_matched_groups(
            group_nodes, group_edges, finger_map,
            matching_info, group_terminal_nets, terminal_nets or {},
            no_abutment=no_abutment
        )

        # Step 4.5: Update group_terminal_nets for merged blocks
        # This ensures row assignment and matching strings see the aggregate connectivity
        for gn in group_nodes:
            if gn.get("_matched_block") and "terminal_nets" in gn:
                group_terminal_nets[gn["id"]] = gn["terminal_nets"]

        # Step 5: pre-assign rows (deterministic bin-packing)
        group_nodes, row_summary_str = pre_assign_rows(
            group_nodes,
            max_row_width=8.0,
            matching_info=matching_info,
            group_terminal_nets=group_terminal_nets,
        )

        # Step 6: build matching section string
        matching_section_str = build_matching_section(
            group_nodes, group_edges, group_terminal_nets
        )

        # Step 7: build finger group section
        finger_group_str = build_finger_group_section(finger_map, group_nodes)

        return group_nodes, finger_map, row_summary_str, matching_section_str, finger_group_str, merged_blocks
    except Exception as exc:
        import traceback
        # Write to log so failures are visible during pipeline execution
        try:
            from ai_agent.utils.logging import vprint
            vprint(f"[_compute_matching_and_rows] ERROR: {exc}")
            vprint(traceback.format_exc())
        except Exception:
            pass
        return [], {}, "", "", "", {}


def build_placement_context(
    nodes,
    constraints_text="",
    terminal_nets=None,
    edges=None,
    spice_nets=None,
    no_abutment=False,
):
    """Build a comprehensive context string for the Placement Specialist LLM.

    Includes pre-computed row assignments, matched groups (ABBA), skill knowledge,
    and net adjacency info so the LLM receives complete domain knowledge upfront.
    """
    lines = ["=" * 60, "CURRENT LAYOUT INVENTORY", "=" * 60]

    finger_pattern = re.compile(r"^(?P<base>.+)_f(?P<idx>\d+)$", re.IGNORECASE)

    # ── Pre-compute matching groups and row assignments ──────────────────────
    # This is the core fix: run finger_grouper pipeline to get symmetry-aware
    # multi-row assignment BEFORE giving the LLM any context.
    group_nodes, finger_map, row_summary_str, matching_section_str, finger_group_str, merged_blocks = \
        _compute_matching_and_rows(nodes, edges, terminal_nets, no_abutment=no_abutment)

    active_fingers = [n for n in nodes if not _is_dummy_node(n)]
    dummy_ids = [n["id"] for n in nodes if _is_dummy_node(n)]

    lines.append(f"\nTOTAL FINGER INSTANCE COUNT : {len(active_fingers)}")
    lines.append(f"TOTAL LOGICAL DEVICE COUNT  : {len(group_nodes)}")

    # Inventory summary
    logical_ids = sorted([g["id"] for g in group_nodes])
    lines.append(f"LOGICAL TRANSISTORS ({len(logical_ids)}): " + ", ".join(logical_ids))
    lines.append(
        f"FLUID DUMMIES ({len(dummy_ids)}): "
        + (", ".join(dummy_ids) if dummy_ids else "none")
    )
    lines.append("")

    lines.append("DEVICE -> FINGER INSTANCES MAP:")
    # Sort for deterministic prompt
    sorted_groups = sorted(group_nodes, key=lambda g: (g.get("type", ""), g["id"]))
    for gn in sorted_groups:
        gid = gn["id"]
        fingers = finger_map.get(gid, [])
        f_ids = [f.get("id", "?") for f in fingers]
        lines.append(
            f"  {gid:<14} fingers={len(f_ids):<2} -> " + ", ".join(f_ids)
        )
    lines.append("")

    if finger_group_str:
        lines.append(finger_group_str)
        lines.append("")

    if row_summary_str:
        lines.append("=" * 60)
        lines.append("PRE-COMPUTED ROW ASSIGNMENT (MANDATORY — copy Y values exactly)")
        lines.append("=" * 60)
        lines.append(row_summary_str)
        lines.append("")
        lines.append("CRITICAL: Use ONLY the Y values above. Each row Y is pre-computed")
        lines.append("to enforce a near-square aspect ratio. DO NOT collapse all devices")
        lines.append("into just 2 rows. Multiple NMOS rows and PMOS rows are expected.")
        lines.append("")
    else:
        # Fallback: show existing Y values from raw nodes
        pmos_ys = sorted(set(
            round(n["geometry"]["y"], 6) for n in nodes
            if str(n.get("type", "")).lower().startswith("p")
        ))
        nmos_ys = sorted(set(
            round(n["geometry"]["y"], 6) for n in nodes
            if str(n.get("type", "")).lower().startswith("n")
        ))
        lines.append("ROW Y-VALUE REFERENCE (copy these exactly into move CMDs):")
        for y in pmos_ys:
            row_nodes = [n["id"] for n in nodes
                         if str(n.get("type", "")).lower().startswith("p")
                         and abs(n["geometry"]["y"] - y) < 1e-4]
            lines.append(f"  PMOS row  y = {y:.6f}   (fingers: {', '.join(sorted(row_nodes)[:10])}{'...' if len(row_nodes)>10 else ''})")
        for y in nmos_ys:
            row_nodes = [n["id"] for n in nodes
                         if str(n.get("type", "")).lower().startswith("n")
                         and abs(n["geometry"]["y"] - y) < 1e-4]
            lines.append(f"  NMOS row  y = {y:.6f}   (fingers: {', '.join(sorted(row_nodes)[:10])}{'...' if len(row_nodes)>10 else ''})")
        lines.append("")

    if matching_section_str:
        lines.append("=" * 60)
        lines.append("SYMMETRY & MATCHING CONSTRAINTS (pre-computed — MANDATORY)")
        lines.append("=" * 60)
        lines.append(matching_section_str)
        lines.append("")

    if finger_group_str:
        lines.append("=" * 60)
        lines.append("TRANSISTOR GROUPS & FOOTPRINTS")
        lines.append("=" * 60)
        lines.append(finger_group_str)
        lines.append("")

    if merged_blocks:
        lines.append("=" * 60)
        lines.append("FIXED MATCHED BLOCKS (pre-interdigitated — treat as single units)")
        lines.append("=" * 60)
        for bid, info in merged_blocks.items():
            technique_label = {
                "ABBA_diff_pair": "ABBA differential pair",
                "ABBA_current_mirror": "ABBA current mirror",
                "symmetric_cross_coupled": "symmetric cross-coupled latch",
                "common_centroid_mirror": "2D Common Centroid Matrix",
                "ABAB_load_pair": "ABAB Active Load Pair",
            }.get(info.get("technique", ""), info.get("technique", "matched"))
            members_str = " + ".join(info.get("members", []))
            nfin = info.get("total_fingers", "?")
            pitch = "abutted (0.070µm)" if info.get("use_abutment") else "standard (0.294µm)"
            lines.append(f"  BLOCK: {bid}")
            lines.append(f"    Technique : {technique_label}")
            lines.append(f"    Contains  : {members_str}")
            lines.append(f"    Fingers   : {nfin}  |  Pitch: {pitch}")
            if "matrix_data" in info and info["matrix_data"]:
                try:
                    from ai_agent.placement.centroid_generator import format_matrix_for_prompt
                    lines.append(f"    Matrix Topology ({info['matrix_data']['cols']}x{info['matrix_data']['rows']}):")
                    lines.append(format_matrix_for_prompt(info["matrix_data"]))
                except ImportError:
                    pass
            lines.append(f"    RULE: Assign ONE origin (X, Y) for this ENTIRE block. DO NOT split it.")
        lines.append("")

    # Redacted: exhaustive per-finger list removed to avoid context overflow.
    # LLM should focus on LOGICAL TRANSISTORS list above.

    # Per-finger detail list removed for brevity.

    if constraints_text:
        lines.append("=" * 60)
        lines.append("TOPOLOGY CONSTRAINTS (from Topology Analyst — Stage 1)")
        lines.append("=" * 60)
        lines.append(constraints_text)
        lines.append("")

    # ── Compact analog key rules (NOT full skills — avoids context overflow) ──
    # Full skill bodies (~42KB) caused the LLM to hang on large prompts.
    # Instead we embed the 10 most critical layout rules concisely.
    lines.append("=" * 60)
    lines.append("KEY ANALOG LAYOUT RULES (apply strictly)")
    lines.append("=" * 60)
    lines.append("""\
1. ROW Y VALUES: Use ONLY the values from PRE-COMPUTED ROW ASSIGNMENT above.
   Never put all PMOS in one row and all NMOS in one row when the table shows multiple rows.

2. DIFF PAIR (ABBA): Place M1 and M2 fingers interleaved: M1a M2a M2b M1b.
   Both devices must share the same Y row and be horizontally adjacent.

3. CURRENT MIRROR: Mref and Mcopy must be adjacent in the same row.
   If nf differs use common-centroid: Mref Mcopy Mcopy Mref.

4. MATCHED BLOCKS: Any block listed in FIXED MATCHED BLOCKS gets ONE [CMD].
   Use the block ID as the device name and assign only the origin X.
   Do NOT emit individual finger CMDs for matched blocks.

5. NMOS/PMOS SEPARATION: All NMOS Y values must be strictly less than all PMOS Y values.

6. NO OVERLAP: Each (x, y) coordinate must be unique. x = slot * 0.294.

7. DEVICE CONSERVATION: Every finger instance in IMMUTABLE TRANSISTORS must appear
   in exactly one [CMD]. No additions, no deletions.

8. SQUARE ASPECT RATIO: Aim for width ≈ height. If >3 NMOS rows are assigned,
   keep each row width ≤ 8µm by splitting devices across rows as shown in the table.
""")
    lines.append("")

    return "\n".join(lines)

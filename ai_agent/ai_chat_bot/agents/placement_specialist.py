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

- Each group may have at most ONE skill, Refinement skills (diffusion_sharing, matched_environment) are EXEMPT from the one-skill limit. They apply to all valid groups post-placement.
- Skills apply only within assigned group
- Skills define internal structure only
- Skills cannot violate higher-priority constraints

────────────────────────────────────────────

STRUCTURAL CONFLICT RULE:

- differential_pair and common_centroid are mutually exclusive per device set
- bias_mirror overrides both and replaces structure

If DP exists inside CC:
→ split CC domain OR downgrade CC to symmetry constraint

Ordering dominance: DP > MB > CC > IG

If multiple skills match:
→ select highest priority only

Skill priority: 
differential_pair > bias_mirror > common_centroid > interdigitate > multirow_placement

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
4. topological center-out ordering

────────────────────────────────────────────

STEP 5 — SLOT ASSIGNMENT
-Assign integer slots per row. Dynamically expand grid indices to accommodate DUMMY insertions from matched_environment before passing to Step 6.
- Preserve ordering strictly
- Ensure uniqueness

────────────────────────────────────────────

STEP 6 — COORDINATE MAPPING

x = slot × constant_pitch  
y = row_index

────────────────────────────────────────────
6. VALIDATION RULES (NON-NEGOTIABLE)
────────────────────────────────────────────

GLOBAL:
✓ Each finger appears exactly once
✓ No duplicate slots
✓ No overlaps

TOPOLOGY:
✓ NMOS/PMOS separation preserved
✓ Bias chain ordering satisfied
✓ DP symmetry preserved

SYMMETRY:
✓ MB exact symmetry
✓ CC centroid exact parity (ε = 0.0)
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

Emit commands:

[CMD] PLACE <device_name> ROW=<row_index> SLOT=<slot_index> X=<x> Y=<y>

Rules:
- one line per finger
- no batching
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
"""

# ---------------------------------------------------------------------------
# Agent creation helper
# ---------------------------------------------------------------------------
def create_placement_specialist_agent(middlewares: Optional[List[object]] = None) -> Dict[str, object]:
  """Create placement specialist agent config with middleware stack."""
  return {
    "name": "placement_specialist",
    "framework": "react",
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
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

You do NOT:
- execute skills sequentially
- overwrite strategies
- or concatenate group layouts

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

Hardest constraints MUST NEVER be violated.

Priority order (highest → lowest):

1) DEVICE CONSERVATION
2) BIAS_CHAIN
3) DIFFERENTIAL PAIR
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

Skills are LOCAL CONSTRAINT MODIFIERS applied per GROUP.

Rules:

- Each group may have at most ONE skill
- Skills apply ONLY inside their assigned group
- Skills define INTERNAL structure constraints
- Skills CANNOT violate higher-priority global constraints
- Skills do NOT control global ordering directly

STRUCTURAL INCOMPATIBILITY RULE:

- differential_pair and common_centroid are mutually exclusive on the same device set
- bias_mirror overrides both and replaces their structure
- if DP exists inside a CC group:
    → split CC domain OR downgrade CC to a local symmetry constraint

- Only ONE structural pattern may define ordering:
    MB > DP > CC > IG

If multiple skills match a group:
→ select highest priority skill only

Skill priority:
  bias_mirror > differential_pair > common_centroid > interdigitate > multirow_placement

────────────────────────────────────────────
5. GLOBAL EXECUTION PIPELINE (DETERMINISTIC CSP SOLVER)
────────────────────────────────────────────

STEP 0 — PARSE INPUT
- Extract devices, groups, strategies, skills

STEP 1 — CONSTRAINT COMPILATION
- Convert all skills → local constraints
- Convert all strategies → global constraints
- Merge into unified constraint graph:
    HARD_CONSTRAINTS + SOFT_CONSTRAINTS

STEP 2 — TOPOLOGY STRUCTURING

- Apply BIAS_CHAIN and MULTIROW constraints

BIAS_CHAIN OVERRIDES MULTIROW:

- If bias_chain is active:
    - row assignment is derived ONLY from bias_chain levels
    - multirow_placement becomes a grouping/alignment constraint ONLY

- Multirow may NOT override bias_chain vertical ordering

- Establish vertical ordering skeleton (row assignment)

STEP 3 — GROUP INTERNAL STRUCTURING

For each group:

  IF skill exists:
    apply skill constraints internally ONLY

  ELSE:
    apply strategy constraints (as soft guidance)

STEP 4 — GLOBAL PLACEMENT SOLVER

- Merge all group structures into one global layout
- Solve constraint graph:
    highest priority constraints satisfied first
    lower priority optimized under them

IMPORTANT:
This step is NOT concatenation.
It is constraint reconciliation.

STEP 5 — SLOT ASSIGNMENT

- Assign discrete integer slots per row
- Preserve resolved ordering strictly
- Ensure uniqueness of all slots

STEP 6 — COORDINATE MAPPING

x = slot × constant_pitch  
y = row_index

STEP 7 — VALIDATION (STRICT HARD CHECK)

────────────────────────────────────────────
6. VALIDATION RULES (NON-NEGOTIABLE)
────────────────────────────────────────────

GLOBAL VALIDATION:

✓ Every finger appears exactly once  
✓ No duplicate slot assignment  
✓ No overlaps  

TOPOLOGY VALIDATION:

✓ NMOS/PMOS separation preserved  
✓ Bias chain ordering satisfied  
✓ Differential pairs remain symmetric  

SYMMETRY VALIDATION:

✓ MB symmetry exact  
✓ CC centroid variance within tolerance (≤ 0.5 slot)  
✓ DP pairs strictly mirrored  

CONNECTIVITY VALIDATION:

✓ High-weight nets are spatially clustered (relative check)  
✓ No extreme separation of strongly connected nodes  

FAIL → OUTPUT “✗ INVALID” ONLY

────────────────────────────────────────────
7. OUTPUT FORMAT
────────────────────────────────────────────

1) SKILL_MAP  
2) STRATEGY_CONSTRAINTS  
3) TOPOLOGY_LEVEL_ASSIGNMENT (rows)  
4) FINAL ORDER PER ROW  
5) SLOT MAP  
6) COORDINATES  
7) VALIDATION REPORT  

IF VALID:
  emit [CMD] move commands

IF INVALID:
  ✗ INVALID  
  reason summary  
  no commands  

────────────────────────────────────────────
8. FORBIDDEN OPERATIONS
────────────────────────────────────────────

✗ Treating skills as independent execution steps  
✗ Ignoring constraint hierarchy  
✗ Flattening groups via concatenation  
✗ Violating bias chain ordering  
✗ Breaking differential pair symmetry  
✗ Allowing group splitting across rows  
✗ Ignoring net connectivity constraints  

DUMMY INSERTION RULE:

✗ Arbitrary dummy insertion  

✓ Dummy insertion is allowed ONLY when:
   - required by matched_environment skill
   - required for symmetry boundary closure (MB / CC)

────────────────────────────────────────────
9. EXECUTION RULE
────────────────────────────────────────────

- Fail fast  
- No retries  
- No partial corrections  
- Only produce fully constraint-compliant solution or INVALID  

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
---
id: multirow_placement
name: Multi-Row Topology Partitioning

trigger:
  keywords: ["multirow", "row partitioning", "topology"]

inputs:
  - TOPOLOGY_GROUPS (mandatory)
  - SYMMETRY_CONSTRAINTS (CC / MB / DP awareness required)
  - BIAS_CHAIN_LEVELS (optional but overrides row logic if present)

────────────────────────────────────
SCOPE
────────────────────────────────────

- Operates at GROUP LEVEL (not device level)
- Assigns topology groups to vertical placement levels (rows)
- Defines ONLY initial row feasibility structure
- Must remain consistent with bias_chain ordering if present

────────────────────────────────────
CORE PRINCIPLE
────────────────────────────────────

Multirow is NOT an independent layout decision.

It is a CONSTRAINT COMPATIBILITY MAPPING between:

- electrical topology groups
- vertical bias-chain structure
- symmetry domains (CC / MB / DP)

Final row assignment is always subordinate to:
    BIAS_CHAIN (if defined)

────────────────────────────────────
BEHAVIOR
────────────────────────────────────

- Group devices into vertical row candidates
- Ensure NMOS/PMOS separation is respected
- Preserve symmetry domain alignment feasibility
- Provide row feasibility map, not final geometry

────────────────────────────────────
ALGORITHM
────────────────────────────────────

step_1_build_group_graph:

  construct graph G where:

    nodes = TOPOLOGY_GROUPS

    edges = constraints:
      - bias dependencies (from BIAS_CHAIN if available)
      - symmetry coupling (CC / MB / DP)
      - proximity coupling (soft constraint hints)

step_2_extract_bias_levels (IF AVAILABLE):

  if BIAS_CHAIN exists:

    use bias_chain levels as PRIMARY vertical scaffold

    row_base(Gi) = L(Gi)

  else:

    compute provisional levels using dependency depth:
      row_base(Gi) = longest_path_from_sources(Gi)

step_3_classify_groups:

  SYMMETRY_CRITICAL:
    - CC groups
    - MB groups
    - DP groups (treated as pair-locked units)

  STRUCTURAL:
    - cascode
    - gain stages
    - current mirrors

  SUPPORT:
    - bias blocks
    - clocking
    - auxiliary circuits

step_4_feasible_row_assignment:

  for each group Gi:

    assign candidate row set R(Gi):

      R(Gi) must satisfy:

        1) electrical ordering (bias_chain if exists)
        2) NMOS/PMOS domain consistency
        3) symmetry compatibility constraints

step_5_symmetry_alignment_projection:

  for each symmetry domain D:

    if D is CC or MB:

      enforce:

        all groups in D must have:
          aligned row feasibility sets

    meaning:
      R(Gi) ∩ R(Gj) ≠ ∅ for all Gi, Gj in domain

step_6_constraint_reconciliation:

  resolve conflicts using priority:

    1) BIAS_CHAIN (hardest)
    2) DP / MB symmetry (hard)
    3) CC symmetry (hard)
    4) NMOS/PMOS separation (hard)
    5) structural grouping
    6) proximity hints

step_7_final_row_map_generation:

  produce:

    row_feasible_map:
      group → allowed rows

  NOT final placement

────────────────────────────────────
CONSTRAINTS
────────────────────────────────────

- Each group must have at least one feasible row
- No group splitting across incompatible bias levels
- NMOS and PMOS must remain in separate vertical domains
- CC / MB / DP groups must maintain overlapping feasible row sets
- Must not contradict bias_chain ordering if present
- Rows are feasibility constraints, not final assignments

────────────────────────────────────
VALIDATION (STRICT)
────────────────────────────────────

VALID IF:

1) Feasibility:
   every group has at least one valid row assignment

2) Electrical consistency:
   bias_chain ordering not violated in row feasibility

3) Symmetry consistency:
   CC / MB / DP groups have overlapping row domains

4) Domain correctness:
   NMOS and PMOS remain separated

FAIL → ✗ INVALID IF:

- any group has empty feasible row set
- bias ordering contradiction exists
- symmetry domain has no valid alignment overlap
- NMOS/PMOS mixing occurs in same row domain

────────────────────────────────────
FORBIDDEN
────────────────────────────────────

✗ Treating multirow as final placement
✗ Overriding bias_chain row assignment
✗ Forcing CC/MB to define rows independently
✗ Splitting symmetry groups across incompatible domains
✗ Ignoring electrical dependency structure
✗ Assigning deterministic final coordinates

────────────────────────────────────
RELATIONSHIP TO OTHER SKILLS
────────────────────────────────────

- SUBORDINATE to BIAS_CHAIN (hard override)
- PROVIDES constraints to Placement Specialist only
- CC / MB / DP define coupling constraints, not row ownership
- PROXIMITY_NET is NOT considered at this stage (too early)
- Final geometry is NOT decided here

────────────────────────────────────
OUTPUT REQUIREMENT
────────────────────────────────────

Must produce:

- group → feasible row set mapping
- bias-consistent vertical structure
- symmetry compatibility report
- constraint conflict resolution summary
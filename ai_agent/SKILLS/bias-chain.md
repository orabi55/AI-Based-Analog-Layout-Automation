---
id: bias_chain
name: Current Flow / Bias Chain Ordering

trigger:
  keywords:
    [
      "bias", "current mirror", "cascode", "latch", "tail", "stack",
      "current flow", "biasing", "vdd to gnd chain", "stacking"
    ]

scope:
  - topology_chain_groups
  - vertical_stack_structures
  - NMOS_and_PMOS_separately (per row domain)

inputs:
  - CURRENT_FLOW_GRAPH (MANDATORY)
    description:
      Directed graph of electrical dependency:
        node A → node B means A provides bias current to B

────────────────────────────────────
CORE PURPOSE
────────────────────────────────────

Enforce VERTICAL ELECTRICAL ORDERING of devices such that:

- current flows monotonically from supply to ground
- bias dependencies are respected in vertical placement
- stacking order reflects real device operation

This is NOT geometric ordering.
This is ELECTRICAL TOPOLOGY PRESERVATION.

────────────────────────────────────
KEY PHYSICAL PRINCIPLE (CRITICAL)
────────────────────────────────────

Vertical ordering depends on DEVICE TYPE:

- NMOS stack:
    current flows from DRAIN → SOURCE → GND
    higher row = closer to VDD

- PMOS stack:
    current flows from SOURCE → DRAIN → VDD
    higher row = closer to VDD

Therefore:

- NMOS:
    upstream (bias source) must be placed ABOVE downstream devices

- PMOS:
    upstream (bias source) must be placed BELOW downstream devices

Bias ordering must respect physical current direction per device type.

────────────────────────────────────
BEHAVIOR
────────────────────────────────────

- Construct a directed electrical dependency graph
- Identify all bias chains (may be multiple independent chains)
- Assign vertical levels based on dependency depth
- Enforce monotonic stack ordering per device type
- Preserve horizontal symmetry constraints ONLY within each level
- Produce a vertical scaffold used by global placement

────────────────────────────────────
ALGORITHM
────────────────────────────────────

step_1_build_dependency_graph:

  for each device Di:
    identify:
      - bias source (gate/drain dependency)
      - load dependency
      - current mirror relationships

  construct directed graph:
    A → B means A provides bias current to B

step_2_detect_cycles:

  if graph contains cycle:
    → ✗ INVALID (bias chain must be acyclic)

step_3_identify_roots:

  roots = all nodes with no incoming edges

  note:
    multiple roots are allowed
    (e.g., separate bias branches, mirrored structures)

step_4_compute_levels (topological layering):

  perform topological sort

  for each node Di:

    L(Di) = max over all predecessors A of:
              L(A) + 1

  if Di has no predecessors:
    L(Di) = 0

  result:
    levels = {L0, L1, L2, ... Ln}

step_5_map_levels_to_rows:

  for each device Di:

    if Di is NMOS:
      row(Di) = L(Di)
      (larger L → physically lower toward GND)

    if Di is PMOS:
      row(Di) = MAX_LEVEL - L(Di)
      (mirrored so higher row = closer to VDD)

  ensures:
    consistent physical interpretation across device types

step_6_within_level_constraints:

  within each level:

    allowed:
      - horizontal reordering
      - symmetry enforcement (CC / MB / DP)
      - interdigitation

  forbidden:
      - any operation that changes vertical level

step_7_stack_consistency_check:

  for every edge A → B:

    if NMOS:
      require row(A) < row(B)

    if PMOS:
      require row(A) > row(B)

────────────────────────────────────
CONSTRAINTS
────────────────────────────────────

- Vertical ordering must strictly follow dependency graph
- No device may violate current flow direction
- Cross-level swaps are forbidden
- Horizontal reordering allowed ONLY within same level
- Multiple independent chains must not interleave vertically if ordering conflicts
- Bias structure dominates ALL other placement constraints

────────────────────────────────────
VALIDATION (STRICT)
────────────────────────────────────

VALID IF:

1) Acyclic graph:
   no cycles in dependency graph

2) Topological correctness:
   for all A → B:

     NMOS: row(A) < row(B)  
     PMOS: row(A) > row(B)

3) Stack monotonicity:
   no reversal of current flow ordering

4) Level consistency:
   all devices in same level share same row

5) Cross-type consistency:
   NMOS and PMOS stacks do not violate global VDD ↔ GND ordering

FAIL → ✗ INVALID IF:

- cycle exists in dependency graph
- any dependency violates row ordering
- device placed before its bias source
- cross-level movement occurs
- mixed NMOS/PMOS ordering breaks physical flow

────────────────────────────────────
FORBIDDEN
────────────────────────────────────

✗ Treating bias as purely geometric constraint  
✗ Ignoring device type polarity (NMOS vs PMOS)  
✗ Allowing CC/MB/IG to override vertical ordering  
✗ Mixing vertical dependency with horizontal symmetry rules  
✗ Placing dependent devices before their sources  
✗ Collapsing multiple levels into one row  

────────────────────────────────────
RELATIONSHIP TO OTHER SKILLS
────────────────────────────────────

- HIGHEST PRIORITY structural constraint after device conservation
- Defines vertical scaffold for ALL placement
- OVERRIDES multirow_placement (row assignment authority)
- Differential Pair must operate within same level
- Common Centroid / Interdigitation apply ONLY horizontally within a level
- Proximity_NET cannot violate vertical ordering
- Matched Environment operates after vertical structure is fixed

────────────────────────────────────
OUTPUT REQUIREMENT
────────────────────────────────────

Must produce:

- level assignment per device (L(Di))
- row assignment per device
- vertical ordering map (stack structure)
- validation report confirming monotonic current flow

"""
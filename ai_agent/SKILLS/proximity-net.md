---
id: proximity_net
name: Connectivity-Based Proximity Optimization

trigger:
  keywords:
    [
      "proximity", "net", "routing distance", "connectivity",
      "wire length", "parasitic", "shared net", "fanout",
      "minimize routing", "adjacent connection"
    ]

scope:
  - full_layout_graph
  - cross_group_connections_allowed

inputs:
  - NETLIST_GRAPH (MANDATORY)
    description:
      Undirected weighted graph:
        nodes = devices
        edges = electrical connections
        weights = connection strength / criticality

────────────────────────────────────
CORE PURPOSE
────────────────────────────────────

Minimize ELECTRICAL INTERCONNECT COST by ensuring:

- strongly connected devices are physically close
- high-weight nets have minimal Manhattan distance
- critical signal paths are locally clustered
- parasitic-sensitive connections are shortened

This is NOT symmetry-based.
This is GRAPH DISTANCE OPTIMIZATION.

────────────────────────────────────
BEHAVIOR
────────────────────────────────────

- Convert netlist into weighted graph
- Identify high-criticality edges
- Pull strongly connected nodes closer in placement space
- Allow controlled violation of geometric uniformity (CC/IG) if net cost dominates
- Optimize local clusters while respecting global constraints (DP, Bias Chain)

────────────────────────────────────
ALGORITHM
────────────────────────────────────

step_1_build_net_graph:

  for each connection (A, B):

    assign weight W(A,B) based on:

      - current magnitude importance
      - signal sensitivity (analog input/output higher)
      - frequency domain relevance (if known)
      - fanout criticality

step_2_compute_attraction_forces:

  for each device Di:

    attraction_vector[Di] =
      sum over all connected Dj:
        W(Di, Dj) × direction_vector(Dj - Di)

step_3_cluster_formation:

  group devices into clusters such that:

    intra_cluster_weight >> inter_cluster_weight

  clusters become placement proximity units

step_4_placement_adjustment:

  for each cluster:

    - compress spatial distance between high-weight nodes
    - preserve CC / MB / IG structure INSIDE cluster if possible
    - allow mild symmetry distortion if net gain is significant

step_5_global_balancing:

  resolve conflicts between:

    - symmetry constraints (CC / MB / IG)
    - vertical constraints (Bias Chain)
    - pair constraints (Differential Pair)

  priority:

    1. Bias Chain (vertical correctness)
    2. Differential Pair (electrical symmetry)
    3. High-weight net proximity
    4. CC / MB / IG refinement

────────────────────────────────────
CONSTRAINTS
────────────────────────────────────

- High-weight nets MUST have minimized Manhattan distance
- Connected devices SHOULD be in same or adjacent clusters
- No long-distance routing allowed for critical nets
- Symmetry may be slightly relaxed ONLY if net weight justifies it
- Cannot violate DP or Bias Chain constraints

────────────────────────────────────
VALIDATION (STRICT)
────────────────────────────────────

VALID IF:

1) Net cost minimization:
   sum(W(A,B) × distance(A,B)) is locally minimized

2) Critical nets:
   highest-weight connections are short-range

3) Cluster integrity:
   strongly connected components remain spatially compact

4) Constraint compliance:
   DP and Bias Chain constraints not violated

FAIL → ✗ INVALID IF:

- high-weight nets are excessively long
- strongly connected devices are far apart without justification
- clustering is ignored
- symmetry is violated without net benefit
- DP or bias ordering is broken

────────────────────────────────────
FORBIDDEN
────────────────────────────────────

✗ Ignoring net weights in placement decisions
✗ Treating layout as purely geometric symmetry problem
✗ Allowing long high-criticality routing paths
✗ Violating bias chain or differential pair constraints for minor net gains
✗ Uniform spacing when connectivity demands clustering

────────────────────────────────────
RELATIONSHIP TO OTHER SKILLS
────────────────────────────────────

- Subordinate to Bias Chain (cannot break vertical stack)
- Subordinate to Differential Pair (cannot break pair symmetry)
- Must STRICTLY obey CC / MB / DP constraints. Proximity optimization operates ONLY within the valid geometric solutions of higher-priority symmetry rules.
- Defines horizontal clustering pressure across full layout

────────────────────────────────────
OUTPUT REQUIREMENT
────────────────────────────────────

Must produce:

- net-weighted cluster map
- proximity-adjusted grouping
- conflict resolution summary with CC/MB/DP/Bias Chain
- final spatial clustering constraints

"""
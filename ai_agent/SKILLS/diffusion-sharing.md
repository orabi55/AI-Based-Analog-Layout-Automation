---
name: Diffusion Sharing Optimization
description: Share diffusion among compatible adjacent devices to reduce area and junction parasitics.
---

────────────────────────────────────
CORE PURPOSE
────────────────────────────────────

Minimize:
- total diffusion boundary count
- parasitic junction area
- silicon footprint

by enabling:
- sharing of diffusion regions between adjacent compatible devices

This is a PHYSICAL OPTIMIZATION SKILL, not a symmetry skill.

────────────────────────────────────
BEHAVIOR
────────────────────────────────────

- Identify adjacent compatible devices in placement
- Merge or abut fingers where electrical type allows
- Reduce redundant diffusion boundaries
- Preserve CC / IG / MB structural constraints while compacting geometry
- Maintain electrical identity of each device

────────────────────────────────────
ALGORITHM
────────────────────────────────────

step_1_identify_candidates:

  for each row:

    group devices by:
      - same type (NMOS/PMOS)
      - same well region
      - compatible orientation

step_2_build_merge_graph:

  create adjacency graph where:

    edge(A, B) exists if:
      - A and B are same device type OR matched pair allowed
      - no CC/MB constraint prevents adjacency
      - Proximity_NET does not conflict strongly

step_3_diffusion_merge_selection:

  for each connected component:

    order devices by:
      (nf DESC, net_weight DESC)

    attempt to place as:

      ABUTTED FINGER ARRAYS:
        AAAAA BBBBB → share boundary

      or INTERLEAVED SHARED STRUCTURE:
        ABABAB → shared diffusion chain

step_4_merge_constraints:

  enforce:

    - shared diffusion only between compatible devices
    - maintain finger count integrity
    - preserve CC / IG / MB ordering constraints locally
    - do NOT break DP pairing structure

step_5_compaction_pass:

  after placement:

    compress horizontal spacing where diffusion is shared

────────────────────────────────────
CONSTRAINTS
────────────────────────────────────

- Only same-type or explicitly compatible devices can share diffusion
- Differential pairs MUST NOT lose mirror integrity
- Common-centroid structures must preserve centroid correctness
- Bias chain vertical ordering must remain untouched
- Proximity_NET constraints still apply (do not break connectivity clustering)

────────────────────────────────────
VALIDATION (STRICT)
────────────────────────────────────

VALID IF:

1) Diffusion reduction achieved:
   number of boundaries minimized compared to naive placement

2) Electrical correctness:
   no DP or CC symmetry broken

3) Finger integrity:
   all nf counts preserved exactly

4) Connectivity preserved:
   Proximity_NET cost not significantly worsened

FAIL → ✗ INVALID IF:

- device count mismatch occurs
- CC / MB symmetry is broken
- DP pairing is violated
- diffusion sharing applied across incompatible devices
- bias chain vertical structure affected

────────────────────────────────────
FORBIDDEN
────────────────────────────────────

✗ Sharing diffusion across different device types incorrectly
✗ Breaking differential pair symmetry for compaction
✗ Ignoring CC/IG structure for aggressive merging
✗ Reducing area at cost of bias chain correctness
✗ Applying diffusion sharing globally without cluster awareness

────────────────────────────────────
RELATIONSHIP TO OTHER SKILLS
────────────────────────────────────

- Subordinate to Bias Chain (vertical structure cannot change)
- Subordinate to Differential Pair (pair symmetry must remain intact)
- Compatible with CC/IG/MB inside local clusters
- Strongly interacts with Proximity_NET (reinforces clustering)
- Acts AFTER symmetry structure is defined
-Acts purely as a post-placement compaction step. Evaluates adjacent assigned slots and merges compatible boundaries without altering the integer slot order or centroid math.
────────────────────────────────────
OUTPUT REQUIREMENT
────────────────────────────────────

Must produce:

- diffusion sharing map (which devices share boundaries)
- compaction plan per cluster
- before/after diffusion reduction estimate
- conflict resolution with CC/MB/DP/Bias Chain

"""
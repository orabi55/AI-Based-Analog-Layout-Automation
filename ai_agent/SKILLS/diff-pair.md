---
id: differential_pair
name: Differential Pair Matching

trigger:
  keywords: ["diff pair", "differential pair", "dp", "v+ v-", "in+ in-", "comparator", "opamp input"]

scope:
  - matched_pair_group_only
  - requires_explicit_pairing_metadata

inputs:
  - PAIR_MAPPING (MANDATORY)
    format:
      (Di+, Di-) pairs must be explicitly defined in topology

────────────────────────────────────
CORE PURPOSE
────────────────────────────────────

Enforce ELECTRICAL symmetry between paired devices by ensuring:

- matched transconductance environment
- symmetric parasitic loading
- equal electrical path conditions (placement-level abstraction)
- mirrored geometric placement about a shared axis

This is NOT centroid matching.
This is PAIRWISE ELECTRICAL EQUIVALENCE under symmetry.

────────────────────────────────────
KEY PHYSICAL PRINCIPLE (CRITICAL)
────────────────────────────────────

Differential pairs must satisfy:

1) geometric symmetry (mirror placement)
2) environmental symmetry (local density + context similarity)
3) electrical symmetry (equal parasitic exposure)

Exact identity of neighbors is NOT required.
Equivalent neighborhood conditions are required.

────────────────────────────────────
BEHAVIOR
────────────────────────────────────

- Treat each (Di+, Di-) pair as a coupled symmetry unit
- Enforce mirrored placement across a shared symmetry axis
- Ensure equivalent (not identical) local environment
- Maintain consistent axis across ALL pairs in group
- Support multi-finger interdigitation inside each pair if required

────────────────────────────────────
ALGORITHM
────────────────────────────────────

step_1_pair_validation:

  verify:
    for every Di+ there exists Di-
    no orphan devices allowed

step_2_pair_sorting:

  order pairs by:
    (nf DESC, net_priority DESC, device_id ASC)

step_3_global_axis_selection:

  if STRATEGY provides axis:
    use it

  else:
    choose SINGLE shared axis for all pairs such that:

      minimizes:
        Σ over all pairs:
          |center(Di+) - mirror(center(Di-))|

  NOTE:
    axis is GLOBAL for the entire DP group

step_4_pair_placement (CORE STEP):

  for each pair (A+, A-):

    enforce:

      position(A+) = mirror(position(A-), axis)

    constraints:

      - equal row level OR equivalent bias-chain level
      - equal distance to symmetry axis
      - equivalent (not identical) local environment
      - consistent adjacency pattern across pairs

step_5_finger_mapping:

  if nf(A+) == nf(A-):

    enforce finger-level symmetry:

      A+_fi ↔ A-_fi mirrored across axis

  else:

    distribute mismatch using symmetric balancing:

      - center-aligned compensation only
      - preserve overall centroid neutrality of pair

step_6_environment_equivalence_check:

  ensure:

    E(A+) ≈ E(A-)

  where E includes:
    - local density
    - edge distance
    - diffusion congestion
    - nearby net density

────────────────────────────────────
CONSTRAINTS
────────────────────────────────────

- Each differential pair must behave as a single symmetric unit
- Mirror symmetry must hold at pair level
- All pairs must share SAME symmetry axis
- Local environments must be equivalent (not identical)
- Pair integrity must be preserved across all placement stages
- Interdigitation within pair is allowed if symmetry preserved

────────────────────────────────────
VALIDATION (STRICT)
────────────────────────────────────

VALID IF:

1) Pair symmetry:
   position(A+) == mirror(position(A-))

2) Axis consistency:
   all pairs use identical symmetry axis

3) Environment equivalence:
   E(A+) ≈ E(A-) within tolerance ε

4) Finger consistency:
   nf(A+) == nf(A-) OR symmetric distribution proven

5) Structural integrity:
   no pair is broken across rows or clusters

FAIL → ✗ INVALID IF:

- any orphan device exists
- mirror mapping is broken
- different pairs use different axes
- strong environmental asymmetry exists
- pair split across incompatible rows or bias levels

────────────────────────────────────
FORBIDDEN
────────────────────────────────────

✗ Treating DP as centroid problem (CC logic is invalid here)
✗ Requiring identical neighbors (overconstrained)
✗ Allowing independent axis per pair
✗ Breaking pair coupling during placement
✗ Violating bias chain vertical constraints
✗ Ignoring environmental mismatch effects

────────────────────────────────────
RELATIONSHIP TO OTHER SKILLS
────────────────────────────────────

- OVERRIDES CC locally when conflict occurs
- MUST respect BIAS_CHAIN vertical ordering
- Compatible with INTERDIGITATION inside pair only
- Compatible with BIAS_MIRROR only if same axis enforced
- Subordinate to DEVICE CONSERVATION and BIAS_CHAIN
- PROXIMITY_NET may adjust placement ONLY if symmetry preserved

────────────────────────────────────
OUTPUT REQUIREMENT
────────────────────────────────────

Must produce:

- explicit (Di+, Di-) mirror mapping
- global symmetry axis definition
- pair-level placement constraints
- environment equivalence validation report
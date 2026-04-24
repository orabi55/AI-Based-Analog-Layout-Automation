---
id: matched_environment
name: Matched Environment Consistency

trigger:
  keywords:
    [
      "matching environment", "layout environment", "mismatch",
      "systematic mismatch", "local variation", "edge effects",
      "thermal gradient", "stress gradient", "lithography variation",
      "environmental symmetry"
    ]

scope:
  - matched_device_sets
  - CC_groups
  - MB_pairs
  - DP_pairs
  - high_precision_analog_blocks

inputs:
  - ENVIRONMENTAL_MODEL (MANDATORY)
    includes:
      - edge proximity
      - density variation
      - thermal gradients
      - stress field direction (if available)
      - well proximity effects

────────────────────────────────────
CORE PURPOSE
────────────────────────────────────

Minimize SYSTEMATIC MISMATCH by ensuring that matched devices:

- experience identical local layout environment
- have symmetric exposure to edges, wells, and dummy structures
- are equally affected by density-dependent process variation

This skill does NOT change geometry symmetry.
It enforces **environmental equivalence beyond symmetry**.

────────────────────────────────────
KEY INSIGHT
────────────────────────────────────

Even if two devices are geometrically symmetric (CC / MB / DP):

They are still mismatched if:

- one is closer to an edge
- one has different dummy density
- one sees different diffusion crowding
- one is near different net congestion

This skill corrects THAT gap.

────────────────────────────────────
BEHAVIOR
────────────────────────────────────

- Evaluate local neighborhood of each matched device
- Ensure environmental parity across matched sets
- Enforce symmetric “layout context shells”
- Introduce or adjust dummy structures if needed
- Balance density, spacing, and adjacency around matched devices

────────────────────────────────────
ALGORITHM
────────────────────────────────────

step_1_identify_matched_sets:

  collect:
    - CC groups
    - MB pairs
    - DP pairs
    - any explicitly matched analog devices

step_2_environment_extraction:

  for each device Di:

    compute environment vector E(Di):

      E(Di) =
        [
          edge_distance,
          local_density,
          neighbor_types,
          diffusion_crowding,
          net_congestion,
          symmetry_axis_offset
        ]

step_3_environmental_matching:

  for each matched set S:

    for all pairs (Di, Dj) in S:

      enforce:

        E(Di) ≈ E(Dj)

      minimize:

        ||E(Di) - E(Dj)||

step_4_dummy_balancing:

  if mismatch detected:

    insert or reposition:
      - dummy devices
      - filler structures
      - spacing adjustments

    goal:
      equalize local density field

step_5_symmetry_preservation_check:

  ensure:

    - CC / MB / DP geometric symmetry remains intact
    - only *environmental context* is adjusted
    - no violation of Bias Chain or Proximity constraints

────────────────────────────────────
CONSTRAINTS
────────────────────────────────────

- Matched devices must have equivalent environmental vectors
- Edge exposure must be balanced across pairs/groups
- Local density must be symmetric around matched sets
- Dummy insertion is allowed ONLY for environmental balancing
- Cannot violate:
    - Bias Chain vertical ordering
    - Differential Pair symmetry
    - CC centroid constraints

────────────────────────────────────
VALIDATION (STRICT)
────────────────────────────────────

VALID IF:

1) Environmental equivalence:
   for all matched sets S:
     variance(E(Di)) within S is minimal

2) Symmetry integrity:
   CC / MB / DP structures remain geometrically valid

3) Edge balance:
   matched devices have equal edge proximity exposure

4) Density balance:
   no device in matched set is in significantly higher congestion region

FAIL → ✗ INVALID IF:

- matched devices have unequal edge exposure
- density gradients are asymmetric within matched sets
- dummy structures do not restore balance
- environmental mismatch persists after placement
- CC/MB symmetry is violated

────────────────────────────────────
FORBIDDEN
────────────────────────────────────
✗ Arbitrary dummy insertion

✓ Dummy insertion ONLY allowed by:
   - matched_environment skill
   - symmetry boundary enforcement (MB/CC)
✗ Ignoring environmental factors in matched devices
✗ Relying purely on geometric symmetry for matching
✗ Allowing edge-biased placement of paired devices
✗ Breaking DP/CC symmetry during correction
✗ Applying corrections globally instead of locally

────────────────────────────────────
RELATIONSHIP TO OTHER SKILLS
────────────────────────────────────

- Subordinate to Differential Pair (must preserve pairing first)
- Subordinate to Common Centroid (centroid first, then environment)
- Subordinate to Bias Chain (vertical order cannot be changed)
- Works WITH Proximity_NET (but refines within clusters)
- Works WITH Diffusion Sharing (balances density post-merge)

────────────────────────────────────
OUTPUT REQUIREMENT
────────────────────────────────────

Must produce:

- environmental vector map per matched device
- mismatch score per matched set
- correction actions (dummy placement / spacing adjustment)
- final environmental parity validation report

"""
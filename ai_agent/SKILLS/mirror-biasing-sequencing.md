---
id: bias_mirror
name: Mirror Bias Symmetry
description: Enforce mirror symmetry for bias mirror device groups.

trigger:
  mode: MB
  keywords: ["mirror", "bias", "current mirror", "matched pair", "symmetry"]

scope:
  - matched_device_set (local symmetry domain only)
  - never applies globally unless explicitly defined as MB group

---

────────────────────────────────────
CORE PURPOSE
────────────────────────────────────

Construct a symmetric placement structure such that:

- every device has a mirrored counterpart (or self-center)
- symmetry holds at both:
    - finger level
    - local neighborhood level
- electrical equivalence is preserved across the symmetry axis

This is a CONSTRAINED SYMMETRY MAPPING problem, not sequence reversal.

────────────────────────────────────
BEHAVIOR
────────────────────────────────────

- Define a symmetry axis for the group
- Assign all fingers to mirrored positions across this axis
- Ensure structural symmetry is preserved locally and globally
- Allow center element only under strict parity conditions

────────────────────────────────────
ALGORITHM
────────────────────────────────────

step_1_expand:

  convert each device Di → set of fingers Fi

step_2_define_symmetry_axis:

  if axis provided by STRATEGY:
    use it

  else:
    choose axis that minimizes:

      sum over all pairs (Fi, Fj):
        |distance(Fi, axis) - distance(Fj, axis)|

step_3_build_finger_pool:

  pool = all fingers grouped by device

step_4_pairwise_symmetric_assignment:

  initialize:

    left_side = []
    right_side = []

  while pool not empty:

    select device Di in deterministic order:
      (nf DESC, device_id ASC)

    while Di has remaining fingers:

      assign next finger pair:

        place Di_fk at leftmost available slot
        place mirrored Di_fk' at symmetric right slot

      ensure:
        positional symmetry about axis is maintained

step_5_center_handling:

  if total_fingers is odd:

    select center finger such that:

      priority:

        1. device with highest remaining unpaired count
        2. tie → smallest device_id

    place at symmetry axis center position

────────────────────────────────────
CONSTRAINTS
────────────────────────────────────

- For every placed element at position x:
    exists mirrored element at position -x

- Device counts must be preserved exactly

- Symmetry is enforced at:
    - spatial position level
    - local adjacency level

- No asymmetric neighborhood patterns allowed

- No device fragmentation across sides

────────────────────────────────────
VALIDATION (REAL MB CRITERIA)
────────────────────────────────────

VALID IF:

1) Mirror correctness:
   for every element E at position x:
     exists identical counterpart at -x

2) Count preservation:
   count(Di) == nf_i for all devices

3) Neighborhood symmetry:
   adjacency(E_left) mirrors adjacency(E_right)

4) Axis consistency:
   single symmetry axis per MB domain

FAIL → ✗ INVALID IF:

- any unmatched mirrored element exists
- adjacency structures differ across axis
- device counts violated
- multiple conflicting symmetry axes exist

────────────────────────────────────
FORBIDDEN
────────────────────────────────────

✗ Treating MB as simple sequence reversal
✗ Block-based half splitting without pairing logic
✗ Forcing center placement without parity requirement
✗ Allowing asymmetric local neighborhoods
✗ Mixing MB domain with CC centroid optimization logic
✗ Ignoring axis consistency constraints

────────────────────────────────────
RELATIONSHIP TO OTHER SKILLS
────────────────────────────────────

- Overrides CC locally (MB is stricter symmetry model)
- Compatible with DP only if DP pairs align with axis
- Subordinate to Bias Chain vertical ordering
- Must be resolved BEFORE IG or diffusion sharing

────────────────────────────────────
OUTPUT REQUIREMENT
────────────────────────────────────

Must produce:

- symmetry axis definition
- mirror mapping per finger
- center assignment (if applicable)
- symmetry validation report
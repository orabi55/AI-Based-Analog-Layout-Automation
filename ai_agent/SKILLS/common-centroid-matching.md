---
id: common_centroid
name: Common-Centroid Matching

trigger:
  mode: CC
  keywords: ["centroid", "common centroid", "cc", "match"]

scope:
  - matched_device_set (NOT full row)
  - all devices within the CC group only

────────────────────────────────────
CORE PURPOSE
────────────────────────────────────

Minimize SYSTEMATIC MISMATCH caused by spatial gradients by:

- cancelling first-order spatial variation (process, temperature, stress)
- equalizing centroid positions of matched devices
- distributing device fingers symmetrically in space

This is NOT just symmetry.
This is GRADIENT CANCELLATION via spatial moment balancing.

────────────────────────────────────
BEHAVIOR 
────────────────────────────────────

- Treat ONLY the matched CC group as a unit
- Construct a MIRROR-SYMMETRIC sequence
- Ensure centroid alignment across all devices
- Distribute fingers to cancel first-order gradients
- Allow interleaving across devices while preserving symmetry

────────────────────────────────────
ALGORITHM 
────────────────────────────────────

step_1_expand:

  Convert each device Di → list of fingers:
    Fi = [Di_f1, Di_f2, ..., Di_fn]

step_2_compute_total:

  N = total number of fingers across all devices

step_3_initialize_positions:

  define discrete positions:
    P = [0, 1, 2, ..., N-1]

  symmetry pairs:
    (i, N-1-i)

step_4_ratio_analysis:

  for each device Di:
    compute ratio:
      r_i = nf_i / N

  determine if canonical patterns apply:

    - 2-device equal ratio → ABBA / ABAB
    - 2-device unequal ratio → ratio-based mirrored interleave
    - >2 devices → generalized centroid balancing

step_5_construct_sequence (CENTROID-DRIVEN)

  goal:
    minimize centroid deviation:
      centroid(Di) ≈ N/2 for all Di

  method:

    initialize empty sequence S of length N

    sort devices by (nf DESC, device_id ASC)

    for each symmetric pair (i, N-1-i):

        select device Di with:

          - remaining fingers
          - largest deficit from ideal centroid position
          - highest remaining nf

        place:
          S[i] = Di_fk
          S[N-1-i] = Di_f(k+1) if available
                       else symmetric placement using same device

        update remaining pool

step_6_center_handling:

  if N is odd:

    select device Di such that:
      placing at center minimizes centroid error

    place at:
      S[N//2]

step_7_optional_pattern_override:

  if known optimal patterns exist:

    - 2-device equal:
        ABBA preferred over naive symmetry

    - ratio-based:
        use mirrored proportional sequences (e.g., AABBAA)

  override generic construction if it improves centroid balance

────────────────────────────────────
CONSTRAINTS
────────────────────────────────────

- Each device Di appears exactly nf_i times
- Sequence must be mirror-symmetric:
    S[i] == S[N - 1 - i]
- Devices may interleave freely IF symmetry is preserved
- No block partitioning (e.g., AAA BBB) allowed
- Placement must minimize centroid deviation globally

────────────────────────────────────
CENTROID DEFINITION (CRITICAL)
────────────────────────────────────

For each device Di:

  centroid(Di) =
    (1 / nf_i) * Σ position(fingers of Di)

Target:

  centroid(Di) ≈ (N - 1) / 2

Objective:

  minimize:
    max_i |centroid(Di) - center|

This replaces pairwise centroid comparison with absolute alignment.

────────────────────────────────────
VALIDATION (REAL CC CRITERIA)
────────────────────────────────────

VALID IF:

1) Symmetry:
   S[i] == S[N - 1 - i]

2) Count preservation:
   count(Di) == nf_i

3) Centroid alignment:
   for all Di:
     |centroid(Di) - center| ≤ ε
   where:
     center = (N - 1) / 2
     ε = 0.0. "Centroid alignment must be mathematically exact based on modulo 2. If exact parity cannot be achieved on the grid or half-grid, it fails."

4) Distribution quality:
   no clustering of same device beyond local symmetry requirement

FAIL → ✗ INVALID if:

- symmetry broken
- device counts violated
- centroid deviation exceeds tolerance
- sequence exhibits clustering (block-like behavior)

────────────────────────────────────
CONSTRAINT INTERACTIONS
────────────────────────────────────

- Subordinate to:
    BIAS_CHAIN (vertical levels must remain unchanged)
    DIFFERENTIAL_PAIR (DP overrides CC for paired devices)

- Compatible with:
    INTERDIGITATION (if symmetry preserved)
    MATCHED_ENVIRONMENT

- Overridden by:
    BIAS_MIRROR (MB replaces CC structure entirely)

────────────────────────────────────
FORBIDDEN
────────────────────────────────────

✗ Treating whole row as CC domain  
✗ Center-expansion (inside-out greedy placement)  
✗ Exact centroid equality requirement (unrealistic)  
✗ Block-wise partitioning (AAA BBB)  
✗ Ignoring device ratios  
✗ Breaking symmetry for distribution  
✗ Applying CC across unrelated groups  

────────────────────────────────────
OUTPUT REQUIREMENT
────────────────────────────────────

Must produce:

- symmetric finger sequence S
- centroid value per device
- centroid deviation report
- validation confirming gradient cancellation quality
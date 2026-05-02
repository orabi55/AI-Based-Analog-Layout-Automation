---
id: interdigitate
name: Ratio-Based Interdigitation
description: Interleave device fingers to preserve ratios and statistical distribution.

trigger:
  mode: IG
  keywords: ["interdigitate", "interdigitation", "alternate", "mix"]

scope:
  - matched_device_set (local group only)
  - may apply to CC substructures only if explicitly enabled by strategy

---

────────────────────────────────────
CORE PURPOSE
────────────────────────────────────

Construct a globally interleaved finger sequence that:

- preserves device ratio fidelity
- avoids clustering artifacts
- maintains uniform statistical distribution across layout positions

This is a DISTRIBUTION OPTIMIZATION problem, not strict alternation.

────────────────────────────────────
BEHAVIOR
────────────────────────────────────

- Build a single linear sequence of fingers
- Maintain proportional representation over the entire sequence
- Ensure bounded local imbalance (not exact prefix equality)
- Avoid long homogeneous runs

────────────────────────────────────
ALGORITHM
────────────────────────────────────

step_1_extract_pool:

  pool[Di] = nf_i fingers

step_2_compute_global_ratios:

  N = sum(nf_i)

  target_ratio(Di) = nf_i / N

step_3_initialize_state:

  sequence = []
  window_size = max(3, number_of_device_types * 2)

step_4_construct_sequence (BALANCED SAMPLING):

  while pool not empty:

      for each device Di:

          ideal_share = target_ratio(Di) * len(sequence + 1)
          actual_share = count(Di in sequence)

          deficit(Di) = ideal_share - actual_share

      select Di such that:

          - pool[Di] > 0
          - deficit(Di) is maximal
          - tie-breaker: lowest recent usage frequency

      append Di_f to sequence
      decrement pool[Di]

step_5_LOCAL_RUN_LENGTH_CONTROL:

  enforce:

    for any sliding window W of size window_size:

      count(Di in W) ≤ ceil(window_size * target_ratio(Di)) + 1

step_6_COMPLETION:

  continue until all pool exhausted

────────────────────────────────────
CONSTRAINTS
────────────────────────────────────

- Exact count preservation: count(Di) == nf_i
- Total sequence completeness required
- No long-range clustering allowed
- Local window ratio must approximate global ratio
- Deterministic selection only (no randomness)

────────────────────────────────────
VALIDATION (REAL IG CRITERIA)
────────────────────────────────────

VALID IF:

1) Completeness:
   sequence length == sum(nf_i)

2) Ratio fidelity:
   for all sliding windows W:
     |observed_ratio(Di, W) - target_ratio(Di)| ≤ ε

3) Run-length boundedness:
   no device dominates local window

4) Distribution smoothness:
   no clustering spikes or starvation regions

FAIL → ✗ INVALID IF:

- device starvation occurs early or mid sequence
- local clustering exceeds window constraints
- ratio drift is unbounded in any region
- deterministic construction violated

────────────────────────────────────
FORBIDDEN
────────────────────────────────────

✗ Hard strict round-robin enforcement
✗ Post-hoc sequence fixing
✗ Randomized selection
✗ Fixed spacing rules (e.g., “always separate by 2”)
✗ Global prefix-equality requirement
✗ Treating IG as strict alternating ABAB pattern

────────────────────────────────────
RELATIONSHIP TO OTHER SKILLS
────────────────────────────────────

- Compatible with CC only if applied AFTER centroid symmetry formation
- Compatible with DP only if pairing is preserved as atomic units
- Subordinate to Bias Chain (cannot reorder vertical structure)
- May be overridden locally by MB symmetry constraints

────────────────────────────────────
OUTPUT REQUIREMENT
────────────────────────────────────

Must produce:

- interdigitated finger sequence
- local window balance verification
- ratio fidelity report
- run-length constraint validation
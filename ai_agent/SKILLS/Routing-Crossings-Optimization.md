---
name: Routing-Crossings-Optimization
description: Optimize device slot ordering within rows AND migrate eligible devices between rows to minimize future routing wire crossings. Use this skill whenever the placement specialist needs to order or reorder device fingers/blocks, move devices across rows, or resolve crossing conflicts introduced by electrical connectivity. Trigger on any mention of crossing minimization, net ordering, slot reordering, cross-row migration, routing-aware placement, or wire crossing in the context of slot/coordinate assignment.
---

────────────────────────────────────
CORE PURPOSE
────────────────────────────────────

Minimize ROUTING WIRE CROSSINGS by:

  1. Reordering device slots WITHIN each row
  2. Migrating eligible devices BETWEEN rows

when either action reduces the global weighted crossing count.

This is a PLACEMENT-PHASE optimization, NOT a routing step.
Output is [CMD] move and swap commands — not layer assignments or via locations.

This is NOT cosmetic reordering.
This is CROSSING REDUCTION via net-aware slot permutation and cross-row migration.

────────────────────────────────────
WHEN TO APPLY
────────────────────────────────────

Apply this skill AFTER:
  - Step 5 (slot assignment) is complete
  - All hard constraints (BIAS_CHAIN, DP, CC, MB) are satisfied

Apply this skill BEFORE:
  - Step 6 (coordinate mapping)
  - Any [CMD] blocks are emitted

DO NOT apply if reordering or migration would violate any constraint of priority 1–5.
This skill operates only in the soft-constraint zone (priority 6–10).

────────────────────────────────────
BEHAVIOR
────────────────────────────────────

- Reorder slots within rows to minimize intra-row crossing contribution
- Migrate unlocked devices to adjacent rows if global crossing score improves
- Treat migrated devices as occupying a new slot in the destination row
- Preserve all hard-constraint orderings (DP mirroring, CC symmetry, MB structure)
- Matched blocks (pre-interdigitated) are ATOMIC UNITS — move as a whole or not at all
- Dummies remain at row ends — never pulled inward or migrated by this skill
- NMOS/PMOS separation is ABSOLUTE — cross-type migration is forbidden

────────────────────────────────────
NET SENSITIVITY CLASSIFICATION
────────────────────────────────────

Classify each net before computing crossings:

  HIGH:   differential signals, matched pairs, sensitive bias nodes
  MED:    generic signals, single-ended outputs, moderate bias
  LOW:    VDD, VSS, bulk, digital control

Crossing penalty weight:

  HIGH × HIGH  →  penalty = 4   ← avoid at all costs
  HIGH × MED   →  penalty = 2
  HIGH × LOW   →  penalty = 1
  MED  × MED   →  penalty = 1
  MED  × LOW   →  penalty = 0   (acceptable)
  LOW  × LOW   →  penalty = 0   (benign)

────────────────────────────────────
ALGORITHM
────────────────────────────────────

step_1_extract_net_order:

  For each row R:
    extract ordered list of nets N_R = [n0, n1, ..., nk]
    where ni = net connected to device at slot i

  Build global net position map:
    net_positions[net] = { row → slot_index }

step_2_compute_global_crossing_score:

  For every adjacent row pair (R, R'):
    For each pair of slots (i, j) in R where i < j:
      net_A = net at slot i in R
      net_B = net at slot j in R
      if pos(net_A, R') and pos(net_B, R') both exist:
        if pos(net_A, R') > pos(net_B, R'):
          score += penalty(net_A, net_B)

  global_score = Σ all row-pair scores

step_3_within_row_reorder:

  Preserve LOCKED slots (see LOCKED SLOT RULES below).

  For UNLOCKED slots only, per row:

    a) For each adjacent unlocked pair (slot_i, slot_i+1):
         Δscore = score_after_swap − score_before_swap
         if Δscore < 0 → apply swap, update net positions

    b) Repeat until no improvement

  Record: within_row_Δscore

step_4_cross_row_migration:

  ELIGIBILITY CHECK for device D in row R:

    D is MIGRATION-ELIGIBLE only if ALL hold:
      ✓ D is not locked (not DP, not CC, not MB, not bias_chain anchor)
      ✓ D is not a matched block spanning multiple devices
      ✓ Destination row R' has the same device type (NMOS→NMOS, PMOS→PMOS)
      ✓ Destination row R' is an ADJACENT row (|R' - R| == 1)
      ✓ Destination row R' has at least one free slot
      ✓ Moving D does not leave R with a broken symmetry or gap that
        invalidates a remaining locked constraint in R

  MIGRATION PROCEDURE:

    For each eligible device D in row R:

      candidate_rows = adjacent rows of same type with free slots

      for each candidate row R':

        trial_score = recompute global_score with D moved to
                      best available slot in R'

        Δscore = trial_score − current_global_score

        if Δscore < 0:
          record migration: (D, R → R', best_slot, Δscore)

    Apply migrations in order of most negative Δscore first.
    After each migration, recompute global_score before next candidate.

    If migrating D creates a free slot in R:
      fill with DUMMY to maintain row width if required by symmetry.
      [CMD]{"action":"add_dummy","type":"nmos|pmos","x":X,"y":Y}[/CMD]

  Record: cross_row_Δscore, list of migrations

step_5_iterate:

  Repeat steps 3–4 until:
    global_score does not improve OR
    max_iterations = 3 reached

  Final global_score must satisfy:
    global_score_final ≤ global_score_initial

step_6_emit_commands:

  For each device/block in final layout (all rows):

    x = slot_index × 0.294   (non-abutted)
    x = slot_index × 0.070   (abutted within matched block)
    y = exact value from PRE-COMPUTED ROW ASSIGNMENT table
        ← migrated devices use y of their DESTINATION row

    emit:
      [CMD]{"action":"move","device":"DEVICE_ID","x":X,"y":Y}[/CMD]

  For matched blocks: emit ONE [CMD] at origin_x (first finger only).
  For simple same-row swaps: may use swap action:
      [CMD]{"action":"swap","device_a":"BLOCK_A","device_b":"BLOCK_B"}[/CMD]
  For dummies added after migration:
      [CMD]{"action":"add_dummy","type":"nmos","x":X,"y":Y}[/CMD]

────────────────────────────────────
LOCKED SLOT RULES (CRITICAL)
────────────────────────────────────

These devices MUST NOT be reordered OR migrated:

  BIAS_CHAIN anchors    → locked (priority 2) — row AND slot fixed
  DP mirror pairs       → locked (priority 3) — both devices move together or not at all
  MB symmetric pairs    → locked (priority 4) — full MB block is atomic
  CC finger sequences   → locked (priority 5) — entire CC group is atomic
  Matched block internals → atomic, never split across rows

UNLOCKED devices eligible for reordering AND cross-row migration:

  - Devices with only PROXIMITY_NET or SIMPLE ORDERING constraints
  - Devices whose movement does not orphan a locked partner

If any move (within-row or cross-row) would break a locked constraint:
  → do NOT apply the move
  → log: MIGRATION SKIPPED — [device] locked by [constraint]

────────────────────────────────────
CROSS-ROW MIGRATION CONSTRAINTS
────────────────────────────────────

ABSOLUTE RULES (never violated):

  ✗ NMOS device → PMOS row             (type violation)
  ✗ PMOS device → NMOS row             (type violation)
  ✗ Migration to non-adjacent row       (skip row forbidden)
  ✗ Migration that leaves a locked      (e.g. DP partner stranded in original row)
    partner without its symmetric pair
  ✗ Migration of a matched block to     (block must fit contiguously)
    a row without sufficient free slots

SOFT RULES (log if violated, do not apply migration):

  ~ Migration that increases row width beyond layout aspect ratio target
  ~ Migration that creates a new HIGH×HIGH crossing elsewhere

────────────────────────────────────
VALIDATION
────────────────────────────────────

VALID IF:

1) Device conservation:
   every finger appears in exactly one [CMD]

2) No slot duplication:
   no two devices share (x, y) after coordinate mapping

3) No hard constraint broken:
   DP symmetry, CC centroid, MB symmetry all intact after migration

4) Type separation preserved:
   no NMOS in PMOS row and vice versa

5) Global crossing score non-increasing:
   global_score_final ≤ global_score_initial

6) Dummies at row ends only (including newly added post-migration dummies)

FAIL → ✗ INVALID if:

- any finger missing from output
- duplicate (x, y) coordinate pair
- locked constraint violated by reorder or migration
- NMOS/PMOS type boundary crossed
- crossing score increased after optimization

────────────────────────────────────
CONSTRAINT INTERACTIONS
────────────────────────────────────

Subordinate to (do NOT override):
  BIAS_CHAIN         (row structure and anchor slots fixed)
  DIFFERENTIAL_PAIR  (mirror slots fixed; both legs migrate together or not at all)
  BIAS_MIRROR        (symmetric slots fixed)
  COMMON_CENTROID    (finger sequence and row fixed)

Feeds into:
  PROXIMITY_NET       (migration brings same-net devices closer vertically)
  MATCHED_ENVIRONMENT (tighter bounding box after migration)

Compatible with:
  INTERDIGITATION    (matched blocks treated as atoms during migration)
  DUMMY PLACEMENT    (dummies added post-migration to fill vacated slots)

────────────────────────────────────
FORBIDDEN
────────────────────────────────────

✗ Reordering or migrating locked slots (DP, MB, CC, bias_chain)
✗ Migrating NMOS to PMOS row or vice versa
✗ Migrating to a non-adjacent row
✗ Splitting matched blocks across rows
✗ Moving dummies away from row ends
✗ Emitting per-finger CMDs for matched blocks (one CMD per block only)
✗ Applying this skill before hard constraints are resolved
✗ Increasing global weighted crossing score after optimization
✗ Emitting coordinates without using PRE-COMPUTED ROW ASSIGNMENT y values
✗ Migrating a DP device without its mirror partner

────────────────────────────────────
OUTPUT REQUIREMENT
────────────────────────────────────

Must produce:

1) Crossing score report:
   global_score_initial, global_score_final, Δ
   breakdown: within_row_Δ, cross_row_Δ

2) Migration log:
   for each migration: device, source row (y), destination row (y),
   destination slot (x), Δscore contribution

3) Reordering log:
   list of within-row swaps applied, devices affected

4) Relaxation log:
   any skipped moves with reason (locked by / type violation / score neutral)

5) [CMD] blocks — one per device/block, strict format:

   [CMD]{"action":"move","device":"MM1_f1","x":0.000,"y":0.000}[/CMD]
   [CMD]{"action":"swap","device_a":"MM3_m1","device_b":"MM4_m1"}[/CMD]
   [CMD]{"action":"add_dummy","type":"nmos","x":0.000,"y":0.000}[/CMD]

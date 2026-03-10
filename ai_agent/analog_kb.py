"""
ai_agent/analog_kb.py
======================
Analog layout design knowledge base.
Injected into the Topology Analyst and Placement Specialist prompts
to give the LLM domain knowledge without needing training on layout manuals.

Reference: Razavi "Design of Analog CMOS Integrated Circuits", Chapter 6-8.
"""

ANALOG_LAYOUT_RULES = """
=== ANALOG LAYOUT KNOWLEDGE BASE ===

## DIFFERENTIAL PAIRS
- Devices sharing the same GATE net are a current mirror or diff-pair input.
- Devices sharing the same SOURCE net with a common tail form diff-pair halves.
- Rule: Diff-pair devices MUST be placed SYMMETRICALLY about the row centre.
  Their x-coordinates must be equidistant from the symmetry axis.
- Rule: Diff-pair orientations must mirror each other: one R0, one R0_FH.
- Rule: Tail current source should be placed at the horizontal CENTER of the
  diff-pair — directly below (or adjacent to) the diff-pair centre.
- Rule: Load mirror devices (PMOS) must be placed symmetrically above the
  diff-pair NMOS devices — same x-offsets from centre.

## CURRENT MIRRORS
- A diode-connected MOS (gate tied to drain) paired with another device
  sharing that gate net forms a current mirror.
- Rule: Mirror devices should be placed ADJACENT (abutted, consecutive x-slots)
  to minimise gate resistance mismatch.
- Rule: Mirror devices should share the same orientation (both R0 or both R0_FH).
- Rule: For high-accuracy mirrors (W/L match), place them in the CENTRE of the
  row to avoid etch asymmetry at row edges.

## CASCODE DEVICES
- A cascode device stacks between a mirror and the output node.
  It shares a bias gate net with its cascode partner.
- Rule: Cascode pairs should be stacked VERTICALLY (same x-slot, one PMOS,
  one NMOS) — the PMOS cascode above the NMOS cascode.
- Rule: Cascode bias net devices should be placed adjacent to the main cascode.

## DUMMY DEVICES
- Dummy devices (is_dummy=True) are required for etch uniformity.
- Rule: Dummies must be placed at the FAR LEFT and FAR RIGHT of each row —
  never inserted between active transistors.
- Rule: Every row should have ≥ 1 dummy at each end (left-edge and right-edge).
- Rule: DUMMYP* devices → PMOS row. DUMMYN* devices → NMOS row.

## PMOS / NMOS ROW ASSIGNMENT
- PMOS devices occupy the TOP row (lower y-coordinate in this editor).
- NMOS devices occupy the BOTTOM row (higher y-coordinate).
- Rule: NEVER mix PMOS and NMOS in the same physical row.
- Rule: The PMOS row connects to VDD (power rail at top).
- Rule: The NMOS row connects to GND (power rail at bottom).
- Rule: X-pitch is 0.294 µm. Each device slot is 0.294 wide.

## LAYOUT SYMMETRY AXIS
- For fully differential circuits, a vertical symmetry axis divides the layout.
- Rule: All matched pairs (diff-pair, load mirror) must be placed equidistant
  from this axis — one device on each side at the same x-offset.
- Rule: The symmetry axis is perpendicular to the row direction.

## NET-DRIVEN ROUTING PRIORITY
- CRITICAL nets = differential signals (INP/INN), output (OUT/VOUT), clock (CLK).
  → Minimise wire length. Place driver and load devices adjacent or vertically aligned.
- BIAS nets = NBIAS, PBIAS, VTAIL, VCMFB. Less critical — longer routes OK.
- POWER rails = VDD, GND. Run along row edges; keep devices that connect VDD/GND
  at row periphery.

## DEVICE SIZING AND MATCHING
- Two devices with the SAME W, L, nf (fingers) are MATCHING CANDIDATES.
  → They must be placed adjacently and with the same orientation.
- Devices with nf > 1 are multifinger — occupy the same x-pitch but are
  wider in physical area. Treat them identically to single-finger for placement.
- Inter-digital (ABAB) placement: for highest-accuracy mirrors, alternate the
  two matched devices (A B A B) instead of (A A B B).
"""

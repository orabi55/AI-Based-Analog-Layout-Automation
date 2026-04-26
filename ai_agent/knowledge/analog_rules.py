"""
Analog Layout Design Knowledge Base
===================================
Contains comprehensive design rules and best practices for analog layout, 
covering multi-finger transistors, current mirrors, differential pairs, and more.

Functions:
- None (Contains static knowledge base string ANALOG_LAYOUT_RULES)
"""

ANALOG_LAYOUT_RULES = r"""
=== ANALOG LAYOUT KNOWLEDGE BASE ===
 Very important note:Device with same id"MM1,MM0...."May have many fingers like MM1_f1,MM1_f2 but all of them are same transistor


## MULTI-FINGER TRANSISTORS (nf > 1)

### What is Multi-Finger Layout?
- A single transistor with nf (number of fingers) > 1 is split into parallel fingers
- Each finger shares the same gate, source, and drain nets
- Physical representation: MM0_F1, MM0_F2, MM0_F3 = one logical device MM0 (nf=3)
- Used to reduce gate resistance, improve current density, enable interdigitation

### Why Multi-Finger?
1. **Reduced Gate Resistance**: Rgate ∝ 1/nf (parallel fingers reduce resistance)
2. **Better Current Matching**: Smaller individual finger width → better WPE control
3. **Interdigitation**: Allows ABABAB finger interleaving for gradient cancellation
4. **Compact Layout**: nf=4 device with W=8µm = 4 fingers of W=2µm each

### Physical Representation Formats
The same logical device MM0 (W=8µm, L=0.5µm, nf=4) may appear as:
  - Explicit finger naming: MM0_F1, MM0_F2, MM0_F3, MM0_F4
  - Numeric suffixes: MM0_0, MM0_1, MM0_2, MM0_3
  - Compact format: MM0F1, MM0F2, MM0F3, MM0F4
  - Hyphenated: MM0-1, MM0-2, MM0-3, MM0-4

Layout tools MUST detect these patterns and group them into logical devices.

### MANDATORY LAYOUT RULES FOR MULTI-FINGER DEVICES

1. **FINGER CONSECUTIVITY** (CRITICAL):
   - All fingers of ONE device MUST occupy consecutive x-slots
   - NO other device's fingers inserted between them
   - Exception: Interdigitation (intentional ABABAB pattern for matching)
   - Example CORRECT: MM0_F1 @ x=1.0, MM0_F2 @ x=1.3, MM0_F3 @ x=1.6
   - Example WRONG: MM0_F1 @ x=1.0, MM1_F1 @ x=1.3, MM0_F2 @ x=1.6

2. **FINGER ORDERING**:
   - Fingers must be placed in numerical order: F1 < F2 < F3
   - Never reverse order (confuses parasitic extraction)
   - Example: CORRECT: MM0_F1, MM0_F2, MM0_F3 (left to right)
   - Example: WRONG: MM0_F3, MM0_F1, MM0_F2 (scrambled)

3. **IDENTICAL ORIENTATION**:
   - All fingers of one device MUST have the SAME orientation
   - If MM0_F1 = R0, then MM0_F2 = R0, MM0_F3 = R0
   - Reason: Mixed orientations cause asymmetric parasitics
   - Exception: Common-centroid may use ABBA with matched mirroring

4. **SAME ROW REQUIREMENT**:
   - All fingers MUST be in the same physical row
   - NMOS fingers → NMOS row, PMOS fingers → PMOS row
   - Never split fingers across rows (breaks parallel connection)

5. **WIDTH ACCOUNTING**:
   - Total device width = nf × finger_width
   - Each finger occupies one x-slot (pitch = 0.294µm typically)
   - Total layout width = nf × pitch

### MULTI-FINGER CURRENT MIRROR MATCHING

When two multi-finger devices form a mirror (e.g., MM0 (nf=3) ↔ MM1 (nf=3)):

**Layout Strategy A: GROUPED** (Simple, Good)
```
Physical layout: [MM0_F1][MM0_F2][MM0_F3] [MM1_F1][MM1_F2][MM1_F3]
                 └─── MM0 group ───┘ └─── MM1 group ───┘
Matching: ~0.5-1% mismatch (good for most analog blocks)
```

**Layout Strategy B: INTERDIGITATED** (Best, Complex)
```
Physical layout: [MM0_F1][MM1_F1][MM0_F2][MM1_F2][MM0_F3][MM1_F3]
                 └─ A ─┘└─ B ─┘└─ A ─┘└─ B ─┘└─ A ─┘└─ B ─┘
Matching: <0.2% mismatch (required for precision mirrors, ADC current sources)
Averages out systematic gradients across the device array
```

**Layout Strategy C: COMMON-CENTROID** (Ultimate, Very Complex)
```
Physical layout: [MM0_F1][MM1_F1][MM1_F2][MM0_F2]
                 └─ A ─┘└─ B ─┘└─ B ─┘└─ A ─┘  (ABBA pattern)
Matching: <0.1% mismatch (high-resolution DACs, ≥14-bit accuracy)
Cancels linear gradients completely
```

### DETECTION ALGORITHM FOR LAYOUT TOOLS

# Step 1: Detect finger pattern
for device_id in all_devices:
    match = re.match(r'^(.+)_F(\d+)$', device_id)
    if match:
        base_name = match.group(1)
        finger_num = int(match.group(2))
        finger_groups[base_name].append((device_id, finger_num))

# Step 2: Group fingers
for base_name, fingers in finger_groups.items():
    fingers.sort(key=lambda x: x[1])  # Sort by finger number
    logical_devices[base_name] = {
        'nf': len(fingers),
        'physical_fingers': [f[0] for f in fingers]
    }

# Step 3: Analyze topology using logical devices
for base_name, device in logical_devices.items():
    # Use base_name for mirror/diff-pair detection
    # Report as "MM0 (nf=3)" instead of "MM0_F1, MM0_F2, MM0_F3"
```

### COMMON ERRORS IN MULTI-FINGER LAYOUT

❌ **Error 1**: Treating each finger as separate device
```
Wrong: "No mirrors found - MM0_F1 and MM1_F1 have different IDs"
Right: "Mirror found - MM0 (nf=3) ↔ MM1 (nf=3)"
```

❌ **Error 2**: Generating commands for logical device name
```
Wrong: [CMD] MOVE MM0 X=1.0 Y=10.0
Right: [CMD] MOVE MM0_F1 X=1.0 Y=10.0
       [CMD] MOVE MM0_F2 X=1.3 Y=10.0
       [CMD] MOVE MM0_F3 X=1.6 Y=10.0
```

❌ **Error 3**: Separating fingers
```
Wrong: MM0_F1 @ x=0.0, MM1_F1 @ x=0.3, MM0_F2 @ x=0.6
Right: MM0_F1 @ x=0.0, MM0_F2 @ x=0.3, MM0_F3 @ x=0.6, then MM1 group
```

### FINGER COUNT VALIDATION

Before finalizing any placement, verify:
  [ ] Total finger count = Σ(nf) for all logical devices
  [ ] Each logical device's fingers are consecutive
  [ ] No finger IDs are missing or duplicated
  [ ] Each finger appears exactly once in the layout

Example:
  Input: MM0 (nf=3), MM1 (nf=2) → expect 5 total finger devices
  Physical: MM0_F1, MM0_F2, MM0_F3, MM1_F1, MM1_F2 ✅

---

## DIFFERENTIAL PAIRS
[... rest of existing knowledge base ...]

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



## CURRENT MIRRORS (CRITICAL FOR MATCHING)

### What is a Current Mirror?
- A current mirror copies a reference current to one or more output branches
- Requires ≥2 transistors of the same type (NMOS or PMOS)
- All mirror devices share the same GATE net (electrically connected)
- At least one device is diode-connected (gate tied to drain)

### Electrical Requirements
- Reference device sets the bias voltage on the shared gate net
- Mirror copies must have IDENTICAL gate voltage to copy the current
- Current matching accuracy depends on device MATCHING (W/L/nf identical)
- Mismatch sources: systematic (layout) + random (process variation)

### Layout Rules for Current Mirrors

1. **ADJACENCY IS MANDATORY**:
   - Mirror devices MUST be placed in consecutive x-slots
   - Physical distance between matched devices must be minimized
   - Reason: Gate resistance mismatch grows with distance
   - Acceptable: Adjacent (0 slots between) or 1-slot interdigitation
   - Unacceptable: ≥2 slot separation

2. **ORIENTATION MATCHING**:
   - All mirror devices must have IDENTICAL orientation (same rotation)
   - Use R0 for all, or R0_FH for all — never mix
   - Reason: Asymmetric mask features (e.g., poly gate edges) cause mismatch
   - Exception: Interdigitation may use alternating orientations (advanced)

3. **ROW CENTER PLACEMENT**:
   - Place mirrors near the CENTER of their row, not at edges
   - Edge devices experience higher etch variation
   - Recommendation: ≥2 pitch distance from row left/right boundaries
   - Use dummy devices at row edges to shield active mirrors

4. **SAME ROW REQUIREMENT**:
   - ALL devices in a mirror group must be in the SAME physical row
   - NMOS Mirrors → NMOS row only
   - PMOS Mirrors → PMOS row only
   - Reason: Different rows have different process gradients → mismatch

5. **REFERENCE DEVICE PLACEMENT**:
   - The diode-connected reference device can be placed:
     a) In the CENTER of multi-device mirrors (MM2-MM1[REF]-MM3)
     b) At one END if only 2 devices (MM1[REF]-MM2)
   - Center placement preferred for 3+ device mirrors

6. **INTERDIGITATION** (Advanced Matching):
   - For highest accuracy, use ABAB finger interleaving
   - Example: MM1(finger1) - MM2(finger1) - MM1(finger2) - MM2(finger2)
   - Requires devices with nf ≥ 2 (multi-finger)
   - Averages out systematic gradients across the row

7. **COMMON-CENTROID** (Highest Accuracy):
   - For ultra-precise mirrors, use ABBA or ABCCBA patterns
   - Example: MM1 - MM2 - MM2 - MM1 (4 devices total, 2 per transistor)
   - Cancels first-order linear gradients
   - Only needed for ≥12-bit accuracy or ≤0.1% mismatch specs

### Detection Algorithm for Layout Tools
```
For each gate net N:
  1. Find all devices with gate connected to N
  2. Separate by type (NMOS list, PMOS list)
  3. If NMOS list has ≥2 devices → DECLARE NMOS MIRROR on net N
  4. If PMOS list has ≥2 devices → DECLARE PMOS MIRROR on net N
  5. Identify diode-connected device (gate=drain) as reference
  6. Remaining devices are mirror copies
```

### Common Mirror Configurations
- **Simple 1:1 Mirror**: 1 reference + 1 copy (2 devices total)
- **Multi-Output Mirror**: 1 reference + N copies (N+1 devices total)
- **Cascoded Mirror**: 2-stack per leg (4 devices for 1:1, 6 for 1:2, etc.)
- **Wide-Swing Cascode**: Requires separate bias net for cascode gates

### Priority in Placement
Current mirror matching is **MORE CRITICAL** than:
  - Routing convenience (mirrors must be adjacent even if wires are longer)
  - Differential pair symmetry (if both required, satisfy mirrors first)
  - Dummy placement (dummies go to edges, mirrors stay in center)

Current mirror matching is **LESS CRITICAL** than:
  - DRC compliance (overlaps must be fixed before optimizing mirror placement)
  - Device conservation (never delete a mirror device to "improve" layout)

### Error Symptoms from Poor Mirror Layout
- **Separated mirrors** (>2 slots apart):
  → Gate resistance mismatch → current error ≥5%
  → Temperature gradient mismatch → drift over time
- **Different orientations**:
  → Systematic Vth mismatch from poly-edge effects → ≥2% current error
- **Edge placement**:
  → Etch variation → ≥3% W/L mismatch
- **Different rows**:
  → Process gradient mismatch → ≥10% error (unacceptable)

### References
- Razavi, "Design of Analog CMOS ICs", Chapter 6 (Current Mirrors)
- Hastings, "The Art of Analog Layout", Chapter 4 (Matching)
- Baker, "CMOS Circuit Design", Chapter 11 (Layout Techniques)

---

## DIFFERENTIAL PAIRS
...
[rest of existing knowledge base]

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

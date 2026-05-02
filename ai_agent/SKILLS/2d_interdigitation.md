---
id: 2d_interdigitation
name: 2D ABBA Interdigitation and 2D Common Centroid
description: 2D ABBA Interdigitation and 2D Common Centroid

trigger:
  keywords: ["2d matching", "2d common centroid", "fold", "multi-row interdigitation",
             "poor utilization", "dummy cells", "aspect ratio", "2d interdigitated"]

scope:
  - matched_pair_group_only
  - applied_before_llm_sees_blocks

---

────────────────────────────────────
CORE PURPOSE
────────────────────────────────────

Improve layout utilization by folding tall 1D matched blocks across 2 physical
rows, halving the row width while preserving all matching properties.

This is a **deterministic pre-LLM optimization** — the LLM sees the already-folded
block with its reduced width. The LLM MUST NOT split a 2D block.

────────────────────────────────────
WHEN 2D IS USED (AUTOMATIC DECISION)
────────────────────────────────────

The system automatically chooses 1D vs 2D by minimizing total dummy/filler cells
across the ENTIRE layout.

2D is applied when:
  - folding reduces total layout dummy count (calculated globally)
  - block has ≥ 4 fingers per device (physically meaningful fold)
  - finger count per device is even (required for ABBA quads per row)
  - technique is ABBA_diff_pair or ABAB_load_pair

NOT a hard-coded threshold — it is computed per circuit.

────────────────────────────────────
LAYOUT STRUCTURE
────────────────────────────────────

1D ABBA (before optimization):
  Width = N_fingers × pitch  (e.g. 18 × 0.294 = 5.292 µm)
  [D A B B A  A B B A  A B B A  A B B A D]  ← one long row

2D ABBA (after fold, 2 rows):
  Width = (N_fingers/2 + 2) × pitch  (e.g. 10 × 0.294 = 2.940 µm)
  Row 1 (top):    [D A B B A  A B B A D]  ← half the fingers
  Row 0 (bottom): [D A B B A  A B B A D]  ← other half

Each physical row satisfies ABBA pattern independently:
  - X-gradient: canceled (equal A and B in each row half)
  - Y-gradient: canceled (same pattern in both rows)
  → TRUE 2D common-centroid matching

────────────────────────────────────
GRADIENT CANCELLATION PROOF
────────────────────────────────────

For a parameter P with linear gradients in X and Y:
  P_device(x,y) = P0 + αx + βy

In 2D ABBA with A and B split across 2 rows:
  ΔP(A) = Σ P0 + αx_A + βy_A  (sum over all A cells)
  ΔP(B) = Σ P0 + αx_B + βy_B  (sum over all B cells)

Since each row has equal A and B at symmetric X positions:
  Σ x_A = Σ x_B   (ABBA in each row)
  Σ y_A = Σ y_B   (same row structure in each physical row)
  → ΔP(A) = ΔP(B)  ✓ gradient-free matching

────────────────────────────────────
LLM RULES (CRITICAL)
────────────────────────────────────

- A block marked [FIXED 2D MATCHED BLOCK] occupies 2 consecutive physical rows
- You MUST place it at a SINGLE x,y origin — the expansion engine places both rows
- DO NOT split the block across different x positions
- DO NOT try to manually place Row 0 and Row 1 separately
- DO NOT modify x position of a 2D block once the system sets it
- The block's reported WIDTH is the FOLDED width (half of equivalent 1D)
- The block's reported HEIGHT spans 2 physical row heights

VALID placement:
  x = <centered position>, y = <base_y>  ← system places rows at y and y+ROW_HEIGHT

INVALID (DO NOT DO):
  x = <pos_for_row0>, y = <base>         ← correct row 0
  x = <pos_for_row1>, y = <base+step>    ← separate row 1 ← FORBIDDEN

────────────────────────────────────
EXAMPLE: Comparator MM8+MM9 (8 fingers each)
────────────────────────────────────

Before 2D optimization:
  MM8_MM9_matched: 1D ABBA, 18 cols, width=5.292µm
  MM7_MM6_matched: symmetric_cross_coupled, width=0.588µm
  MM5_MM4_matched: symmetric_cross_coupled, width=2.352µm
  → layout_width = 5.292µm, total_dummies ≈ 46 slots

After 2D optimization (automatic):
  MM8_MM9_matched: 2D ABBA, 10 cols × 2 rows, width=2.940µm
  MM7_MM6_matched: width=0.588µm
  MM5_MM4_matched: width=2.352µm
  → layout_width = 2.940µm, total_dummies ≈ 15 slots (67% reduction)

────────────────────────────────────
RELATIONSHIP TO OTHER SKILLS
────────────────────────────────────

- REPLACES standard interdigitated-matching for high finger-count diff pairs
- COMPATIBLE WITH differential_pair symmetry axis (axis still centered)
- SUBORDINATE TO bias chain vertical ordering (2D block stays in its tier)
- If 2D is active, common-centroid-matching is superseded for that block

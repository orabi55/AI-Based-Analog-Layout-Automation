# Fix FinFET CPP-Width Bug: Unified Tech Constants

## Problem

The placement pipeline has a dimension bug: NMOS fingers come out at **0.070 µm** x-width while PMOS fingers come out at **0.294 µm** x-width for the same 14 nm FinFET technology. In reality, every gate sits on one shared **contacted poly pitch (CPP = 0.078 µm)** regardless of device type.

The root cause is two stale constants defined in [design_rules.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/config/design_rules.py):
- `PITCH_UM = 0.294` (used as non-abutted width → wrong, this is ~4× CPP)
- `FINGER_PITCH = 0.070` (used as abutted finger pitch → wrong, this is ~0.9× CPP)
- `ROW_HEIGHT_UM = 0.668` / `ROW_PITCH = 0.668` (wrong, should be derived from nfin)

Both values should be `CPP_UM = 0.078` — every single finger occupies exactly one CPP slot.

## Proposed Changes

### [NEW] Tech Constants Module

#### [NEW] [tech_constants.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/ai_agent/tech_constants.py)

Single source of truth for 14 nm FinFET constants:

```python
CPP_UM              = 0.078   # contacted poly pitch — x-step per finger
FIN_PITCH_UM        = 0.048   # fin-to-fin pitch (y direction)
NFIN_DEFAULT        = 7       # from netlist; override per node if set
DIFF_EXT_UM         = 0.050   # diffusion extension top and bottom
DEVICE_HEIGHT_UM    = NFIN_DEFAULT * FIN_PITCH_UM + 2 * DIFF_EXT_UM  # 0.436
ROW_GAP_UM          = 0.164   # inter-row margin (well/NP spacing)
ROW_PITCH_UM        = DEVICE_HEIGHT_UM + ROW_GAP_UM                  # 0.600
MIN_POLY_PITCH_UM   = CPP_UM  # = 0.078
NMOS_DIFF_LAYER     = 1       # GDS layer for NMOS active
PMOS_DIFF_LAYER     = 2       # GDS layer for PMOS active
```

---

### Config Module

#### [MODIFY] [design_rules.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/config/design_rules.py)

Replace all hardcoded values with imports from `ai_agent.tech_constants`:

| Old constant | Old value | New source |
|---|---|---|
| `PITCH_UM` | 0.294 | `CPP_UM` (0.078) |
| `ROW_PITCH` | 0.668 | `ROW_PITCH_UM` (0.600) |
| `ROW_HEIGHT_UM` | 0.668 | `DEVICE_HEIGHT_UM` (0.436) |
| `ROW_GAP_UM` | 0.000 | `ROW_GAP_UM` (0.164) |
| `FINGER_PITCH` | 0.070 | `CPP_UM` (0.078) — **same as PITCH_UM now** |
| `PMOS_Y` | 0.668 | `ROW_PITCH_UM` (0.600) |
| `NMOS_Y` | 0.000 | 0.0 (unchanged) |
| Derived `BLOCK_GAP_UM` | `PITCH_UM * 2` | `CPP_UM * 2` |
| Derived `PASSIVE_ROW_GAP_UM` | `PITCH_UM` | `CPP_UM` |

> [!IMPORTANT]
> `FINGER_PITCH` and `PITCH_UM` are now **identical** (both = CPP_UM). The concept of "abutted" vs "non-abutted" pitch no longer exists at the finger level — every finger is one CPP wide. The old 0.294 was a leftover from a planar-CMOS model.

---

### Placement Module — [finger_grouper.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/ai_agent/placement/finger_grouper.py)

This is the largest file and the core of the dimension pipeline:

1. **`aggregate_to_logical_devices` (line ~361)**: Width formula `(total_fingers - 1) * FINGER_PITCH + STD_PITCH` → `total_fingers * CPP_UM` (since both pitches are now CPP, this simplifies). Height fallback `0.568` → `DEVICE_HEIGHT_UM`.
2. **`interdigitate_fingers` (lines ~866–1008)**: No changes to pattern logic. Edge dummy width/height to use `CPP_UM` / `DEVICE_HEIGHT_UM`.
3. **`expand_to_fingers` (lines ~1848–1970)**: Finger geometry `"width": pitch` → `"width": CPP_UM`, `"height"` → `DEVICE_HEIGHT_UM`. The `"x"` formula already uses `pitch` variable which will now resolve to `CPP_UM`.
4. **`_resolve_row_overlaps` (lines ~2026–2235)**: `pitch_abut` and `pitch_std` both become `CPP_UM`. Filler dummy `"width": pitch_std` → `CPP_UM`, `"height": ref_height` → `DEVICE_HEIGHT_UM`. Consecutive filler limit: cap at 3 consecutive `FILLER_DUMMY_*` entries.
5. **`expand_logical_to_fingers` (line 2262)**: Default `pitch: float = 0.294` → `pitch: float = CPP_UM`. Width/height emitted in the inner loop.
6. **`merge_matched_groups` (lines ~1173, 1269)**: Block height `0.668` → `DEVICE_HEIGHT_UM`. Block width formulas already use `block_pitch` which flows from the unified constants.

---

### Placement Module — [abutment.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/ai_agent/placement/abutment.py)

- Lines 182–183: `ABUT_SPACING = 0.070` / `PITCH = 0.294` → import from tech_constants → `CPP_UM` for both.
- Lines 401–402: same fix in `force_abutment_spacing`.
- Default height fallbacks (`0.668`) → `DEVICE_HEIGHT_UM`.

---

### Agent Prompts — [placement_specialist.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/ai_agent/agents/placement_specialist.py)

- Replace hardcoded prompt text `0.294`, `0.070`, `0.668` with f-string references to tech constants.
- Lines 252, 296, 362–363, 682, 731: update all numeric literals in the prompt strings.

---

### Agent — [prompts.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/ai_agent/agents/prompts.py)

- Lines 57–58, 245, 282, 306, 310, 315: replace hardcoded `0.294`, `0.070`, `0.668` with imports from tech_constants.

---

### Agent — [drc_critic.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/ai_agent/agents/drc_critic.py)

- Lines 681, 711, 786: Replace hardcoded `0.294` and `0.668` with imports from tech_constants.

---

### Agent — [routing_previewer.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/ai_agent/agents/routing_previewer.py)

- Line 191: `_ROW_HEIGHT_UM = 0.668` → import `ROW_PITCH_UM` from tech_constants.

---

### Placement Module — [validators.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/ai_agent/placement/validators.py)

- Line 69: `abs(dx - 0.070) > 0.005` → use `CPP_UM` tolerance.

---

### Placement Module — [symmetry.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/ai_agent/placement/symmetry.py)

- Lines 72, 78: Default width fallback `0.294` → `CPP_UM`.

---

### Tools — [cmd_parser.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/ai_agent/tools/cmd_parser.py)

- Line 23: `DEFAULT_MIN_DEVICE_SPACING_UM = 0.294` → `CPP_UM`.

---

### Tools — [overlap_resolver.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/ai_agent/tools/overlap_resolver.py)

- Line 35: `MIN_SPACING = 0.294` → `CPP_UM`.

---

### Knowledge — [analog_rules.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/ai_agent/knowledge/analog_rules.py)

- Lines 67, 310: Update comment text from `0.294` to `CPP_UM`.

---

### Symbolic Editor — [layout_tab.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/symbolic_editor/layout_tab.py)

- Lines 479, 480, 490: Scale factor references `0.294`, `0.668` → `CPP_UM`, `ROW_PITCH_UM`.
- Lines 1502–1509: Default geometry dict → use tech constants.
- Line 2145: `0.070` → `CPP_UM`.

---

### Symbolic Editor — [editor_view.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/symbolic_editor/editor_view.py)

- Lines 1228–1233: Replace `0.070` with `CPP_UM`.

---

### Symbolic Editor — [device_item.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/symbolic_editor/device_item.py)

- No `set_abut_state` method with `shared_net_left` condition was found. The file has `set_abut_left`/`set_abut_right` methods (lines 159–164, 288–294) that take a simple `state` boolean. No fix needed per the prompt's request since the check `if shared_net_left:` does not exist in this file.

---

### Export — [export_json.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/export/export_json.py)

- The current `export_json.py` does **not** have a `_detect_abutments` function. This is a **new function to add**. It will:
  - Detect abutted pairs when `|edge_gap| < 0.5 * CPP_UM` AND facing terminal nets are the same.
  - Emit `{left, right, net, overlap_um, boundary_kind}` for each abutted pair.

---

### Export — [oas_writer.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/export/oas_writer.py)

- No `merge_diff_layer` parameter exists yet. The request asks to add NMOS/PMOS-specific diffusion layer arguments and a `--no-merge-diff` CLI toggle. 

> [!WARNING]
> The `update_oas_placement` is called from `layout_tab.py` line 1814 with a simple signature. Adding `merge_diff_layer` would be a new feature requiring a new `_merge_diffusion` function that does not currently exist in the codebase.

---

### Dialogs — [ai_model_dialog.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/symbolic_editor/dialogs/ai_model_dialog.py)

- Line 180: Update prompt string `0.070` → `CPP_UM`.

---

### Tests

#### [MODIFY] [test_deterministic_placement_fixes.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/tests/test_deterministic_placement_fixes.py)

Update all hardcoded `0.294`, `0.668`, `0.818`, `0.070`, `1.636` → use tech constants.

#### [MODIFY] [test_placement_pipeline.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/tests/test_placement_pipeline.py)

Same — update all geometry constants in test fixtures.

#### [MODIFY] [test_finger_grouper_pipeline.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/tests/test_finger_grouper_pipeline.py)

Update geometry fixtures.

#### [MODIFY] [test_design_rules.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/tests/test_design_rules.py)

Update assertions for new constant values.

#### [NEW] [test_abutment_comparator.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/tests/test_abutment_comparator.py)

Regression test per the prompt's spec. Note: the prompt references `examples/StrongARMComparator/comparator.sp` but the actual file is at `examples/comparator/comparator.sp`. The test will use the actual path.

---

## Open Questions

> [!IMPORTANT]
> **StrongARMComparator path**: The prompt says `examples/StrongARMComparator/comparator.sp` but the repo has `examples/comparator/comparator.sp`. I'll use the actual path. Please confirm this is the intended circuit.

> [!IMPORTANT]
> **Diffusion merging**: The prompt asks for `_merge_diffusion` in `oas_writer.py` and a `--no-merge-diff` CLI toggle. This function does not exist yet — it would be a new OASIS polygon-merging feature. Should I implement a full polygon union on GDS layers 1/2, or just stub the API and pass the layer constants through?

> [!IMPORTANT]
> **`set_abut_state` with `shared_net_left`**: This function/pattern does not exist in `device_item.py`. The file uses simple boolean `set_abut_left(state)` / `set_abut_right(state)`. Should I create a new `set_abut_state` method that accepts `shared_net_left` / `shared_net_right` parameters, or is the prompt referring to logic elsewhere?

> [!WARNING]
> **Abutment concept change**: With `FINGER_PITCH = PITCH_UM = CPP_UM`, the distinction between "abutted" and "non-abutted" spacing disappears at the finger level. All fingers are always one CPP apart. The `no_abutment` flag would now only control whether abutment *flags* (leftAbut/rightAbut) are set on PCell variants for diffusion sharing — not spacing. Is this the correct interpretation?

## Verification Plan

### Automated Tests
1. `python -m pytest tests/test_design_rules.py -v` — verify new constant values
2. `python -m pytest tests/test_deterministic_placement_fixes.py -v` — verify geometry updates
3. `python -m pytest tests/test_placement_pipeline.py -v` — verify pipeline integration
4. `python -m pytest tests/test_finger_grouper_pipeline.py -v` — verify finger grouper
5. `python -m pytest tests/test_abutment_comparator.py -v` — new regression test

### Manual Verification
- Load the comparator example through the GUI and inspect finger widths are uniform 0.078 µm
- Verify NMOS/PMOS row pitch is 0.600 µm

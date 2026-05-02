# Two-Half Vertical-Axis Symmetry Enforcement

## Goal

When you run **Ctrl+P (AI Initial Placement)** on a comparator or differential circuit, all transistors should be placed symmetrically around a single **vertical axis per row** — no LLM randomness, no approximations. This is a fully deterministic, post-LLM correction pass.

---

## Understanding: What Exists Already

The current flow **already does** ABBA/ABAB interdigitation via `merge_matched_groups()` in `finger_grouper.py` → expanded by `node_finger_expansion`. And `enforce_reflection_symmetry()` in `symmetry.py` already tries to reflect matched-block pairs.

**The gap:** `enforce_reflection_symmetry` fires on `_matched_block` nodes using `_technique` flags, but it does NOT enforce that:
- The vertical axis is **shared across PMOS and NMOS rows** (x_axis_pmos == x_axis_nmos).
- Axis devices (tail current sources) are **centered exactly on the axis**.
- Left/right rank assignments are **deterministic** (rank 1 = diff pair innermost, rank 2 = loads).

**What this plan adds:**
1. A pure-Python `[SYMMETRY]` block appended to `constraint_text` by `analyze_json()` — machine-readable, no LLM.
2. A new `node_symmetry_enforcer` LangGraph node that reads the `[SYMMETRY]` block and runs `_enforce_two_half()`.
3. `_enforce_two_half()` reuses `enforce_reflection_symmetry` logic but extends it to share axis across rows and handle axis devices.
4. Wire the new node into the graph: `finger_expansion → symmetry_enforcer → routing_previewer`.
5. State gets a new `placement_mode` field, and DRC Critic gets a symmetry-aware guard.

---

## Key Design Decisions

> [!IMPORTANT]
> **Pure Python, No LLM.** The `[SYMMETRY]` block is detected algorithmically, and `_enforce_two_half()` is a rigid math transform — same result every run.

> [!IMPORTANT]
> **Runs AFTER finger expansion.** The enforcer sees real physical finger nodes (e.g., `MM1_f1`, `MM2_f2`) not logical groups. This means it operates on the correct final positions, after ABBA has already been applied internally within each pair.

> [!WARNING]
> **Axis shared across rows (CRITICAL).** `x_axis_pmos` and `x_axis_nmos` must be equal. The current `enforce_reflection_symmetry` does NOT do this — it processes each row independently. The new enforcer will compute a global axis from the bounding box of ALL symmetry-constrained fingers.

> [!NOTE]
> **`analyze_json()` is a Python-only function** (no LLM). The `[SYMMETRY]` block it appends will be seen by the LLM in the `constraint_text` passed to the Placement Specialist, which will help the LLM output the right rough positions. But the actual enforcement is done by `_enforce_two_half()` which OVERRIDES whatever the LLM produced.

---

## Open Questions

> [!IMPORTANT]
> **Rank detection for >2 device types per row:** The current detection rule is: diff-pair = shared-source (not VDD/VSS) → rank 1; load mirror = shared-gate on row directly above diff-pair → rank 2. Is this sufficient for your comparator? Some comparators have cross-coupled loads (symmetric_cross_coupled) which are already detected. Please confirm.

> [!NOTE]
> **Multi-row axis:** For a 5T-OTA comparator, the tail (MM7) is on the NMOS row, the diff pair (MM1/MM2) is on the same NMOS row (or next), and loads (MM4/MM5) are on the PMOS row. The plan detects `axis_row=both` meaning the vertical axis is shared. Is this the structure of your comparator?

---

## Proposed Changes

### Component 1: `[SYMMETRY]` Block Detection

#### [MODIFY] [topology_analyst.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/ai_agent/agents/topology_analyst.py)

Add a new pure-Python function `extract_symmetry_block(nodes, terminal_nets) -> str` at the bottom of the file. It will be called at the end of `analyze_json()` and its output appended to the returned text.

**Detection rules (all pure Python):**
- **Diff pair (rank 1):** Two same-type devices where `S-net` is NOT in `{VDD, VSS, GND, VCC, AVDD, AVSS}` AND both devices share the same `S-net`.
- **Load mirror (rank 2):** Two same-type devices where `G-net` is shared AND they are NOT on the same row as the diff-pair (i.e., different device type OR same type but not sharing S-net with a diff pair S-net).
- **Axis device:** A device whose `D-net` equals the shared `S-net` of the diff pair (i.e., it is the tail current source draining into the common source).

**Output appended to `constraint_text`:**
```
[SYMMETRY]
mode=two_half axis_row=both
pair=MM1,MM2 rank=1
pair=MM4,MM5 rank=2
axis=MM7
[/SYMMETRY]
```

---

### Component 2: State Extension

#### [MODIFY] [state.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/ai_agent/graph/state.py)

Add one field to `LayoutState`:
```python
placement_mode: str  # "auto" | "two_half"
```
Default `"auto"` (set in `placement_worker.py`'s `initial_state` dict).

---

### Component 3: Strategy Selector — `two_half` mode parsing

#### [MODIFY] [agents/strategy_selector.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/ai_agent/agents/strategy_selector.py)

- Add option 4 to `_mirror_fallback_strategies()`: **"Two-Half (Vertical Axis Symmetry)"** for fully-differential circuits.
- Extend `parse_placement_mode()` to map `"two-half"`, `"2 halves"`, `"axis symmetry"`, `"4"` → `"two_half"`.
- Update `STRATEGY_SELECTOR_PROMPT` to mention Two-Half symmetry as an option.

#### [MODIFY] [nodes/strategy_selector.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/ai_agent/nodes/strategy_selector.py)

After getting `strategy_text`, detect if `[SYMMETRY]` is present in `constraint_text`. If so, call `parse_placement_mode(strategy_text, constraint_text)` and include `placement_mode` in the returned state update dict.

---

### Component 4: New `node_symmetry_enforcer`

#### [NEW] [nodes/symmetry_enforcer.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/ai_agent/nodes/symmetry_enforcer.py)

This is the core new file. Contains:

**`parse_symmetry_block(constraint_text: str) -> dict`**
- Parses `[SYMMETRY]...[/SYMMETRY]` from `constraint_text`.
- Returns `{"mode": str, "pairs": [(left, right, rank), ...], "axis_devices": [str, ...]}` or `{}` if not found.

**`_enforce_two_half(nodes, pairs, axis_devices, pitch=0.294) -> list`**

Deterministic algorithm:
1. Collect all finger IDs belonging to any pair device or axis device (using `_fingers` list on logical nodes, or matching by prefix on physical finger nodes).
2. Compute `x_axis` = midpoint of bounding x-range of all collected fingers. Snap to nearest `pitch/2 = 0.147` so that `x_axis ± k*pitch` are valid slots.
3. **Force shared axis across rows:** all rows use the same `x_axis` value.
4. For each `(left_id, right_id, rank)` pair:
   - Place all fingers of `left_id` at `x_axis - rank * pitch`, `x_axis - rank * pitch - pitch`, etc. (left side, ABBA already done, just shift).
   - Place all fingers of `right_id` at `x_axis + rank * pitch`, etc. (right side).
   - Set `orientation = "R0"` for left, `"R0_FH"` for right.
5. For each `axis_device`:
   - If `nf=1`: set `x = x_axis`.
   - If `nf` even: split half-and-half around axis.
   - If `nf` odd: centre finger at axis, rest symmetric.

**`node_symmetry_enforcer(state) -> dict`**

LangGraph node that:
1. Reads `constraint_text` and calls `parse_symmetry_block()`.
2. If no `[SYMMETRY]` block or `placement_mode == "auto"` and no pairs found: return `{}` (pass through).
3. Otherwise calls `_enforce_two_half(placement_nodes, pairs, axis_devices)`.
4. Runs `validate_device_count()` and `resolve_overlaps()`.
5. Returns `{"placement_nodes": enforced_nodes, "deterministic_snapshot": enforced_nodes}`.

---

### Component 5: Graph Wiring

#### [MODIFY] [graph/builder.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/ai_agent/graph/builder.py)

- Import `node_symmetry_enforcer` from `ai_agent.nodes`.
- Register it: `builder.add_node("node_symmetry_enforcer", node_symmetry_enforcer)`.
- Change edge: `placement_specialist → finger_expansion → symmetry_enforcer → routing_previewer`.

#### [MODIFY] [nodes/\_\_init\_\_.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/ai_agent/nodes/__init__.py)

Export `node_symmetry_enforcer`.

---

### Component 6: DRC Critic Symmetry Guard

#### [MODIFY] [nodes/drc_critic.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/ai_agent/nodes/drc_critic.py)

In `compute_prescriptive_fixes`: before applying a fix that displaces a device, check if that device is one side of a `[SYMMETRY]` pair. If so, move both sides in opposite X directions by the same delta (mirror-preserving fix).

---

### Component 7: Placement Specialist Prompt

#### [MODIFY] [agents/placement_specialist.py](file:///c:/Users/DELL%20G3/Desktop/GP/NEW/AI-Based-Analog-Layout-Automation/ai_agent/agents/placement_specialist.py)

- Add a `MODE = TWO_HALF` section to `PLACEMENT_SPECIALIST_PROMPT` (after current Step 3 DIFF_PAIR rules) with a 5T-OTA worked example:
  ```
  TWO-HALF SYMMETRY MODE (active when [SYMMETRY] block present):
  - All pairs placed around a single vertical axis x_axis
  - rank 1 (diff pair): left at x_axis - 0.294, right at x_axis + 0.294
  - rank 2 (loads): left at x_axis - 0.588, right at x_axis + 0.588
  - axis device (tail): centered at x_axis
  Example (5T-OTA, x_axis = 0.588):
    MM7 (tail)  → x=0.588
    MM1 (left)  → x=0.294  MM2 (right) → x=0.882
    MM4 (load L)→ x=0.000  MM5 (load R) → x=1.176
  ```
- Extend `build_placement_context()` to surface the `[SYMMETRY]` excerpt if present.

---

## Verification Plan

### Automated Tests
1. Run `python -m pytest tests/` (if tests exist).
2. Import test: `python -c "from ai_agent.nodes.symmetry_enforcer import node_symmetry_enforcer; print('ok')"`.

### Pipeline Test
Run the full application and import a comparator SPICE netlist. Then press **Ctrl+P** (Run AI Initial Placement). Verify:
- The `[SYMMETRY]` block appears in the console log under Stage 1 (Topology Analyst).
- Stage 3.5 (Symmetry Enforcer) appears in the pipeline log.
- On the canvas: diff-pair fingers are equidistant from the vertical center line.
- Load pair fingers are further out, also equidistant.
- Tail current source is exactly centered.
- DRC: no overlaps introduced.

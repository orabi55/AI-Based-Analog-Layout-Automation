# Matching Tool Integration — Full GUI Workflow with Lock/Unlock

## Goal

Integrate a complete matching workflow into the symbolic layout editor so the user can:

1. **Select** 2+ transistors/fingers on the canvas
2. **Click Match** (toolbar) → choose a technique (Interdigitated, Common-Centroid 1D, Common-Centroid 2D)
3. **Apply** → devices are rearranged into the chosen pattern and become a **locked matched group** (visual highlight)
4. **AI protection** → the AI chatbot and AI placement pipeline **cannot move, swap, or delete** any locked device
5. **Unlock** → user can right-click or use toolbar to dissolve a group, releasing the constraint

## Current State

| What exists | Status |
|---|---|
| `_MatchDialog` (technique chooser) | ✅ Already exists (lines 90–239) |
| `_apply_matching()` with ABBA logic | ✅ Already exists (lines 1962–2087) but has bugs |
| `_matched_groups` list | ✅ Initialized (line 799), used in `_enforce_matched_group_move` |
| `_enforce_matched_group_move()` | ✅ Exists (line 1521) — moves group members together |
| `set_match_highlight()` on DeviceItem | ❌ **Missing** — called but never defined |
| AI command lock guard | ❌ **Missing** — `_handle_ai_command` doesn't check locked devices |
| Unlock / Dissolve group UI | ❌ **Missing** — no way to unlock a matched group |
| Visual indicator for locked groups | ❌ **Missing** — no persistent border/badge |
| Toolbar Unlock button | ❌ **Missing** |

## Proposed Changes

### Component 1: DeviceItem — Match Highlight Support

#### [MODIFY] [device_item.py](file:///c:/Users/DELL G3/Desktop/GP/AI-Based-Analog-Layout-Automation-Basic/symbolic_editor/device_item.py)

Add `set_match_highlight()` and `clear_match_highlight()` methods, plus a `_match_color` state variable. When set, the device draws a colored border overlay (similar to selection highlight but with a different color and a small "🔒" lock icon badge). This makes locked devices visually distinct at all times.

Changes:
- Add `self._match_color = None` in `__init__`
- Add `set_match_highlight(color: QColor)` method
- Add `clear_match_highlight()` method
- Add `is_match_locked() -> bool` property
- In `paint()`, draw a colored border + subtle fill when `_match_color` is set, with a "🔒" icon

---

### Component 2: AI Command Lock Guard

#### [MODIFY] [main.py](file:///c:/Users/DELL G3/Desktop/GP/AI-Based-Analog-Layout-Automation-Basic/symbolic_editor/main.py)

Add a `_is_device_locked(device_id)` helper that checks if any device belongs to a matched group, and `_get_device_group(device_id)` to find which group it's in. Then inject lock-aware logic in `_handle_ai_command()`:

- **`move` / `move_device`** — if device is locked, **move the ENTIRE matched group as one block** by the same delta. The internal pattern is preserved, but the group's anchor shifts. AI gets a message: "↕ Moved matched group (N devices) as a block."
- **`swap` / `swap_devices`** — if either device is locked, **reject** with message "⚠️ Cannot swap — device is in a locked matched group."
- **`abut`** — if either device is locked, **reject**
- **`move_row`** — for locked devices, **move the entire group** to the new row instead of just the device
- **`flip`** — if device is locked, **reject**

This ensures the AI can optimize placement by repositioning whole matched blocks, but can never break the internal matching pattern.

---

### Component 3: Fix `_apply_matching()` with Proper Parent-Based ABBA

#### [MODIFY] [main.py](file:///c:/Users/DELL G3/Desktop/GP/AI-Based-Analog-Layout-Automation-Basic/symbolic_editor/main.py)

Replace the current simple `_apply_matching()` (lines 1962–2087) with the improved version from the user's pasted code. Key improvements:

- **Parent-based grouping**: Split devices into groups by parent transistor name (e.g., `MM8_m1` → parent `MM8`), not just by position order
- **True ABBA interdigitation**: `A1 B1 B2 A2 A3 B3 B4 A4` pattern using parent groups
- **Common-centroid 1D**: `B_rev + A_fwd` mirror pattern
- **Common-centroid 2D**: Row 0 = ABBA of top halves, Row 1 = ABBA of bottom halves (reversed), centered
- **Uses item width** as placement step (not hardcoded pitch) for correct scene-unit positioning
- **Color-coded highlights**: Blue for interdigitated, green for CC-1D, purple for CC-2D
- **Chat feedback**: Shows group A/B breakdown in the chat panel

---

### Component 4: Unlock / Dissolve Matched Group

#### [MODIFY] [main.py](file:///c:/Users/DELL G3/Desktop/GP/AI-Based-Analog-Layout-Automation-Basic/symbolic_editor/main.py)

Add:
1. **`_on_unlock_matched_group()`** — toolbar action that:
   - Checks if any selected device is in a matched group
   - Removes the group from `_matched_groups`
   - Clears visual highlights on all group members
   - Shows chat confirmation

2. **Toolbar "Unlock" button** — added next to the Match button, shortcut `Ctrl+Shift+M`

3. **AI placement protection** — In `_on_ai_placement_completed()`, after loading new data, **restore matched group positions** so AI placement doesn't override them. Matched devices keep their locked positions.

---

### Component 5: Enforce Group Integrity During AI Placement

#### [MODIFY] [main.py](file:///c:/Users/DELL G3/Desktop/GP/AI-Based-Analog-Layout-Automation-Basic/symbolic_editor/main.py)

In `_on_ai_placement_completed()` (line 2354), after `_load_from_data_dict()`:
- Save matched group positions before AI runs
- After AI completes, restore the locked device positions
- This ensures AI placement respects matched groups

---

## Summary of All Files Changed

| File | Change |
|---|---|
| `symbolic_editor/device_item.py` | Add `set_match_highlight`, `clear_match_highlight`, lock visual |
| `symbolic_editor/main.py` | Fix `_apply_matching`, add lock guard, add unlock button, protect AI placement |

## Open Questions

> [!IMPORTANT]
> **No new files needed** — the standalone `matching_engine.py` module you showed me is a more advanced engine with finger-level interdigitation. The current `main.py` already has a working `_apply_matching()` that operates at the GUI item level (scene positions). I'll upgrade the existing `_apply_matching()` with the parent-group splitting logic from your pasted code, keeping everything in `main.py` where it already integrates with the GUI. Should I also create the standalone engine module for future use?

## Verification Plan

### Manual Verification
1. Launch the editor, load a circuit with matched devices (e.g., comparator)
2. Select 2+ devices → click Match → choose technique → verify ABBA pattern
3. Try AI chatbot "move MM8_m1 to (1,0)" → verify it's rejected with lock message
4. Try AI chatbot "swap MM8_m1 MM9_m1" → verify it's rejected
5. Click Unlock → verify devices become moveable again
6. Run AI placement → verify locked devices are not moved

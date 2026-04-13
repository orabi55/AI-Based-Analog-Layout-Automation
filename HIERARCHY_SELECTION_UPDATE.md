# Hierarchy Selection Blocking & Descend Feature

## Problem Statement
When selecting a big device (hierarchy group), users could select its child components even before descending to the child level. This created confusion as users could interact with elements that shouldn't be accessible at the current hierarchy level.

## Solution Overview
Implemented a hierarchy-aware selection system that:
1. **Blocks selection of child devices** when their parent hierarchy group is not descended
2. **Adds 'D' key shortcut** to quickly descend into hierarchy groups
3. **Maintains existing functionality** - dummy mode moved to `Shift+D`

## Changes Made

### 1. `hierarchy_group_item.py`
**Added hierarchy tracking capabilities:**

- **New attribute:** `_all_descendant_devices`
  - Stores a flat list of ALL descendant device items (recursive)
  - Built during initialization and updated when child groups are set

- **New method:** `_collect_all_descendant_devices()`
  - Recursively collects all device items from child groups
  - Returns complete list of devices that belong to this hierarchy

- **New method:** `get_all_descendant_devices()`
  - Public getter for accessing descendant devices
  - Used by editor to check device ownership

- **New method:** `set_child_groups(child_groups)`
  - Properly sets child groups and rebuilds descendant list
  - Ensures parent-child relationships are correctly established

### 2. `editor_view.py`
**Added hierarchy-aware selection logic with custom scene:**

- **New class:** `HierarchyAwareScene(QGraphicsScene)`
  - Custom QGraphicsScene that intercepts all selection events
  - Automatically blocks selection of devices in non-descended hierarchies
  - Works at the Qt event level, preventing selection before it happens
  - More robust than relying solely on signal handlers

- **New method:** `can_select_device(device_item)`
  - Checks if a device can be selected based on hierarchy state
  - Returns `False` if device belongs to a visible, non-descended hierarchy group
  - Returns `True` for devices that are accessible at current hierarchy level
  - Simple and clear logic: if parent group is visible and not descended → block

- **New method:** `descend_nearest_hierarchy()`
  - Called when user presses 'D' key
  - Finds first non-descended hierarchy group and descends into it
  - Provides feedback when all groups are already expanded

- **New method:** `ascend_all_hierarchy()`
  - Ascends from all descended hierarchy groups
  - Utility function for resetting hierarchy view

- **New method:** `descend_selected_hierarchy()`
  - Descends into the hierarchy group containing selected device(s)
  - Allows quick access to hierarchy of interest

- **Updated method:** `_on_selection_changed()`
  - Now uses improved blocking with signal management
  - Blocks all signals during deselection to prevent recursive updates
  - Re-fetches selection after blocking to ensure clean state

- **Updated method:** `keyPressEvent()`
  - Added 'D' key handler for hierarchy descend
  - 'D' (without modifiers) → descend into hierarchy
  - 'Escape' → ascend from hierarchy (existing)

- **Updated method:** `_build_hierarchy_groups()`
  - Now uses `set_child_groups()` instead of direct attribute assignment
  - Ensures proper hierarchy tree construction

- **Updated method:** `__init__()`
  - Now uses `HierarchyAwareScene(self)` instead of plain `QGraphicsScene()`
  - Passes editor reference to scene for hierarchy checking

### 3. `main.py`
**Updated keyboard shortcuts:**

- **Changed:** Dummy mode shortcut from `'D'` → `'Shift+D'`
  - Frees up 'D' key for hierarchy descend
  - More intuitive - descend is more common than dummy placement

## How It Works

### Two-Level Selection Blocking

The system uses **two levels of protection** to ensure devices cannot be selected when their hierarchy is not descended:

#### Level 1: Scene-Level Blocking (Primary)
**`HierarchyAwareScene.selectionChanged()`**

1. **User clicks or drags to select devices**
2. **Qt's built-in selection mechanism selects the devices**
3. **`HierarchyAwareScene.selectionChanged()` is called immediately**
4. **For each selected device:**
   - Calls `editor.can_select_device(device_item)`
   - Checks if device belongs to any visible, non-descended hierarchy group
   - If yes → blocks selection by calling `item.setSelected(False)`
   - All signal handling is blocked during this operation to prevent recursion
5. **Selection is cleaned up before any other handlers run**

#### Level 2: Signal Handler Blocking (Secondary)
**`SymbolicEditor._on_selection_changed()`**

1. **Called after scene-level blocking**
2. **Double-checks that no blocked devices remain selected**
3. **Deselects any remaining blocked devices**
4. **Processes the selection for valid devices**

This two-level approach ensures selection is blocked **both at the Qt event level AND at the application logic level**, making it impossible to accidentally select child devices.

### Selection Blocking Flow

1. **User tries to select a device** (click or rubber-band selection)
2. **`HierarchyAwareScene.selectionChanged()` intercepts** (Level 1)
3. **For each selected device:**
   - Calls `can_select_device(device_item)`
   - Checks if device belongs to any hierarchy group
   - If group is visible and not descended → block selection
   - Immediately deselects the device
4. **`_on_selection_changed()` fires** (Level 2)
   - Performs secondary check on remaining selected devices
   - Deselects any that slipped through
5. **If valid devices remain:**
   - Normal selection handling proceeds
   - Device clicked signal is emitted
   - Connection lines are shown

### Descend Flow

1. **User presses 'D' key**
2. **`keyPressEvent()` catches it** (only if no modifiers)
3. **Calls `descend_nearest_hierarchy()`**
4. **Searches for first non-descended group:**
   - Iterates through `_hierarchy_groups`
   - Finds group where `_is_descended == False` and `has_children() == True`
5. **Descends into the group:**
   - Calls `group.descend()`
   - Hides parent group
   - Shows child groups
   - Child devices become selectable
6. **If all groups already descended:**
   - Does nothing (user can press Escape to ascend)

## Usage Examples

### Example 1: Working with a Large Transistor Array

**Scenario:** You have `MM5` with m=3, nf=4 (12 finger devices total)

**Before this fix:**
- User could select individual fingers (`MM5_m1_f1`) even when viewing top-level
- Confusing - user thought they were selecting the whole array but only got one finger

**After this fix:**
- Clicking on the array selects the hierarchy group border (if enabled)
- Individual fingers CANNOT be selected until you descend
- Press **'D'** to descend into the hierarchy
- Now you can select individual multipliers (`MM5_m1`, `MM5_m2`, `MM5_m3`)
- Press **'D'** again to descend further into fingers
- Now individual fingers (`MM5_m1_f1`, etc.) are selectable
- Press **Escape** to ascend back up

### Example 2: Quick Hierarchy Navigation

```
D          → Descend into first available hierarchy
D          → Descend again (if nested hierarchies exist)
Escape     → Ascend one level
Escape     → Ascend to top (all hierarchies)
Shift+D    → Toggle dummy placement mode (unchanged functionality)
```

### Example 3: Selection Behavior

**Top-level view (not descended):**
- ✅ Can select the hierarchy group border
- ✅ Can select other standalone devices
- ❌ Cannot select child devices inside hierarchy

**After descending (pressed 'D'):**
- ✅ Can select child devices (now accessible)
- ✅ Can select other standalone devices
- Parent group is hidden (shows children instead)

## Testing

A comprehensive test suite is provided in `test_hierarchy_selection.py`:

```bash
python test_hierarchy_selection.py
```

**Tests cover:**
1. ✅ Selection blocking when hierarchy is not descended
2. ✅ Devices become selectable after descend
3. ✅ Selection blocked again after ascend
4. ✅ Descendant device collection is correct
5. ✅ **Scene-level selection blocking** (simulates actual user clicks)

All tests pass successfully. **Test 5 specifically verifies that the custom scene blocks selection at the Qt level**, ensuring that even direct calls to `setSelected(True)` are intercepted and blocked.

## Backward Compatibility

- ✅ All existing shortcuts still work (just moved 'D' to 'Shift+D' for dummy mode)
- ✅ Existing hierarchy group behavior preserved (double-click still works)
- ✅ No changes to data structures or file formats
- ✅ Escape key still ascends all hierarchies
- ✅ Device tree selection still works

## Future Enhancements (Optional)

1. **Visual feedback:** Gray out or add overlay to blocked devices
2. **Status bar message:** "Press D to descend into hierarchy" when blocked device is clicked
3. **Hierarchical selection:** Click parent to select all children at once (when descended)
4. **Multi-level descend:** Shortcut to descend multiple levels at once
5. **Hierarchy indicator:** Show current depth level in UI

## Files Modified

1. `symbolic_editor/hierarchy_group_item.py` - Added hierarchy tracking (`_all_descendant_devices`, `set_child_groups()`)
2. `symbolic_editor/editor_view.py` - Added `HierarchyAwareScene` class and selection blocking logic
3. `symbolic_editor/main.py` - Changed shortcut from 'D' to 'Shift+D' for dummy mode
4. `test_hierarchy_selection.py` - New comprehensive test suite (created)
5. `debug_hierarchy.py` - Debug utility for inspecting hierarchy structure (created)

### Key Implementation Details

**The fix uses a custom `HierarchyAwareScene` class that overrides `selectionChanged()` to intercept all selection events at the Qt level.** This is the critical piece that ensures devices cannot be selected when their parent hierarchy is not descended, regardless of how the selection is attempted (click, rubber-band drag, or programmatic).

The `can_select_device()` method provides the logic:
```python
# If hierarchy group is visible and not descended → block all its devices
if group.isVisible() and not group._is_descended:
    if device_item in group._all_descendant_devices:
        return False  # Block selection
```

This approach is **bulletproof** because:
- It works at the Qt event system level (before application logic runs)
- It blocks selection regardless of how it's triggered
- It has a secondary fallback in `_on_selection_changed()` for safety
- It's been thoroughly tested with 5 different test scenarios

## Summary

This fix implements proper hierarchy-aware selection blocking, preventing users from accidentally selecting child devices before descending to the appropriate hierarchy level. The 'D' key provides quick access to descend into hierarchies, making navigation much more intuitive. All changes are backward compatible and thoroughly tested.

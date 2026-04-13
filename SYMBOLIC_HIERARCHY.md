# Symbolic Hierarchy Visualization

## Overview

The hierarchy system now implements a **true symbolic view** where parent devices are shown as abstract rectangles that hide their children until you explicitly descend into them.

## Visual Structure

### Example: MM9 (m=3, nf=4)

```
LEVEL 1 - TOP LEVEL (Symbolic View)
═══════════════════════════════════════════════════════════

    ┌────────────────────────────────────┐
    │  MM9 (m=3, nf=4)             ▼    │  ← Red-bordered rectangle
    │                                    │     No devices visible inside
    │                                    │     Just the name and hierarchy info
    └────────────────────────────────────┘

    Press 'D' key or double-click header to descend
                     ↓
LEVEL 2 - MULTIPLIER LEVEL
═══════════════════════════════════════════════════════════

    ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
    │ MM9_m1 (nf=4) ▼│  │ MM9_m2 (nf=4) ▼│  │ MM9_m3 (nf=4) ▼│  ← Three multiplier rectangles
    │                 │  │                 │  │                 │     Each contains 4 fingers
    └─────────────────┘  └─────────────────┘  └─────────────────┘     (but fingers hidden)

    Double-click a multiplier to descend into it
                     ↓
LEVEL 3 - FINGER LEVEL (Detailed View)
═══════════════════════════════════════════════════════════

    ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐
    │ MM9_m1 │ │ MM9_m1 │ │ MM9_m1 │ │ MM9_m1 │  ← Individual finger devices
    │  _f1   │ │  _f2   │ │  _f3   │ │  _f4   │     Now visible and selectable
    └────────┘ └────────┘ └────────┘ └────────┘

    (Other multipliers MM9_m2, MM9_m3 still shown as rectangles)
```

## Key Features

### 1. Symbolic View (Not Descended)

When a hierarchy group is **NOT descended**:
- **Shows**: The group rectangle with red border and device name
- **Hides**: ALL child devices and child groups
- **Visual style**: Solid border (2.5px width)
- **Purpose**: Abstract representation - you see the device as a single unit

### 2. Descended View

When a hierarchy group **IS descended**:
- **Shows**: Child groups (if any) OR child devices (if no child groups)
- **Hides**: The parent group rectangle
- **Visual style**: Children shown with dashed borders (1.5px)
- **Purpose**: Detailed view - you can see and interact with components

### 3. Nested Hierarchies

Hierarchies can be nested:
```
MM9 (top) → MM9_m1, MM9_m2, MM9_m3 (multipliers) → MM9_m1_f1, MM9_m1_f2, ... (fingers)
```

Each level follows the same rules:
- Not descended = symbolic rectangle
- Descended = shows children

## How to Navigate

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| **`D`** | Descend into first available hierarchy |
| **`Escape`** | Ascend from all hierarchies |
| **Double-click header** | Descend/ascend specific group |

### Mouse Navigation

1. **Double-click header bar** of a hierarchy group to descend
2. **Double-click header bar** when already descended to ascend
3. **Drag the group** to move all children together

## Selection Behavior

### Selection Blocking

Devices **CANNOT be selected** when:
- Their parent hierarchy group is visible and not descended
- They are hidden (isVisible() == False)

Devices **CAN be selected** when:
- They are visible (their parent hierarchy is descended)
- They are standalone devices (not part of any hierarchy)

### Example Selection Flow

```
State: MM9 not descended
  ✗ Click on MM9_m1_f1 → Selection blocked (device hidden)
  ✓ Click on MM9 border → Selects hierarchy group (for dragging)

Press 'D' → MM9 descended, MM9_m1/m2/m3 visible
  ✗ Click on MM9_m1_f1 → Selection still blocked (multiplier not descended)
  ✓ Click on MM9_m1 → Can select/drag the multiplier group

Press 'D' → MM9_m1 descended
  ✓ Click on MM9_m1_f1 → Selection allowed (device now visible)
```

## Implementation Details

### HierarchyGroupItem Class

#### Key Methods

**`_update_child_visibility()`**
```python
if self._is_descended:
    self.setVisible(False)  # Hide this group
    if self._child_groups:
        for child in self._child_groups:
            child.setVisible(True)  # Show child groups
    else:
        for dev in self._device_items:
            dev.setVisible(True)  # Show devices
else:
    self.setVisible(True)  # Show this group (symbolic view)
    for child in self._child_groups:
        child.setVisible(False)  # Hide child groups
    for dev in self._device_items:
        dev.setVisible(False)  # Hide devices
```

**`descend()`**
- Sets `_is_descended = True`
- Calls `_update_child_visibility()`
- Emits `descend_requested` signal

**`ascend()`**
- Sets `_is_descended = False`
- Calls `_update_child_visibility()`
- Emits `ascend_requested` signal

**`set_child_groups(child_groups)`**
- Sets up parent-child relationships
- Rebuilds `_all_descendant_devices` list
- Calls `_update_child_visibility()` to enforce current state

### Visual Styling

**Not Descended (Symbolic View):**
- Border: Solid line, 2.5px width
- Default color: Red (#DC3C3C)
- Fill: Semi-transparent dark color
- Header: Shows device name and hierarchy info (m, nf)
- Indicator: ▼ (down arrow) in top-right corner

**Descended:**
- Border: Dashed line, 1.5px width
- Border color: Varies by hierarchy level (blue, purple, teal, amber)
- Group rectangle is hidden (setVisible(False))
- Children are shown with their own styling

## Testing

Run the comprehensive test suite:

```bash
python test_symbolic_hierarchy.py
```

### Test Coverage

1. ✅ **Symbolic view initialization** - Parent visible, children hidden
2. ✅ **Descend to multiplier level** - Parent hidden, multipliers visible, fingers hidden
3. ✅ **Descend to finger level** - Individual devices visible
4. ✅ **Selection blocking** - Hidden devices cannot be selected
5. ✅ **Ascending** - Returns to symbolic view, hides children

## Files Modified

1. **`hierarchy_group_item.py`**
   - Added `_update_child_visibility()` method
   - Updated `__init__()` to call visibility update
   - Modified `descend()` and `ascend()` to use visibility management
   - Updated `mouseDoubleClickEvent()` to handle device-only groups
   - Changed `paint()` to use solid borders for symbolic view

2. **`editor_view.py`**
   - Updated `_build_hierarchy_groups()` to not manually set child visibility
   - Visibility now managed entirely by `HierarchyGroupItem._update_child_visibility()`

3. **`test_symbolic_hierarchy.py`** (NEW)
   - Comprehensive test suite for symbolic hierarchy behavior

## Benefits

1. **Cleaner UI**: Top-level view shows devices as abstract units, not cluttered with details
2. **Better performance**: Fewer visible items when zoomed out
3. **Intuitive navigation**: Clear hierarchy levels with visual feedback
4. **Selection safety**: Impossible to accidentally select hidden devices
5. **Scalable**: Works with any depth of hierarchy nesting

## Troubleshooting

### Issue: Devices still visible when parent not descended

**Cause**: `_update_child_visibility()` not called
**Fix**: Ensure `set_child_groups()` is called after creating hierarchy

### Issue: Cannot descend into hierarchy

**Cause**: `has_children()` returns False and `_device_items` is empty
**Fix**: Verify child groups or devices are properly added to parent

### Issue: Selection not blocked for hidden devices

**Cause**: `HierarchyAwareScene` not being used
**Fix**: Verify editor uses `HierarchyAwareScene(self)` not `QGraphicsScene()`

## Future Enhancements

1. **Visual indicators**: Show hierarchy depth with color coding
2. **Expand all**: Button to descend all hierarchies at once
3. **Collapse all**: Button to ascend to top-level symbolic view
4. **Hierarchy path**: Show current position (e.g., "MM9 > MM9_m1 > ")
5. **Breadcrumbs**: Clickable navigation trail for hierarchy levels

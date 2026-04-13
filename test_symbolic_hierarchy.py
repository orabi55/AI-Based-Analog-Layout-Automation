"""
Test script to verify symbolic hierarchy visualization.

Tests:
1. Top-level hierarchy shows as symbolic rectangle (children hidden)
2. After descend, child groups/devices become visible
3. After descend again (if nested), grandchild devices become visible
4. Selection only works for visible devices
"""

import sys
import os

# Add the symbolic_editor directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'symbolic_editor'))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

def test_symbolic_hierarchy():
    """Test symbolic hierarchy visualization."""
    from editor_view import SymbolicEditor, HierarchyAwareScene
    from hierarchy_group_item import HierarchyGroupItem
    from device_item import DeviceItem
    
    app = QApplication.instance() or QApplication(sys.argv)
    
    # Create editor
    editor = SymbolicEditor()
    editor.resize(800, 600)
    editor.show()
    
    print("\n" + "="*70)
    print("SYMBOLIC HIERARCHY TEST")
    print("="*70)
    
    # Create a hierarchy: MM9 with 3 multipliers, 4 fingers each
    # Total: 12 finger devices
    
    # Create finger devices for multiplier 1
    mm9_m1_fingers = []
    for i in range(1, 5):
        dev = DeviceItem(f"MM9_m1_f{i}", "nmos", i*60, 0, 50, 30, nf=1)
        editor.scene.addItem(dev)
        editor.device_items[f"MM9_m1_f{i}"] = dev
        mm9_m1_fingers.append(dev)
    
    # Create finger devices for multiplier 2
    mm9_m2_fingers = []
    for i in range(1, 5):
        dev = DeviceItem(f"MM9_m2_f{i}", "nmos", i*60, 50, 50, 30, nf=1)
        editor.scene.addItem(dev)
        editor.device_items[f"MM9_m2_f{i}"] = dev
        mm9_m2_fingers.append(dev)
    
    # Create finger devices for multiplier 3
    mm9_m3_fingers = []
    for i in range(1, 5):
        dev = DeviceItem(f"MM9_m3_f{i}", "nmos", i*60, 100, 50, 30, nf=1)
        editor.scene.addItem(dev)
        editor.device_items[f"MM9_m3_f{i}"] = dev
        mm9_m3_fingers.append(dev)
    
    print("\nTest 1: Create top-level hierarchy group (MM9)")
    print("-" * 70)
    all_fingers = mm9_m1_fingers + mm9_m2_fingers + mm9_m3_fingers
    mm9_info = {"m": 3, "nf": 4, "is_array": False}
    
    # Create top-level MM9 group (symbolic view - no devices directly)
    mm9_group = HierarchyGroupItem("MM9", [], mm9_info)
    editor.scene.addItem(mm9_group)
    editor._hierarchy_groups.append(mm9_group)
    
    # Create multiplier child groups
    m1_info = {"m": 1, "nf": 4, "is_array": False}
    m2_info = {"m": 1, "nf": 4, "is_array": False}
    m3_info = {"m": 1, "nf": 4, "is_array": False}
    
    mm9_m1 = HierarchyGroupItem("MM9_m1", mm9_m1_fingers, m1_info)
    mm9_m2 = HierarchyGroupItem("MM9_m2", mm9_m2_fingers, m2_info)
    mm9_m3 = HierarchyGroupItem("MM9_m3", mm9_m3_fingers, m3_info)
    
    editor.scene.addItem(mm9_m1)
    editor.scene.addItem(mm9_m2)
    editor.scene.addItem(mm9_m3)
    
    # Set child groups (this will trigger _update_child_visibility)
    mm9_group.set_child_groups([mm9_m1, mm9_m2, mm9_m3])
    
    print(f"  MM9 group created:")
    print(f"    _is_descended: {mm9_group._is_descended}")
    print(f"    isVisible(): {mm9_group.isVisible()}")
    print(f"    has_children(): {mm9_group.has_children()}")
    
    print(f"\n  Multiplier groups (should be hidden initially):")
    print(f"    MM9_m1 isVisible(): {mm9_m1.isVisible()}")
    print(f"    MM9_m2 isVisible(): {mm9_m2.isVisible()}")
    print(f"    MM9_m3 isVisible(): {mm9_m3.isVisible()}")
    
    print(f"\n  Finger devices (should be hidden initially):")
    print(f"    MM9_m1_f1 isVisible(): {mm9_m1_fingers[0].isVisible()}")
    print(f"    MM9_m2_f1 isVisible(): {mm9_m2_fingers[0].isVisible()}")
    print(f"    MM9_m3_f1 isVisible(): {mm9_m3_fingers[0].isVisible()}")
    
    # Verify: Top-level group visible, all children hidden
    assert mm9_group.isVisible(), "MM9 group should be visible (symbolic view)"
    assert not mm9_m1.isVisible(), "MM9_m1 should be hidden (parent not descended)"
    assert not mm9_m2.isVisible(), "MM9_m2 should be hidden (parent not descended)"
    assert not mm9_m3.isVisible(), "MM9_m3 should be hidden (parent not descended)"
    assert not mm9_m1_fingers[0].isVisible(), "Finger devices should be hidden"
    print("  ✓ PASS: Symbolic view shows MM9 rectangle, hides all children")
    
    print("\nTest 2: Descend into MM9 (show multiplier groups)")
    print("-" * 70)
    mm9_group.descend()
    
    print(f"  After mm9_group.descend():")
    print(f"    MM9 _is_descended: {mm9_group._is_descended}")
    print(f"    MM9 isVisible(): {mm9_group.isVisible()}")
    print(f"    MM9_m1 isVisible(): {mm9_m1.isVisible()}")
    print(f"    MM9_m2 isVisible(): {mm9_m2.isVisible()}")
    print(f"    MM9_m3 isVisible(): {mm9_m3.isVisible()}")
    print(f"    MM9_m1_f1 isVisible(): {mm9_m1_fingers[0].isVisible()}")
    
    assert not mm9_group.isVisible(), "MM9 group should be hidden after descend"
    assert mm9_m1.isVisible(), "MM9_m1 should be visible after parent descend"
    assert mm9_m2.isVisible(), "MM9_m2 should be visible after parent descend"
    assert mm9_m3.isVisible(), "MM9_m3 should be visible after parent descend"
    # Multiplier groups should NOT be descended yet, so their fingers should be hidden
    assert not mm9_m1._is_descended, "MM9_m1 should not be descended"
    assert not mm9_m1_fingers[0].isVisible(), "Finger devices still hidden (multiplier not descended)"
    print("  ✓ PASS: Descending shows multiplier rectangles, fingers still hidden")
    
    print("\nTest 3: Descend into multiplier (show finger devices)")
    print("-" * 70)
    mm9_m1.descend()
    
    print(f"  After mm9_m1.descend():")
    print(f"    MM9_m1 _is_descended: {mm9_m1._is_descended}")
    print(f"    MM9_m1 isVisible(): {mm9_m1.isVisible()}")
    print(f"    MM9_m1_f1 isVisible(): {mm9_m1_fingers[0].isVisible()}")
    print(f"    MM9_m1_f2 isVisible(): {mm9_m1_fingers[1].isVisible()}")
    print(f"    MM9_m2_f1 isVisible(): {mm9_m2_fingers[0].isVisible()}")  # Should still be hidden
    
    assert not mm9_m1.isVisible(), "MM9_m1 should be hidden after descend"
    assert mm9_m1_fingers[0].isVisible(), "MM9_m1_f1 should be visible"
    assert mm9_m1_fingers[1].isVisible(), "MM9_m1_f2 should be visible"
    # Other multipliers' fingers should still be hidden
    assert not mm9_m2_fingers[0].isVisible(), "MM9_m2_f1 should still be hidden"
    print("  ✓ PASS: Descending multiplier shows its finger devices")
    
    print("\nTest 4: Selection blocking")
    print("-" * 70)
    # Try to select a hidden device
    mm9_m2_f1 = mm9_m2_fingers[0]
    can_select = editor.can_select_device(mm9_m2_f1)
    print(f"  Can select MM9_m2_f1 (hidden): {can_select}")
    assert not can_select, "Should not be able to select hidden device"
    
    # Try to select a visible device
    mm9_m1_f1 = mm9_m1_fingers[0]
    can_select = editor.can_select_device(mm9_m1_f1)
    print(f"  Can select MM9_m1_f1 (visible): {can_select}")
    assert can_select, "Should be able to select visible device"
    print("  ✓ PASS: Selection correctly blocked for hidden devices")
    
    print("\nTest 5: Ascend back up")
    print("-" * 70)
    mm9_m1.ascend()
    print(f"  After mm9_m1.ascend():")
    print(f"    MM9_m1 isVisible(): {mm9_m1.isVisible()}")
    print(f"    MM9_m1_f1 isVisible(): {mm9_m1_fingers[0].isVisible()}")
    assert mm9_m1.isVisible(), "MM9_m1 should be visible again"
    assert not mm9_m1_fingers[0].isVisible(), "Fingers should be hidden again"
    print("  ✓ PASS: Ascending hides finger devices")
    
    mm9_group.ascend()
    print(f"\n  After mm9_group.ascend():")
    print(f"    MM9 isVisible(): {mm9_group.isVisible()}")
    print(f"    MM9_m1 isVisible(): {mm9_m1.isVisible()}")
    assert mm9_group.isVisible(), "MM9 should be visible again (symbolic view)"
    assert not mm9_m1.isVisible(), "MM9_m1 should be hidden again"
    print("  ✓ PASS: Ascending to top shows symbolic view again")
    
    print("\n" + "="*70)
    print("ALL SYMBOLIC HIERARCHY TESTS PASSED!")
    print("="*70)
    print("\nExpected visual behavior:")
    print("  Level 1 (Top): [MM9 (m=3, nf=4)] - Red-bordered rectangle")
    print("  Press 'D' or double-click →")
    print("  Level 2: [MM9_m1] [MM9_m2] [MM9_m3] - Three multiplier rectangles")
    print("  Press 'D' or double-click multiplier →")
    print("  Level 3: Individual finger devices visible inside that multiplier")
    print("="*70 + "\n")
    
    # Clean up
    editor.close()
    
    return True

if __name__ == "__main__":
    try:
        success = test_symbolic_hierarchy()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

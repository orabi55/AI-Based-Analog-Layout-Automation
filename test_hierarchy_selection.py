"""
Test script to verify hierarchy selection blocking and descend functionality.

This script tests:
1. Devices cannot be selected when their parent hierarchy is not descended
2. Pressing 'D' key descends into hierarchy groups
3. After descending, child devices become selectable
4. Pressing Escape ascends from hierarchy groups
"""

import sys
import os

# Add the symbolic_editor directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'symbolic_editor'))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

def test_hierarchy_selection():
    """Test hierarchy selection blocking."""
    from editor_view import SymbolicEditor, HierarchyAwareScene
    from hierarchy_group_item import HierarchyGroupItem
    from device_item import DeviceItem
    
    app = QApplication.instance() or QApplication(sys.argv)
    
    # Create editor
    editor = SymbolicEditor()
    editor.resize(800, 600)
    editor.show()
    
    # Verify custom scene is being used
    assert isinstance(editor.scene, HierarchyAwareScene), "Editor should use HierarchyAwareScene"
    print("✓ Using HierarchyAwareScene for selection blocking")
    
    # Create some test device items
    dev1 = DeviceItem("MM1_m1_f1", "nmos", 0, 0, 50, 30, nf=1)
    dev2 = DeviceItem("MM1_m1_f2", "nmos", 60, 0, 50, 30, nf=1)
    dev3 = DeviceItem("MM1_m2_f1", "nmos", 0, 40, 50, 30, nf=1)
    dev4 = DeviceItem("MM1_m2_f2", "nmos", 60, 40, 50, 30, nf=1)
    
    editor.scene.addItem(dev1)
    editor.scene.addItem(dev2)
    editor.scene.addItem(dev3)
    editor.scene.addItem(dev4)
    
    editor.device_items["MM1_m1_f1"] = dev1
    editor.device_items["MM1_m1_f2"] = dev2
    editor.device_items["MM1_m2_f1"] = dev3
    editor.device_items["MM1_m2_f2"] = dev4
    
    # Create hierarchy group matching symbolic structure
    # Parent group has NO devices directly (symbolic view)
    # Child groups have the actual devices
    hierarchy_info = {"m": 2, "nf": 2, "is_array": False}
    
    group = HierarchyGroupItem("MM1", [], hierarchy_info)  # Empty device list
    editor.scene.addItem(group)
    editor._hierarchy_groups.append(group)
    
    # Create child groups (these have the actual devices)
    child1 = HierarchyGroupItem("MM1_m1", [dev1, dev2], {"m": 1, "nf": 2, "is_array": False})
    child2 = HierarchyGroupItem("MM1_m2", [dev3, dev4], {"m": 1, "nf": 2, "is_array": False})
    
    editor.scene.addItem(child1)
    editor.scene.addItem(child2)
    
    group.set_child_groups([child1, child2])
    
    # Test 1: Initially, devices should NOT be selectable (group not descended)
    print("Test 1: Selection blocking when hierarchy is not descended")
    print(f"  Group _is_descended: {group._is_descended}")
    print(f"  Group has children: {group.has_children()}")
    print(f"  Can select dev1: {editor.can_select_device(dev1)}")
    print(f"  Can select dev2: {editor.can_select_device(dev2)}")
    # Devices should NOT be selectable because parent group has children and is not descended
    assert not editor.can_select_device(dev1), "dev1 should NOT be selectable"
    assert not editor.can_select_device(dev2), "dev2 should NOT be selectable"
    print("  ✓ PASS: Devices are correctly blocked from selection")
    
    # Test 2: Descend into hierarchy (parent shows child groups)
    print("\nTest 2: Descend into hierarchy")
    group.descend()
    print(f"  Group _is_descended: {group._is_descended}")
    print(f"  Group visible: {group.isVisible()}")
    print(f"  Child1 visible: {child1.isVisible()}")
    print(f"  Child1 _is_descended: {child1._is_descended}")
    print(f"  dev1 visible: {dev1.isVisible()}")
    print(f"  Can select dev1: {editor.can_select_device(dev1)}")
    assert group._is_descended, "Group should be marked as descended"
    assert not group.isVisible(), "Parent group should be hidden"
    assert child1.isVisible(), "Child group should be visible after parent descend"
    # Child groups are visible but NOT descended, so their devices are still hidden
    assert not child1._is_descended, "Child group should not be descended yet"
    assert not dev1.isVisible(), "Devices still hidden (child not descended)"
    print("  ✓ PASS: Hierarchy descend shows child groups, devices still hidden")
    
    # Test 2b: Descend child group to show devices
    print("\nTest 2b: Descend child group (show devices)")
    child1.descend()
    print(f"  Child1 _is_descended: {child1._is_descended}")
    print(f"  Child1 visible: {child1.isVisible()}")
    print(f"  dev1 visible: {dev1.isVisible()}")
    print(f"  Can select dev1: {editor.can_select_device(dev1)}")
    assert not child1.isVisible(), "Child group should hide after descend"
    assert dev1.isVisible(), "Device should be visible after child descend"
    assert editor.can_select_device(dev1), "dev1 should be selectable after child descend"
    print("  ✓ PASS: Child descend makes devices visible and selectable")
    
    # Test 3: Ascend from child level
    print("\nTest 3: Ascend from child level")
    child1.ascend()
    print(f"  Child1 _is_descended: {child1._is_descended}")
    print(f"  Child1 visible: {child1.isVisible()}")
    print(f"  dev1 visible: {dev1.isVisible()}")
    print(f"  Can select dev1: {editor.can_select_device(dev1)}")
    assert child1.isVisible(), "Child group should be visible after ascend"
    assert not dev1.isVisible(), "Device should be hidden after child ascend"
    assert not editor.can_select_device(dev1), "dev1 should NOT be selectable after ascend"
    print("  ✓ PASS: Child ascend hides devices correctly")
    
    # Test 3b: Ascend from parent level
    print("\nTest 3b: Ascend from parent level")
    group.ascend()
    print(f"  Group _is_descended: {group._is_descended}")
    print(f"  Group visible: {group.isVisible()}")
    print(f"  Child1 visible: {child1.isVisible()}")
    assert not group._is_descended, "Group should not be descended after ascend"
    assert group.isVisible(), "Parent group should be visible after ascend (symbolic view)"
    assert not child1.isVisible(), "Child group should be hidden after parent ascend"
    assert not editor.can_select_device(dev1), "dev1 should NOT be selectable after parent ascend"
    print("  ✓ PASS: Parent ascend returns to symbolic view")
    
    # Test 4: Test descendant device collection
    print("\nTest 4: Descendant device collection")
    all_descendants = group.get_all_descendant_devices()
    print(f"  Total descendants: {len(all_descendants)}")
    print(f"  Expected: 4 devices (from child groups)")
    assert len(all_descendants) == 4, f"Expected 4 descendants, got {len(all_descendants)}"
    assert dev1 in all_descendants, "dev1 should be in descendants"
    assert dev2 in all_descendants, "dev2 should be in descendants"
    assert dev3 in all_descendants, "dev3 should be in descendants"
    assert dev4 in all_descendants, "dev4 should be in descendants"
    print("  ✓ PASS: Descendant collection is correct")
    
    print("\n" + "="*60)
    print("ALL TESTS PASSED!")
    print("="*60)
    
    # Test 5: Verify scene-level selection blocking
    print("\nTest 5: Scene-level selection blocking (simulating user click)")
    # Reset to non-descended state
    group.ascend()
    
    # Simulate selecting a device through the scene (like user clicking on it)
    dev1.setSelected(True)
    dev2.setSelected(True)
    
    # Give the scene a moment to process
    app.processEvents()
    
    # Check that devices were deselected by the scene
    print(f"  After selecting dev1 and dev2:")
    print(f"    dev1.isSelected(): {dev1.isSelected()}")
    print(f"    dev2.isSelected(): {dev2.isSelected()}")
    
    # The scene should have blocked this selection
    assert not dev1.isSelected(), "Scene should block dev1 selection when not descended"
    assert not dev2.isSelected(), "Scene should block dev2 selection when not descended"
    print("  ✓ PASS: Scene-level selection blocking works correctly")
    
    print("\n" + "="*60)
    print("ALL TESTS PASSED (INCLUDING SCENE-LEVEL BLOCKING)!")
    print("="*60)
    
    # Clean up
    editor.close()
    
    return True

if __name__ == "__main__":
    try:
        success = test_hierarchy_selection()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

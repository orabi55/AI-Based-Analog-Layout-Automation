"""
Debug script to check hierarchy group structure in the actual application.
Run this after loading a design to see hierarchy information.
"""

def debug_hierarchy_structure(editor):
    """Print detailed hierarchy structure for debugging."""
    print("\n" + "="*70)
    print("HIERARCHY STRUCTURE DEBUG INFO")
    print("="*70)
    
    print(f"\nTotal hierarchy groups: {len(editor._hierarchy_groups)}")
    
    for i, group in enumerate(editor._hierarchy_groups):
        print(f"\n{'─'*70}")
        print(f"Group {i}: {group._parent_name}")
        print(f"  _is_descended: {group._is_descended}")
        print(f"  isVisible(): {group.isVisible()}")
        print(f"  has_children(): {group.has_children()}")
        print(f"  Num direct devices: {len(group._device_items)}")
        print(f"  Num child groups: {len(group._child_groups)}")
        print(f"  Total descendants: {len(group._all_descendant_devices)}")
        
        print(f"\n  Direct devices:")
        for dev in group._device_items:
            print(f"    - {dev.device_name} (visible: {dev.isVisible()})")
        
        if group._child_groups:
            print(f"\n  Child groups:")
            for child in group._child_groups:
                print(f"    - {child._parent_name}")
                print(f"      _is_descended: {child._is_descended}")
                print(f"      isVisible(): {child.isVisible()}")
                print(f"      Num devices: {len(child._device_items)}")
                for dev in child._device_items:
                    print(f"        - {dev.device_name} (can select: {editor.can_select_device(dev)})")
        
        print(f"\n  Selection test for first device:")
        if group._device_items:
            dev = group._device_items[0]
            can_sel = editor.can_select_device(dev)
            print(f"    Device: {dev.device_name}")
            print(f"    Can select: {can_sel}")
            print(f"    Device visible: {dev.isVisible()}")
            print(f"    Group visible: {group.isVisible()}")
            print(f"    Group descended: {group._is_descended}")
    
    print("\n" + "="*70)
    print("END DEBUG INFO")
    print("="*70 + "\n")


# Usage: After loading your design, run this in the Python console:
# from debug_hierarchy import debug_hierarchy
# debug_hierarchy(editor)

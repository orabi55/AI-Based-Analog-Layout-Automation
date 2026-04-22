"""
test_group_movement.py — Verify that dragging one finger moves the whole group.

This script:
  1. Creates a minimal QApplication
  2. Loads a flat JSON with individual finger nodes
  3. Verifies that _sibling_group is wired for EVERY finger
  4. Simulates dragging MM0_f1 and checks that ALL siblings moved
"""
import sys
import os
import json

# Add the repository root to the path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPointF
from symbolic_editor.editor_view import SymbolicEditor


def main():
    app = QApplication(sys.argv)

    # Load test JSON
    json_path = os.path.join(
        os.path.dirname(__file__),
        '..', 'examples', 'current_mirror', 'NEW.json'
    )
    with open(json_path) as f:
        data = json.load(f)

    nodes = data["nodes"]
    print(f"Loaded {len(nodes)} finger nodes from NEW.json\n")

    # Create editor and load placement
    editor = SymbolicEditor()
    editor.load_placement(nodes, compact=False)

    # Simulate set_terminal_nets (from SPICE parsing or _graph.json)
    # For NEW.json there are no terminal_nets, so the manual fallback
    # should have already wired the groups.

    print("=" * 60)
    print("TEST 1: Are sibling groups wired?")
    print("=" * 60)

    # Check every device item
    items_with_siblings = 0
    items_without = 0
    parent_groups_seen = {}

    for dev_id, item in editor.device_items.items():
        group_size = len(getattr(item, '_sibling_group', []))
        parent = getattr(item, '_parent_id', None)
        if group_size > 1:
            items_with_siblings += 1
            parent_groups_seen.setdefault(parent, set()).add(group_size)
        else:
            items_without += 1

    print(f"  Items with sibling groups: {items_with_siblings}")
    print(f"  Items without: {items_without}")
    for parent, sizes in sorted(parent_groups_seen.items()):
        print(f"    {parent}: group_size = {sizes}")

    if items_with_siblings == 0:
        print("\n  FAIL: No sibling groups wired!")
        print("  This means the manual MATCHED_GROUPS fallback isn't activating.")
        sys.exit(1)

    print(f"\n  PASS: {items_with_siblings}/{len(editor.device_items)} items "
          f"have sibling groups\n")

    # ── TEST 2: Simulate dragging MM0_f1 ──
    print("=" * 60)
    print("TEST 2: Does dragging one finger move siblings?")
    print("=" * 60)

    target_id = "MM0_f1"
    target_item = editor.device_items.get(target_id)
    if not target_item:
        print(f"  SKIP: {target_id} not found in device_items")
        sys.exit(0)

    # Record positions of all items BEFORE the move
    before_positions = {}
    for dev_id, item in editor.device_items.items():
        before_positions[dev_id] = (item.pos().x(), item.pos().y())

    # Get siblings of MM0_f1
    siblings = target_item._sibling_group
    sibling_ids = [item.device_name for item in siblings]
    print(f"  Dragging: {target_id}")
    print(f"  Sibling group ({len(siblings)} items): "
          f"{sibling_ids[:5]}... " if len(sibling_ids) > 5 else f"{sibling_ids}")

    # Simulate a drag: move MM0_f1 by (100, 50)
    dx, dy = 100.0, 50.0
    old_pos = target_item.pos()
    target_item._propagating_move = False

    # Simulate what mouseMoveEvent does
    for sibling in target_item._sibling_group:
        if sibling is not target_item:
            sibling._propagating_move = True
            sibling.moveBy(dx, dy)
            sibling._propagating_move = False
    target_item.moveBy(dx, dy)

    # Check which items moved
    moved_items = []
    stayed_items = []
    for dev_id, item in editor.device_items.items():
        bx, by = before_positions[dev_id]
        cx, cy = item.pos().x(), item.pos().y()
        if abs(cx - bx - dx) < 0.01 and abs(cy - by - dy) < 0.01:
            moved_items.append(dev_id)
        elif abs(cx - bx) < 0.01 and abs(cy - by) < 0.01:
            stayed_items.append(dev_id)
        else:
            print(f"  UNEXPECTED: {dev_id} moved by "
                  f"({cx-bx:.1f}, {cy-by:.1f}) instead of ({dx}, {dy})")

    print(f"\n  Moved ({len(moved_items)}): {moved_items[:10]}...")
    print(f"  Stayed ({len(stayed_items)}): {stayed_items[:10]}...")

    # For the manual matched group ["MM0", "MM1", "MM2"],
    # ALL fingers of MM0+MM1+MM2 should have moved
    expected_moved_parents = {"MM0", "MM1", "MM2"}
    moved_parents = set()
    for dev_id in moved_items:
        for p in expected_moved_parents:
            if dev_id.startswith(p + "_f"):
                moved_parents.add(p)

    if moved_parents == expected_moved_parents:
        print(f"\n  PASS: All parents {expected_moved_parents} moved together!")
    elif len(moved_parents) == 1:
        print(f"\n  PARTIAL: Only {moved_parents} moved (parent siblings only)")
    else:
        print(f"\n  FAIL: Only {moved_parents} moved out of {expected_moved_parents}")

    # Check that PMOS devices did NOT move
    pmos_moved = [d for d in moved_items if d.startswith("MM3") or
                  d.startswith("MM4") or d.startswith("MM5")]
    if not pmos_moved:
        print(f"  PASS: PMOS devices stayed in place (correct isolation)")
    else:
        print(f"  INFO: PMOS devices also moved: {pmos_moved}")

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"  Input: {len(nodes)} flat finger nodes")
    print(f"  Sibling wiring: {items_with_siblings}/{len(editor.device_items)}")
    print(f"  Dragging {target_id}: {len(moved_items)} items moved together")
    print(f"  Matched group parents that moved: {sorted(moved_parents)}")

    app.quit()


if __name__ == "__main__":
    main()

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.abspath(os.path.dirname(__file__)), "symbolic_editor"))
from PySide6.QtWidgets import QApplication
from symbolic_editor.main import SymbolicEditor

def test_hierarchy():
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)

    editor = SymbolicEditor()
    sp_file = "Examples/Std_Cell/Std_Cell.sp"
    
    if not os.path.exists(sp_file):
        print(f"Skipping: test file {sp_file} not found")
        return

    # Trigger import directly
    data = editor._run_parser_pipeline(sp_file, "")
    editor._load_from_data_dict(data, sp_file)

    v = editor.editor_view

    # Verify BlockItems created
    blocks = v._block_items
    num_blocks = len(blocks)
    print(f"Found {num_blocks} BlockItems for Symbol View Mode")
    assert num_blocks > 0, "No BlockItems generated!"

    # Verify view levels
    v.set_view_level("symbol")
    assert blocks[0].isVisible() == True, "BlockItem should be visible in symbol mode"
    assert blocks[0]._device_items[0].isVisible() == False, "Devices should be hidden in symbol mode"

    v.set_view_level("transistor")
    assert blocks[0].isVisible() == False, "BlockItem should be hidden in transistor mode"
    assert blocks[0]._device_items[0].isVisible() == True, "Devices should be visible in transistor mode"

    # Test moving the block updates devices
    v.set_view_level("symbol")
    b0 = blocks[0]
    dev0 = b0._device_items[0]
    dev_orig_pos = dev0.pos()

    # Emulate a drag by setting position
    b0.setPos(b0.pos().x() + 100, b0.pos().y() + 50)
    
    dev_new_pos = dev0.pos()
    print(f"Device moved from {dev_orig_pos} to {dev_new_pos}")
    assert dev_new_pos.x() == dev_orig_pos.x() + 100
    assert dev_new_pos.y() == dev_orig_pos.y() + 50
    print("ALL TESTS PASSED: Hierarchical placement and views work automatically.")

if __name__ == "__main__":
    test_hierarchy()

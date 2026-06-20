import pytest
import os
import sys

# Ensure symbolic_editor is in PYTHONPATH
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if os.path.join(_ROOT, "symbolic_editor") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "symbolic_editor"))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QColor
from symbolic_editor.editor_view import SymbolicEditor
from symbolic_editor.device_item import DeviceItem

@pytest.fixture(scope="module")
def qt_app():
    """Ensure QApplication is running offscreen."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance() or QApplication([])
    yield app

def test_sibling_selection_propagation(qt_app):
    # Initialize SymbolicEditor
    editor = SymbolicEditor()
    
    # Create two sibling devices sharing the same parent
    item1 = DeviceItem("MM1_f1", "nmos", x=0, y=0, width=50, height=100)
    item2 = DeviceItem("MM1_f2", "nmos", x=60, y=0, width=50, height=100)
    
    # Setup sibling groups
    item1._sibling_group = [item1, item2]
    item2._sibling_group = [item1, item2]
    
    editor.scene.addItem(item1)
    editor.scene.addItem(item2)
    editor.device_items = {"MM1_f1": item1, "MM1_f2": item2}
    
    # Initially, neither is selected
    assert not item1.isSelected()
    assert not item2.isSelected()
    
    # Select item1
    item1.setSelected(True)
    
    # Run event loop processing to trigger signals
    QApplication.processEvents()
    
    # Verify that selection propagated to item2
    assert item1.isSelected()
    assert item2.isSelected()
    
    # Deselect item1
    item1.setSelected(False)
    QApplication.processEvents()
    
    # Deselecting one item directly clears connections and highlights normally,
    # but does not automatically deselect the other siblings since Qt's clearSelection/deselect
    # is handled by the normal selection model. Let's make sure it doesn't crash.

def test_highlight_net_no_flight_lines(qt_app):
    # Initialize SymbolicEditor
    editor = SymbolicEditor()
    
    # Create a couple of devices
    item1 = DeviceItem("MM1_f1", "nmos", x=0, y=0, width=50, height=100)
    item2 = DeviceItem("MM2_f1", "nmos", x=100, y=0, width=50, height=100)
    
    editor.scene.addItem(item1)
    editor.scene.addItem(item2)
    editor.device_items = {"MM1_f1": item1, "MM2_f1": item2}
    
    # Setup terminal nets
    editor._terminal_nets = {
        "MM1_f1": {"G": "CLK", "D": "NET_A", "S": "VSS"},
        "MM2_f1": {"G": "CLK", "D": "NET_B", "S": "VSS"}
    }
    
    # Highlight a net
    editor.highlight_net_by_name("CLK")
    
    # Verify that the net terminals are colored / highlighted on the items,
    # but no flight lines (QGraphicsPathItem / ellipse dots) are drawn in the scene.
    # The normal ratsnest dotted lines or dots would have been added to editor._conn_lines.
    assert len(editor._conn_lines) == 0
    assert editor._highlighted_net == "CLK"

def test_highlight_device_sibling_and_suffix_propagation(qt_app):
    editor = SymbolicEditor()
    item1 = DeviceItem("MM1.m1", "nmos", x=0, y=0, width=50, height=100)
    item2 = DeviceItem("MM1.m2", "nmos", x=60, y=0, width=50, height=100)
    item1._sibling_group = [item1, item2]
    item2._sibling_group = [item1, item2]
    editor.scene.addItem(item1)
    editor.scene.addItem(item2)
    editor.device_items = {"MM1.m1": item1, "MM1.m2": item2}

    # Test exact match sibling propagation
    editor.highlight_device("MM1.m1")
    assert item1.isSelected()
    assert item2.isSelected()

    # Clear selection
    editor.highlight_device(None)
    assert not item1.isSelected()
    assert not item2.isSelected()

    # Test parent-level match with dot prefix fallback
    editor.highlight_device("MM1")
    assert item1.isSelected()
    assert item2.isSelected()

    # Clear selection
    editor.highlight_device(None)
    assert not item1.isSelected()
    assert not item2.isSelected()

    # Test highlight_device_list with prefix matching
    editor.highlight_device_list(["MM1"])
    assert item1.isSelected()
    assert item2.isSelected()

import sys
import os
import traceback
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.abspath('.'))
from symbolic_editor.main import MainWindow

def main():
    app = QApplication(sys.argv)
    win = MainWindow('')
    try:
        # Mock 2 selected devices of the same type
        win.editor.selected_device_ids = lambda: ["MN1", "MN2"]
        
        class MockItem:
            device_type = "nmos"
            def pos(self):
                from PySide6.QtCore import QPointF
                return QPointF(0, 0)
            def setPos(self, x, y):
                pass
            def boundingRect(self):
                from PySide6.QtCore import QRectF
                return QRectF(0, 0, 10, 10)
        
        win.editor.device_items = {"MN1": MockItem(), "MN2": MockItem()}
        
        # Test dialog
        from unittest.mock import patch
        with patch('symbolic_editor.main._MatchDialog.exec', return_value=1): # Accept
            with patch('symbolic_editor.main._MatchDialog.get_technique', return_value='interdigitated'):
                win._on_match_devices()
                print("TEST COMPLETE: No Exception")
                return 0
    except Exception as e:
        traceback.print_exc()
        return 1
    finally:
        win.close()
        app.quit()

if __name__ == '__main__':
    raise SystemExit(main())

import sys
import os
import traceback
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.abspath('.'))

def main():
    app = QApplication(sys.argv)
    from symbolic_editor.main import MainWindow
    win = MainWindow('')
    try:
        win._on_match_devices()
        print("Success")
        return 0
    except Exception as e:
        traceback.print_exc()
        return 1
    finally:
        win.close()
        app.quit()

if __name__ == '__main__':
    raise SystemExit(main())

import sys
import traceback
from PySide6.QtWidgets import QApplication

def main():
    app = QApplication(sys.argv)
    from symbolic_editor.main import MainWindow
    win = MainWindow('')
    try:
        win._on_match_devices()
        print("Success")
    except Exception as e:
        traceback.print_exc()

if __name__ == '__main__':
    main()

import sys
import os
import traceback
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.abspath('symbolic_editor'))
from main import MainWindow

def main():
    app = QApplication(sys.argv)
    win = MainWindow('')
    try:
        win._on_match_devices()
        print("Success")
    except Exception as e:
        traceback.print_exc()

if __name__ == '__main__':
    main()

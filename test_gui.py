import logging
logging.basicConfig(level=logging.ERROR)
import sys, os, traceback
sys.path.insert(0, os.path.abspath('symbolic_editor'))
from symbolic_editor.main import MainWindow
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
w = MainWindow(None)
data = w._run_parser_pipeline('Test/Inverter.sp', '')
w._load_from_data_dict(data, '')
w._sync_node_positions()
d2 = w._build_output_data()
d2['terminal_nets'] = w._terminal_nets
try:
    out = w._run_ai_initial_placement(d2)
    print('Finished')
except Exception as e:
    traceback.print_exc()

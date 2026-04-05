import sys
from PySide6.QtWidgets import QApplication
from symbolic_editor.main import MainWindow

def main():
    app = QApplication(sys.argv)
    w = MainWindow(None)
    data = w._run_parser_pipeline("Test/Inverter.sp", "")
    print("Pipeline passed. Data length:", len(data))
    
    # We simulate _on_run_ai_placement
    # But bypass the GUI QMessageBox for testing
    w._sync_node_positions()
    data = w._build_output_data()
    data["terminal_nets"] = w._terminal_nets

    print("Running initial placement...")
    out = w._run_ai_initial_placement(data)
    print("Done placement", out)

if __name__ == "__main__":
    main()

import sys
import os

# Add the project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from config.design_rules import (
        PASSIVE_WIDTH_UM, PASSIVE_HEIGHT_UM,
        PASSIVE_CAP_WIDTH_UM, PASSIVE_CAP_HEIGHT_UM
    )
    print(f"Resistor: {PASSIVE_WIDTH_UM}x{PASSIVE_HEIGHT_UM}")
    print(f"Capacitor: {PASSIVE_CAP_WIDTH_UM}x{PASSIVE_CAP_HEIGHT_UM}")
    
    from symbolic_editor.editor_view import SymbolicEditor
    print("SymbolicEditor imported successfully")
    
    from symbolic_editor.passive_item import ResistorItem, CapacitorItem
    print("Passive items imported successfully")
    
    # Test scaling logic
    res = ResistorItem("R1", 0, 0, 100, 40)
    res.set_value(1000) # 1k
    print(f"Resistor width at 1k: {res.rect().width()}")
    
    cap = CapacitorItem("C1", 0, 0, 100, 40)
    cap.set_value(1e-12) # 1p
    print(f"Capacitor width at 1p: {cap.rect().width()}")
    
    if cap.rect().width() > res.rect().width():
        print("SUCCESS: Capacitor is wider than resistor at baseline")
    else:
        print(f"FAILURE: Capacitor ({cap.rect().width()}) is not wider than resistor ({res.rect().width()})")

except Exception as e:
    print(f"Error during verification: {e}")
    import traceback
    traceback.print_exc()

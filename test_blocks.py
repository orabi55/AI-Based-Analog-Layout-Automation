import os
import sys
from pprint import pprint

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from parser.netlist_reader import read_netlist_with_blocks

def test_parsing():
    sp_file = "Examples/Std_Cell/Std_Cell.sp"
    if not os.path.exists(sp_file):
        print(f"Error: Could not find {sp_file}")
        return

    netlist, block_map = read_netlist_with_blocks(sp_file)
    print("--- Parsed Block Map ---")
    pprint(block_map)


if __name__ == "__main__":
    test_parsing()

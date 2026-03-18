"""Test finger expansion block propagation without needing PySide6."""
import sys
import os
import re

proj = r"d:\Senior 2\Layout_Project\Automation\AI-Based-Analog-Layout-Automation\AI-Based-Analog-Layout-Automation"
sys.path.insert(0, proj)

from parser.netlist_reader import read_netlist_with_blocks

sp_file = os.path.join(proj, "examples", "std_cell", "Std_Cell.sp")

print("=== Test 1: Block map from parser ===")
netlist, block_map = read_netlist_with_blocks(sp_file)

print(f"Total devices in netlist: {len(netlist.devices)}")
print(f"Total entries in block_map: {len(block_map)}")

# The block_map keys are the pre-finger-expansion names (e.g. XI3_MM28)
# After finger expansion, device names become XI3_MM28_f1, XI3_MM28_f2
# Test the regex fix
print("\n=== Test 2: Regex finger-expansion matching ===")
finger_expanded_names = [name for name in netlist.devices.keys() if "_f" in name]
print(f"Finger-expanded device names: {len(finger_expanded_names)}")
for name in finger_expanded_names[:6]:
    print(f"  {name}")

# Simulate the fix logic from _run_parser_pipeline
matched = 0
unmatched = 0
for dev_name in netlist.devices.keys():
    block_info = block_map.get(dev_name)
    if block_info is None:
        base = re.sub(r'_f\d+$', '', dev_name)
        if base != dev_name:
            block_info = block_map.get(base)
    if block_info:
        matched += 1
    else:
        unmatched += 1
        print(f"  UNMATCHED: {dev_name}")

print(f"\nMatched: {matched}, Unmatched: {unmatched}")
assert unmatched == 0, f"{unmatched} devices still missing block tags!"

# Test 3: Blocks reconstruction from per-node data
print("\n=== Test 3: Blocks dict reconstruction ===")
blocks = {}
for dev_name, info in block_map.items():
    inst = info["instance"]
    if inst not in blocks:
        blocks[inst] = {"subckt": info["subckt"], "devices": []}
    blocks[inst]["devices"].append(dev_name)

# Also add finger-expanded devices
for dev_name in netlist.devices.keys():
    if dev_name not in block_map:
        base = re.sub(r'_f\d+$', '', dev_name)
        info = block_map.get(base)
        if info:
            inst = info["instance"]
            blocks[inst]["devices"].append(dev_name)

for inst, info in blocks.items():
    print(f"  {inst} ({info['subckt']}): {len(info['devices'])} devices")

total_in_blocks = sum(len(info["devices"]) for info in blocks.values())
print(f"\nTotal devices across all blocks: {total_in_blocks}")
print(f"Total devices in netlist: {len(netlist.devices)}")
assert total_in_blocks == len(netlist.devices), "Not all devices are in blocks!"

print("\nALL TESTS PASSED!")

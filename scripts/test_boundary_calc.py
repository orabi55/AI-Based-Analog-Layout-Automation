"""
Test boundary net calculation with swapped_sd.
"""
import sys
sys.path.insert(0, '.')

from ai_agent.placement.finger_grouper import get_block_boundary_nets

# Test case: MM5_m1 with swapped=True
node = {
    'id': 'MM5_m1',
    'net_s': 'VOUTP',
    'net_d': 'VDD',
    'swapped_sd': True,
    'geometry': {'orientation': 'R0'},
    'electrical': {'nf': 1}
}

left, right = get_block_boundary_nets([node], False)
print(f"MM5_m1 (swapped=True): left={left}, right={right}")
print(f"Expected: left=VDD, right=VOUTP (because swapped)")

# Now test with swapped=False
node['swapped_sd'] = False
left, right = get_block_boundary_nets([node], False)
print(f"\nMM5_m1 (swapped=False): left={left}, right={right}")
print(f"Expected: left=VOUTP, right=VDD")

# Test MM4_m1
node2 = {
    'id': 'MM4_m1',
    'net_s': 'VDD',
    'net_d': 'VOUTN',
    'swapped_sd': False,
    'geometry': {'orientation': 'R0'},
    'electrical': {'nf': 1}
}

left2, right2 = get_block_boundary_nets([node2], False)
print(f"\nMM4_m1 (swapped=False): left={left2}, right={right2}")
print(f"Expected: left=VDD, right=VOUTN")

print(f"\nCan they abut? MM5_m1 right={right} == MM4_m1 left={left2}? {right == left2}")

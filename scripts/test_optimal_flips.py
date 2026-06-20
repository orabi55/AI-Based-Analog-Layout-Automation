"""
Test boundary net calculation and find optimal flips for row y=2.454.
"""
import sys
sys.path.insert(0, '.')

from ai_agent.placement.finger_grouper import get_block_boundary_nets

# Initial device state from row y=2.454
devices = [
    {'id': 'MM5_m1', 'net_s': 'VDD', 'net_d': 'VOUTP', 'swapped_sd': True},
    {'id': 'MM4_m1', 'net_s': 'VDD', 'net_d': 'VOUTN', 'swapped_sd': False},
    {'id': 'MM4_m2', 'net_s': 'VDD', 'net_d': 'VOUTN', 'swapped_sd': True},
    {'id': 'MM5_m2', 'net_s': 'VDD', 'net_d': 'VOUTP', 'swapped_sd': False},
    {'id': 'MM5_m3', 'net_s': 'VDD', 'net_d': 'VOUTP', 'swapped_sd': True},
    {'id': 'MM4_m3', 'net_s': 'VDD', 'net_d': 'VOUTN', 'swapped_sd': True},
    {'id': 'MM4_m4', 'net_s': 'VDD', 'net_d': 'VOUTN', 'swapped_sd': False},
    {'id': 'MM5_m4', 'net_s': 'VDD', 'net_d': 'VOUTP', 'swapped_sd': False},
]

print("=== Testing Boundary Net Calculation ===\n")

# Test with no flips
print("No flips:")
for i, dev in enumerate(devices):
    node = {**dev, 'geometry': {'orientation': 'R0'}, 'electrical': {'nf': 1}}
    left, right = get_block_boundary_nets([node], False)
    print(f"  {dev['id']:10s} swapped={dev['swapped_sd']} -> left={left:8s} right={right:8s}")

print("\n=== Finding Optimal Flip Combination ===\n")

from itertools import product

best_abutments = 0
best_flips = None

# Try all 2^8 = 256 combinations
for flip_combo in product([False, True], repeat=len(devices)):
    # Create nodes with flips applied
    nodes = []
    for i, dev in enumerate(devices):
        net_s = dev['net_s']
        net_d = dev['net_d']
        swapped = dev['swapped_sd']
        
        # Apply flip
        if flip_combo[i]:
            swapped = not swapped
            net_s, net_d = net_d, net_s
        
        node = {
            'id': dev['id'],
            'net_s': net_s,
            'net_d': net_d,
            'swapped_sd': swapped,
            'geometry': {'orientation': 'R0'},
            'electrical': {'nf': 1}
        }
        nodes.append(node)
    
    # Count abutments
    abutments = 0
    for i in range(len(nodes) - 1):
        _, right = get_block_boundary_nets([nodes[i]], False)
        left, _ = get_block_boundary_nets([nodes[i+1]], False)
        
        if right and left and right != 'NC' and left != 'NC' and right == left:
            abutments += 1
    
    if abutments > best_abutments:
        best_abutments = abutments
        best_flips = flip_combo

print(f"Best abutments: {best_abutments} out of {len(devices)-1} possible")
print(f"Best flip combination: {best_flips}\n")

# Show the optimal configuration
print("Optimal configuration:")
nodes = []
for i, dev in enumerate(devices):
    net_s = dev['net_s']
    net_d = dev['net_d']
    swapped = dev['swapped_sd']
    
    if best_flips[i]:
        swapped = not swapped
        net_s, net_d = net_d, net_s
    
    node = {
        'id': dev['id'],
        'net_s': net_s,
        'net_d': net_d,
        'swapped_sd': swapped,
        'geometry': {'orientation': 'R0'},
        'electrical': {'nf': 1}
    }
    nodes.append(node)
    
    left, right = get_block_boundary_nets([node], False)
    print(f"  {dev['id']:10s} swapped={swapped} -> left={left:8s} right={right:8s}")

print("\nAbutment verification:")
for i in range(len(nodes) - 1):
    _, right = get_block_boundary_nets([nodes[i]], False)
    left, _ = get_block_boundary_nets([nodes[i+1]], False)
    
    can_abut = right == left
    print(f"  {nodes[i]['id']:10s} right={right:8s} <-> {nodes[i+1]['id']:10s} left={left:8s} {'✓' if can_abut else '✗'}")

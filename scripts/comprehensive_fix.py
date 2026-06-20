"""
Comprehensive fix for comparator placement abutment issues.
This script:
1. Removes bridge dummies between active devices
2. Maximizes abutment through intelligent finger reordering
3. Preserves symmetry where possible
4. Fills empty spaces with edge dummies only
"""
import sys
import json
import copy
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

def fix_comparator_placement(input_file, output_file):
    """Fix comparator placement for maximum abutment and symmetry."""
    
    # Load the placement
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    nodes = data['nodes']
    terminal_nets = data.get('terminal_nets', {})
    
    print(f"Loaded {len(nodes)} nodes")
    
    # Group by row
    rows = defaultdict(list)
    for n in nodes:
        y = round(n.get('geometry', {}).get('y', 0.0), 6)
        rows[(y, n.get('type', 'unknown'))].append(n)
    
    print(f"Found {len(rows)} rows")
    
    # Process each row
    for key in sorted(rows.keys()):
        y, typ = key
        row = sorted(rows[key], key=lambda n: n['geometry']['x'])
        
        print(f"\nProcessing row y={y:.3f} ({typ}): {len(row)} devices")
        
        # Separate active devices and dummies
        active = [n for n in row if not n.get('is_dummy', False)]
        dummies = [n for n in row if n.get('is_dummy', False)]
        
        print(f"  Active: {len(active)}, Dummies: {len(dummies)}")
        
        # Group active devices by parent transistor
        blocks = defaultdict(list)
        for n in active:
            parent_id = n['id'].split('_')[0]
            blocks[parent_id].append(n)
        
        print(f"  Blocks: {list(blocks.keys())}")
        
        # For matched pairs (interleaved), try to maximize abutment
        # by checking if reordering or flipping helps
        if len(blocks) == 2:
            block_ids = list(blocks.keys())
            b1, b2 = block_ids[0], block_ids[1]
            
            # Check if this is an interleaved pattern
            positions = []
            for n in active:
                parent = n['id'].split('_')[0]
                positions.append(parent)
            
            is_interleaved = False
            for i in range(len(positions) - 1):
                if positions[i] != positions[i + 1]:
                    is_interleaved = True
                    break
            
            if is_interleaved:
                print(f"  Detected interleaved pattern: {positions}")
                
                # Try to maximize abutment by flipping fingers
                # For each pair of adjacent fingers from different blocks,
                # check if flipping one of them would create an abutment
                
                for i in range(len(active) - 1):
                    curr = active[i]
                    next_node = active[i + 1]
                    
                    curr_parent = curr['id'].split('_')[0]
                    next_parent = next_node['id'].split('_')[0]
                    
                    if curr_parent != next_parent:
                        # These are from different blocks - check if they can abut
                        curr_d = curr.get('net_d', '')
                        curr_s = curr.get('net_s', '')
                        next_s = next_node.get('net_s', '')
                        next_d = next_node.get('net_d', '')
                        
                        # Check all combinations of flipping
                        can_abut = False
                        
                        # Current config
                        if curr_d == next_s:
                            can_abut = True
                        
                        # Try flipping current
                        if curr_s == next_s:
                            can_abut = True
                            curr['swapped_sd'] = not curr.get('swapped_sd', False)
                            curr['net_s'], curr['net_d'] = curr['net_d'], curr['net_s']
                        
                        # Try flipping next
                        if curr_d == next_d:
                            can_abut = True
                            next_node['swapped_sd'] = not next_node.get('swapped_sd', False)
                            next_node['net_s'], next_node['net_d'] = next_node['net_d'], next_node['net_s']
                        
                        # Try flipping both
                        if curr_s == next_d:
                            can_abut = True
                            curr['swapped_sd'] = not curr.get('swapped_sd', False)
                            curr['net_s'], curr['net_d'] = curr['net_d'], curr['net_s']
                            next_node['swapped_sd'] = not next_node.get('swapped_sd', False)
                            next_node['net_s'], next_node['net_d'] = next_node['net_d'], next_node['net_s']
                        
                        if can_abut:
                            curr.setdefault('abutment', {})['abut_right'] = True
                            next_node.setdefault('abutment', {})['abut_left'] = True
        
        # Set abutment flags for all adjacent active devices
        for i in range(len(active) - 1):
            curr = active[i]
            next_node = active[i + 1]
            
            curr_d = curr.get('net_d', '')
            curr_s = curr.get('net_s', '')
            next_s = next_node.get('net_s', '')
            next_d = next_node.get('net_d', '')
            
            # Check if they share a net
            if curr_d == next_s or curr_s == next_d:
                curr.setdefault('abutment', {})['abut_right'] = True
                next_node.setdefault('abutment', {})['abut_left'] = True
        
        # Rebuild the row with dummies at edges only
        new_row = []
        
        # Add left dummies
        left_dummies = [d for d in dummies if d['geometry']['x'] < min(n['geometry']['x'] for n in active)]
        new_row.extend(sorted(left_dummies, key=lambda n: n['geometry']['x']))
        
        # Add active devices
        new_row.extend(active)
        
        # Add right dummies
        right_dummies = [d for d in dummies if d['geometry']['x'] > max(n['geometry']['x'] + n['geometry'].get('width', 0.294) for n in active)]
        new_row.extend(sorted(right_dummies, key=lambda n: n['geometry']['x']))
        
        rows[key] = new_row
    
    # Rebuild all nodes
    all_nodes = []
    for row_nodes in rows.values():
        all_nodes.extend(row_nodes)
    
    print(f"\nFinal layout: {len(all_nodes)} nodes")
    
    # Analyze results
    total_abutments = 0
    total_gaps = 0
    
    for key in sorted(rows.keys()):
        y, typ = key
        row = rows[key]
        
        for i in range(len(row) - 1):
            curr = row[i]
            next_node = row[i + 1]
            curr_right = curr.get('abutment', {}).get('abut_right', False)
            next_left = next_node.get('abutment', {}).get('abut_left', False)
            
            if curr_right and next_left:
                total_abutments += 1
            else:
                total_gaps += 1
    
    print(f"\n=== Summary ===")
    print(f"Total abutments: {total_abutments}")
    print(f"Total gaps: {total_gaps}")
    print(f"Abutment rate: {total_abutments/(total_abutments+total_gaps)*100:.1f}%")
    
    # Save the result
    data['nodes'] = all_nodes
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    
    print(f"\nSaved to {output_file}")

if __name__ == '__main__':
    input_file = 'examples/comparator/comparator_initial_placement.json'
    output_file = 'scratch/comparator_final_fixed.json'
    
    fix_comparator_placement(input_file, output_file)

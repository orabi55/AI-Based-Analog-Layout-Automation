"""
Test abutment optimization for comparator-style placements.
Standalone test that doesn't require GUI imports.
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_agent.placement.abutment import optimize_all_rows_orientations
from ai_agent.placement.finger_grouper import get_block_boundary_nets


def test_boundary_nets_consistency():
    """Test that boundary net calculation is consistent between modules."""
    # Create a simple transistor with known S/D nets
    node = {
        "id": "MM0_m1",
        "type": "pmos",
        "geometry": {"x": 0.0, "y": 0.0, "orientation": "R0"},
        "net_s": "VDD",
        "net_d": "VOUTP"
    }
    
    # Test unflipped (R0)
    left, right = get_block_boundary_nets([node], is_flipped=False)
    print(f"Unflipped: left={left}, right={right}")
    # For odd nf (default 1), unflipped should be: left=S, right=D
    assert left == "VDD", f"Expected left=VDD, got {left}"
    assert right == "VOUTP", f"Expected right=VOUTP, got {right}"
    
    # Test flipped (MY)
    left, right = get_block_boundary_nets([node], is_flipped=True)
    print(f"Flipped: left={left}, right={right}")
    # For odd nf, flipped should be: left=D, right=S
    assert left == "VOUTP", f"Expected left=VOUTP, got {left}"
    assert right == "VDD", f"Expected right=VDD, got {right}"
    
    print("[OK] Boundary nets consistency test passed")


def test_comparator_abutment_optimization():
    """Test abutment optimization for a comparator-like row."""
    # Simulate a comparator diff-pair row: MM0, MM1, MM2, MM3
    # Typical nets: MM0/1 share VOUTP, MM2/3 share VOUTN
    # Goal: maximize abutment by matching boundary nets
    
    nodes = [
        # MM0: S=VDD, D=VOUTP
        {"id": "MM0_m1", "type": "pmos", "geometry": {"x": 0.0, "y": 1.636, "orientation": "R0"},
         "net_s": "VDD", "net_d": "VOUTP"},
        # MM1: S=VDD, D=VOUTN
        {"id": "MM1_m1", "type": "pmos", "geometry": {"x": 0.294, "y": 1.636, "orientation": "R0"},
         "net_s": "VDD", "net_d": "VOUTN"},
        # MM2: S=VDD, D=VOUTN
        {"id": "MM2_m1", "type": "pmos", "geometry": {"x": 0.588, "y": 1.636, "orientation": "R0"},
         "net_s": "VDD", "net_d": "VOUTN"},
        # MM3: S=VDD, D=VOUTP
        {"id": "MM3_m1", "type": "pmos", "geometry": {"x": 0.882, "y": 1.636, "orientation": "R0"},
         "net_s": "VDD", "net_d": "VOUTP"},
    ]
    
    terminal_nets = {
        "MM0": {"S": "VDD", "D": "VOUTP", "G": "VINP"},
        "MM1": {"S": "VDD", "D": "VOUTN", "G": "VINN"},
        "MM2": {"S": "VDD", "D": "VOUTN", "G": "VINN"},
        "MM3": {"S": "VDD", "D": "VOUTP", "G": "VINP"},
    }
    
    print("\nBefore optimization:")
    for node in nodes:
        left, right = get_block_boundary_nets([node], is_flipped=False)
        print(f"  {node['id']}: left={left}, right={right}")
    
    # Run optimization
    optimized = optimize_all_rows_orientations(nodes, terminal_nets)
    
    print("\nAfter optimization:")
    abut_count = 0
    for i, node in enumerate(optimized):
        # After optimization, nets are already swapped in the node, so use is_flipped=False
        left, right = get_block_boundary_nets([node], is_flipped=False)
        print(f"  {node['id']}: left={left}, right={right}, swapped={node.get('swapped_sd', False)}")
        
        # Check if this node can abut with the next
        if i < len(optimized) - 1:
            next_node = optimized[i + 1]
            next_left, _ = get_block_boundary_nets([next_node], is_flipped=False)
            if right == next_left:
                abut_count += 1
                print(f"    [OK] Can abut with {next_node['id']} (net: {right})")
    
    print(f"\n[OK] Found {abut_count} abutment opportunities (max possible: {len(nodes) - 1})")
    
    # For this symmetric diff-pair, we should get at least 2 abutments
    # (MM1-MM2 share VOUTN, and potentially MM0-MM1 or MM2-MM3)
    assert abut_count >= 2, f"Expected at least 2 abutments, got {abut_count}"
    
    return optimized


def test_current_mirror_abutment():
    """Test abutment for a current mirror configuration."""
    # Current mirror: MM0 and MM1 should share VOUT net
    nodes = [
        # MM0: S=VDD, D=VOUT
        {"id": "MM0_m1", "type": "pmos", "geometry": {"x": 0.0, "y": 0.0, "orientation": "R0"},
         "net_s": "VDD", "net_d": "VOUT"},
        # MM1: S=VDD, D=VOUT
        {"id": "MM1_m1", "type": "pmos", "geometry": {"x": 0.294, "y": 0.0, "orientation": "R0"},
         "net_s": "VDD", "net_d": "VOUT"},
    ]
    
    terminal_nets = {
        "MM0": {"S": "VDD", "D": "VOUT", "G": "VBIAS"},
        "MM1": {"S": "VDD", "D": "VOUT", "G": "VBIAS"},
    }
    
    print("\nCurrent mirror test:")
    print("Before optimization:")
    for node in nodes:
        left, right = get_block_boundary_nets([node], is_flipped=False)
        print(f"  {node['id']}: left={left}, right={right}")
    
    optimized = optimize_all_rows_orientations(nodes, terminal_nets)
    
    print("After optimization:")
    for node in optimized:
        # After optimization, nets are already swapped, so use is_flipped=False
        left, right = get_block_boundary_nets([node], is_flipped=False)
        print(f"  {node['id']}: left={left}, right={right}, swapped={node.get('swapped_sd', False)}")
    
    # Check if MM0 and MM1 can abut
    left0, right0 = get_block_boundary_nets([optimized[0]], is_flipped=False)
    left1, right1 = get_block_boundary_nets([optimized[1]], is_flipped=False)
    
    can_abut = (right0 == left1)
    print(f"\nMM0 right={right0}, MM1 left={left1}")
    print(f"Can abut: {can_abut}")
    
    assert can_abut, "Current mirror devices should be able to abut"
    print("[OK] Current mirror abutment test passed")


if __name__ == "__main__":
    print("=" * 60)
    print("Testing Abutment Optimization Fixes")
    print("=" * 60)
    
    test_boundary_nets_consistency()
    test_comparator_abutment_optimization()
    test_current_mirror_abutment()
    
    print("\n" + "=" * 60)
    print("All tests passed!")
    print("=" * 60)

import unittest
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from symbolic_editor.layout_tab import _adjacent_devices_can_abut

class TestAbutmentOptimizer(unittest.TestCase):

    def test_adjacent_can_abut(self):
        # 1. Sibling fingers of the same transistor
        prev_sibling = {
            "id": "MM2_f0",
            "type": "pmos",
            "geometry": {"x": 0.0, "y": 0.0, "orientation": "R0"},
            "net_s": "VDD",
            "net_d": "VOUTP"
        }
        curr_sibling = {
            "id": "MM2_f1",
            "type": "pmos",
            "geometry": {"x": 0.294, "y": 0.0, "orientation": "MY"},
            "net_s": "VDD",
            "net_d": "VOUTP"
        }
        self.assertTrue(_adjacent_devices_can_abut(prev_sibling, curr_sibling, {}))

        # 2. Both dummies
        prev_dummy = {
            "id": "DUMMY1",
            "is_dummy": True,
            "geometry": {"x": 0.0, "y": 0.0, "orientation": "R0"}
        }
        curr_dummy = {
            "id": "DUMMY2",
            "is_dummy": True,
            "geometry": {"x": 0.294, "y": 0.0, "orientation": "R0"}
        }
        self.assertTrue(_adjacent_devices_can_abut(prev_dummy, curr_dummy, {}))

        # 3. Active devices with mismatched boundary nets
        # MM2 (R0): left = D (VOUTP), right = S (VDD)
        # MM1 (R0): left = D (VOUTN), right = S (VDD)
        # Boundary touching: MM2 right (VDD) vs MM1 left (VOUTN) -> Mismatch!
        mm2_node = {
            "id": "MM2",
            "type": "pmos",
            "geometry": {"x": 0.0, "y": 0.0, "orientation": "R0"},
            "net_s": "VDD",
            "net_d": "VOUTP"
        }
        mm1_node = {
            "id": "MM1",
            "type": "pmos",
            "geometry": {"x": 0.294, "y": 0.0, "orientation": "R0"},
            "net_s": "VDD",
            "net_d": "VOUTN"
        }
        self.assertFalse(_adjacent_devices_can_abut(mm2_node, mm1_node, {}))

        # 4. Active devices with matched boundary nets (after S/D swap / mirroring)
        # MM2 flipped (MY): left = D (VOUTP), right = S (VDD)
        # MM1 (R0): left = S (VDD), right = D (VOUTN)
        # Boundary touching: MM2 right (VDD) vs MM1 left (VDD) -> Match!
        mm2_flipped = {
            "id": "MM2",
            "type": "pmos",
            "geometry": {"x": 0.0, "y": 0.0, "orientation": "MY"},
            "net_s": "VDD",
            "net_d": "VOUTP",
            "swapped_sd": False
        }
        mm1_node_matched = {
            "id": "MM1",
            "type": "pmos",
            "geometry": {"x": 0.294, "y": 0.0, "orientation": "R0"},
            "net_s": "VDD",
            "net_d": "VOUTN"
        }
        self.assertTrue(_adjacent_devices_can_abut(mm2_flipped, mm1_node_matched, {}))

    def test_optimize_all_rows_orientations(self):
        from ai_agent.placement.abutment import optimize_all_rows_orientations
        
        nodes = [
            {"id": "MM3_f0", "type": "pmos", "geometry": {"x": 0.0, "y": 0.668, "orientation": "R0"}},
            {"id": "MM3_f1", "type": "pmos", "geometry": {"x": 0.07, "y": 0.668, "orientation": "R0"}},
            {"id": "MM4_f0", "type": "pmos", "geometry": {"x": 0.294, "y": 0.668, "orientation": "R0"}},
            {"id": "MM4_f1", "type": "pmos", "geometry": {"x": 0.364, "y": 0.668, "orientation": "R0"}},
            {"id": "DUMMY1", "is_dummy": True, "type": "pmos", "geometry": {"x": -0.07, "y": 0.668, "orientation": "R0"}}
        ]
        
        terminal_nets = {
            "MM3": {"G": "VINP", "D": "VOUTP", "S": "VDD"},
            "MM4": {"G": "VINN", "D": "VOUTN", "S": "VDD"}
        }
        
        optimized = optimize_all_rows_orientations(nodes, terminal_nets)
        self.assertIsNotNone(optimized)
        self.assertEqual(len(optimized), 5)

    def test_sd_swap_optimization(self):
        from ai_agent.placement.abutment import optimize_all_rows_orientations
        
        # Scenario: Two transistors of different sizes (1 finger vs 2 fingers)
        # placed next to each other. Since they are different sizes, they are not paired.
        # MM1: S=S1, D=SHARED. Since nf=1, right pin is D (SHARED).
        # MM2: S=SHARED, D=D2. Since nf=2, left pin of finger 0 is S (SHARED).
        # They can share diffusion at the boundary (SHARED) in their default orientation (flip=0).
        # Flipping them would not improve abutment, so they should remain unflipped (flip=0).
        nodes = [
            {"id": "MM1_f0", "type": "pmos", "geometry": {"x": 0.0, "y": 0.0, "orientation": "R0"}},
            {"id": "MM2_f0", "type": "pmos", "geometry": {"x": 0.294, "y": 0.0, "orientation": "R0"}},
            {"id": "MM2_f1", "type": "pmos", "geometry": {"x": 0.364, "y": 0.0, "orientation": "R0"}}
        ]
        terminal_nets = {
            "MM1": {"G": "G1", "S": "S1", "D": "SHARED"},
            "MM2": {"G": "G2", "S": "SHARED", "D": "D2"}
        }
        optimized = optimize_all_rows_orientations(nodes, terminal_nets)
        self.assertFalse(optimized[0].get("swapped_sd", False))
        self.assertFalse(optimized[1].get("swapped_sd", False))
        self.assertTrue(optimized[2].get("swapped_sd", False))

        # Scenario: S/D tie-breaker test.
        # Two transistors MM3 and MM4 of different sizes that have no shared nets (cannot abut anyway).
        # The optimizer should choose NOT to flip either of them, keeping block_flip = 0.
        nodes_no_abut = [
            {"id": "MM3_f0", "type": "pmos", "geometry": {"x": 0.0, "y": 0.0, "orientation": "R0"}},
            {"id": "MM4_f0", "type": "pmos", "geometry": {"x": 0.294, "y": 0.0, "orientation": "R0"}},
            {"id": "MM4_f1", "type": "pmos", "geometry": {"x": 0.364, "y": 0.0, "orientation": "R0"}}
        ]
        terminal_nets_no_abut = {
            "MM3": {"G": "G3", "S": "S3", "D": "D3"},
            "MM4": {"G": "G4", "S": "S4", "D": "D4"}
        }
        optimized_no_abut = optimize_all_rows_orientations(nodes_no_abut, terminal_nets_no_abut)
        self.assertFalse(optimized_no_abut[0].get("swapped_sd", False), "Should not flip MM3 block when it does not help")
        self.assertFalse(optimized_no_abut[1].get("swapped_sd", False), "Should not flip MM4 block when it does not help")
        self.assertTrue(optimized_no_abut[2].get("swapped_sd", False), "Finger 1 of MM4 alternates phase")

    def test_device_item_swap_state(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from symbolic_editor.device_item import DeviceItem
        
        item = DeviceItem("MM0", "nmos", 0, 0, 10, 20, nf=1)
        self.assertFalse(item._is_swapped_sd)
        
        item.set_swapped_sd(True)
        self.assertTrue(item._is_swapped_sd)
        
        item.clear_net_labels()
        self.assertTrue(item._is_swapped_sd, "clear_net_labels should NOT reset _is_swapped_sd")

    def test_group_nodes_by_row(self):
        from ai_agent.placement.abutment import group_nodes_by_row
        
        # Test Case 1: Nodes with slightly different Y coordinates (within tolerance)
        nodes = [
            {"id": "A", "geometry": {"x": 0.0, "y": 0.668}},
            {"id": "B", "geometry": {"x": 0.294, "y": 0.6683}},
            {"id": "C", "geometry": {"x": 0.588, "y": 0.6678}}
        ]
        rows = group_nodes_by_row(nodes, tolerance=0.05)
        self.assertEqual(len(rows), 1)
        rep_y = list(rows.keys())[0]
        self.assertAlmostEqual(rep_y, 0.668, places=3)
        self.assertEqual(len(rows[rep_y]), 3)

        # Test Case 2: Nodes belonging to different rows (outside tolerance)
        nodes_diff = [
            {"id": "A", "geometry": {"x": 0.0, "y": 0.0}},
            {"id": "B", "geometry": {"x": 0.294, "y": 0.668}},
            {"id": "C", "geometry": {"x": 0.588, "y": 0.670}}
        ]
        rows_diff = group_nodes_by_row(nodes_diff, tolerance=0.05)
        self.assertEqual(len(rows_diff), 2)
        self.assertIn(0.0, rows_diff)
        self.assertIn(0.669, rows_diff)
        self.assertEqual(len(rows_diff[0.0]), 1)
        self.assertEqual(len(rows_diff[0.669]), 2)

if __name__ == "__main__":
    unittest.main()

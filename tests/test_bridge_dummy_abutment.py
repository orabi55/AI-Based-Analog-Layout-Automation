import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_agent.placement.finger_grouper import _resolve_row_overlaps, get_block_boundary_nets


class TestBridgeDummyAbutment(unittest.TestCase):
    def test_inserted_bridge_dummy_exposes_neighbor_boundary_nets(self):
        nodes = [
            {
                "id": "MLEFT",
                "type": "nmos",
                "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.668, "orientation": "R0"},
                "electrical": {"nf": 1, "nfin": 4, "l": 1.4e-8},
                "net_s": "LEFT_SRC",
                "net_d": "LEFT_OUT",
                "net_g": "G1",
            },
            {
                "id": "MRIGHT",
                "type": "nmos",
                "geometry": {"x": 0.294, "y": 0.0, "width": 0.294, "height": 0.668, "orientation": "R0"},
                "electrical": {"nf": 1, "nfin": 4, "l": 1.4e-8},
                "net_s": "RIGHT_IN",
                "net_d": "RIGHT_DRAIN",
                "net_g": "G2",
            },
        ]

        placed = _resolve_row_overlaps(
            copy.deepcopy(nodes),
            no_abutment=False,
            preserve_order=True,
            terminal_nets={},
        )
        placed = sorted(placed, key=lambda n: n["geometry"]["x"])

        self.assertEqual(len(placed), 3)
        left, bridge, right = placed
        self.assertTrue(bridge.get("is_dummy"), placed)
        self.assertTrue(str(bridge.get("id", "")).startswith("FILLER_DUMMY_BRIDGE"))

        # The bridge dummy must expose the left device's right boundary net on
        # its left boundary, and the right device's left boundary net on its
        # right boundary.  Otherwise the final validator clears abutment flags.
        self.assertEqual(get_block_boundary_nets([left], False)[1], get_block_boundary_nets([bridge], False)[0])
        self.assertEqual(get_block_boundary_nets([bridge], False)[1], get_block_boundary_nets([right], False)[0])

        self.assertTrue(left["abutment"]["abut_right"])
        self.assertTrue(bridge["abutment"]["abut_left"])
        self.assertTrue(bridge["abutment"]["abut_right"])
        self.assertTrue(right["abutment"]["abut_left"])


if __name__ == "__main__":
    unittest.main()

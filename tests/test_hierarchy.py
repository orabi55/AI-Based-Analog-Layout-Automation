"""
tests/test_hierarchy.py
=======================
Unit tests for device hierarchy detection, modeling, and expansion.
"""

import pytest
import sys
import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from parser.hierarchy import (
    parse_array_suffix,
    parse_net_array_suffix,
    _extract_int_param,
    HierarchyNode,
    DeviceHierarchy,
    build_hierarchy_for_device,
    expand_hierarchy_devices,
    build_device_hierarchy,
    _device_sort_key,
)
from parser.netlist_reader import Device, parse_mos, parse_value


# ===================================================================
# Tests for parse_array_suffix()
# ===================================================================

class TestParseArraySuffix:
    def test_simple_array(self):
        assert parse_array_suffix("MM9<7>") == ("MM9", 7)

    def test_large_array(self):
        assert parse_array_suffix("XI0_MM3<42>") == ("XI0_MM3", 42)

    def test_no_array(self):
        assert parse_array_suffix("MM10") == ("MM10", None)

    def test_malformed_missing_close_bracket(self):
        assert parse_array_suffix("MM9<7") == ("MM9<7", None)

    def test_malformed_empty_brackets(self):
        assert parse_array_suffix("MM9<>") == ("MM9<>", None)


# ===================================================================
# Tests for parse_net_array_suffix()
# ===================================================================

class TestParseNetArraySuffix:
    def test_indexed_net(self):
        assert parse_net_array_suffix("net2<3>") == ("net2", 3)

    def test_plain_net(self):
        assert parse_net_array_suffix("VDD") == ("VDD", None)


# ===================================================================
# Tests for _extract_int_param()
# ===================================================================

class TestExtractIntParam:
    def test_integer_value(self):
        assert _extract_int_param({"m": 8}, "m") == 8

    def test_float_value(self):
        assert _extract_int_param({"nf": 2.0}, "nf") == 2

    def test_missing_key(self):
        assert _extract_int_param({}, "m") == 1

    def test_negative_value_clamped(self):
        assert _extract_int_param({"nf": -3}, "nf") == 1


# ===================================================================
# Tests for HierarchyNode
# ===================================================================

class TestHierarchyNode:
    def test_leaf_node(self):
        node = HierarchyNode(name="MM5_m1", level=1)
        assert node.is_leaf()
        assert node.leaf_count() == 1

    def test_parent_with_children(self):
        child1 = HierarchyNode(name="MM6_m1", level=1)
        child2 = HierarchyNode(name="MM6_m2", level=1)
        parent = HierarchyNode(name="MM6", level=0, children=[child1, child2])
        assert not parent.is_leaf()
        assert parent.leaf_count() == 2

    def test_two_level_hierarchy(self):
        leaf1 = HierarchyNode(name="MM6_m1_f1", level=2)
        leaf2 = HierarchyNode(name="MM6_m1_f2", level=2)
        mid = HierarchyNode(name="MM6_m1", level=1, children=[leaf1, leaf2])
        root = HierarchyNode(name="MM6", level=0, children=[mid])
        assert root.leaf_count() == 2
        assert root.all_leaves() == [leaf1, leaf2]


# ===================================================================
# Tests for build_hierarchy_for_device()
# ===================================================================

class TestBuildHierarchy:
    def test_single_device(self):
        h = build_hierarchy_for_device(
            name="MM1",
            pins={"D": "VDD", "G": "CLK", "S": "VSS", "B": "VSS"},
            params={"m": 1, "nf": 1},
            dtype="nmos",
        )
        assert h.needs_expansion() is False
        assert h.total_leaves == 1

    def test_finger_only(self):
        h = build_hierarchy_for_device(
            name="MM10",
            pins={"D": "VOUT", "G": "VIN", "S": "VSS", "B": "VSS"},
            params={"m": 1, "nf": 10},
            dtype="pmos",
        )
        assert h.needs_expansion() is True
        assert h.multiplier == 1
        assert h.fingers == 10
        assert h.total_leaves == 10
        assert len(h.root.children) == 10
        assert h.root.children[0].name == "MM10_f1"
        assert h.root.children[0].finger_index == 1

    def test_multiplier_only(self):
        h = build_hierarchy_for_device(
            name="MM3",
            pins={"D": "VOUT", "G": "IB", "S": "VSS", "B": "VSS"},
            params={"m": 8, "nf": 1},
            dtype="pmos",
        )
        assert h.multiplier == 8
        assert h.total_leaves == 8
        assert len(h.root.children) == 8
        assert h.root.children[0].name == "MM3_m1"
        assert h.root.children[0].multiplier_index == 1
        assert h.root.children[7].name == "MM3_m8"

    def test_multiplier_and_finger(self):
        h = build_hierarchy_for_device(
            name="MM6",
            pins={"D": "VOUT", "G": "net24", "S": "VDD", "B": "VDD"},
            params={"m": 3, "nf": 5},
            dtype="pmos",
        )
        assert h.multiplier == 3
        assert h.fingers == 5
        assert h.total_leaves == 15
        assert len(h.root.children) == 3
        assert h.root.children[0].name == "MM6_m1"
        assert len(h.root.children[0].children) == 5
        assert h.root.children[0].children[0].name == "MM6_m1_f1"
        assert h.root.children[2].children[4].name == "MM6_m3_f5"

    def test_array(self):
        """Array of 8 copies, each nf=1 → 8 m-level children."""
        h = build_hierarchy_for_device(
            name="MM9",
            pins={"D": "VY", "G": "VINN", "S": "net2", "B": "GND"},
            params={"m": 1, "nf": 1, "array_count": 8, "is_array": True},
            dtype="nmos",
        )
        assert h.multiplier == 8
        assert h.is_array is True
        assert h.total_leaves == 8
        assert h.root.children[0].name == "MM9_m1"
        assert h.root.children[7].name == "MM9_m8"


# ===================================================================
# Tests for parse_mos() integration
# ===================================================================

class TestParseMosIntegration:
    def test_single_device(self):
        tokens = "MM1 VOUT VIN VSS VSS n08 l=28n nf=1 m=1 nfin=2".split()
        result = parse_mos(tokens)
        assert len(result) == 1
        dev = result[0]
        assert dev.name == "MM1"
        assert dev.params["m"] == 1
        assert dev.params["nf"] == 1
        assert dev.params["parent"] == "MM1"

    def test_multiplier_8(self):
        """m=8 → 8 devices named MM3_m1 .. MM3_m8"""
        tokens = "MM3 VOUT IB VSS VSS p08 l=0.1u nf=1 m=8 nfin=2".split()
        result = parse_mos(tokens)
        assert len(result) == 8
        assert result[0].name == "MM3_m1"
        assert result[0].params["multiplier_index"] == 1
        assert result[0].params["parent"] == "MM3"
        assert result[7].name == "MM3_m8"
        assert result[7].params["multiplier_index"] == 8

    def test_finger_10(self):
        """nf=10 → 10 devices named MM10_f1 .. MM10_f10"""
        tokens = "MM10 net24 net38 VDD VDD p08 l=0.014u nf=10 m=1 nfin=2".split()
        result = parse_mos(tokens)
        assert len(result) == 10
        assert result[0].name == "MM10_f1"
        assert result[0].params["finger_index"] == 1
        assert result[0].params["parent"] == "MM10"
        assert result[9].name == "MM10_f10"

    def test_mixed_m3_nf5(self):
        """m=3, nf=5 → 15 devices: MM6_m1_f1 .. MM6_m3_f5"""
        tokens = "MM6 VOUT net24 VDD VDD p08 l=0.014u nf=5 m=3 nfin=2".split()
        result = parse_mos(tokens)
        assert len(result) == 15
        assert result[0].name == "MM6_m1_f1"
        assert result[0].params["multiplier_index"] == 1
        assert result[0].params["finger_index"] == 1
        assert result[4].name == "MM6_m1_f5"
        assert result[5].name == "MM6_m2_f1"
        assert result[14].name == "MM6_m3_f5"

    def test_array_indexed(self):
        """MM9<7> → named MM9_m8 (array index 7 → 1-based: 8)"""
        tokens = "MM9<7> VY VINN net2<3> GND n08 l=28n nf=1 nfin=7".split()
        result = parse_mos(tokens)
        assert len(result) == 1
        dev = result[0]
        assert dev.name == "MM9_m8"
        assert dev.params["array_index"] == 7
        assert dev.params["multiplier_index"] == 8
        assert dev.params["parent"] == "MM9"

    def test_array_with_fingers(self):
        """MM9<3> with nf=5 → MM9_m4_f1 .. MM9_m4_f5"""
        tokens = "MM9<3> VY VINN net2<0> GND n08 l=28n nf=5 nfin=7".split()
        result = parse_mos(tokens)
        assert len(result) == 5
        assert result[0].name == "MM9_m4_f1"
        assert result[0].params["array_index"] == 3
        assert result[0].params["multiplier_index"] == 4
        assert result[0].params["finger_index"] == 1
        assert result[4].name == "MM9_m4_f5"

    def test_no_params_defaults(self):
        tokens = "MM1 VOUT VIN VSS VSS n08 l=28n".split()
        result = parse_mos(tokens)
        assert len(result) == 1
        dev = result[0]
        assert dev.params["m"] == 1
        assert dev.params["nf"] == 1


# ===================================================================
# Tests for _parse_id() in finger_grouper
# ===================================================================

class TestParseIdIntegration:
    def test_new_multi_finger(self):
        from ai_agent.ai_initial_placement.finger_grouper import _parse_id
        assert _parse_id("MM6_m2_f3") == ("MM6", 2, 3)

    def test_new_multi_only(self):
        from ai_agent.ai_initial_placement.finger_grouper import _parse_id
        assert _parse_id("MM9_m8") == ("MM9", 8, None)
        assert _parse_id("MM3_m1") == ("MM3", 1, None)

    def test_legacy_finger_only(self):
        from ai_agent.ai_initial_placement.finger_grouper import _parse_id
        assert _parse_id("MM5_f2") == ("MM5", None, 2)

    def test_single_device(self):
        from ai_agent.ai_initial_placement.finger_grouper import _parse_id
        assert _parse_id("MM1") == ("MM1", None, None)

    def test_legacy_array_bus(self):
        from ai_agent.ai_initial_placement.finger_grouper import _parse_id
        assert _parse_id("MM9<3>_f4") == ("MM9", 3, 4)
        assert _parse_id("MM9<3>") == ("MM9", 3, None)

    def test_transistor_key(self):
        from ai_agent.ai_initial_placement.finger_grouper import _transistor_key
        assert _transistor_key("MM6_m2_f3") == "MM6"
        assert _transistor_key("MM9_m8") == "MM9"
        assert _transistor_key("MM5_f2") == "MM5"
        assert _transistor_key("MM1") == "MM1"


# ===================================================================
# Tests for _device_sort_key
# ===================================================================

class TestDeviceSortKey:
    def test_multi_finger(self):
        assert _device_sort_key("MM6_m2_f3") == (2, 3)

    def test_multi_only(self):
        assert _device_sort_key("MM9_m8") == (8, 0)

    def test_finger_only(self):
        assert _device_sort_key("MM10_f5") == (0, 5)

    def test_single(self):
        assert _device_sort_key("MM1") == (0, 0)


# ===================================================================
# Tests for Device hierarchy properties
# ===================================================================

class TestDeviceHierarchyProperties:
    def test_device_multiplier_property(self):
        dev = Device("MM3_m1", "pmos", {"D": "VDD"}, {"m": 8})
        assert dev.multiplier == 8

    def test_device_fingers_property(self):
        dev = Device("MM10_f1", "pmos", {"D": "VDD"}, {"nf": 1})
        assert dev.fingers == 1

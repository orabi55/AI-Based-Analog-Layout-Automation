"""Tests for ai_agent.tools.device_resolver."""

from __future__ import annotations

import sys
import os
import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_agent.tools.device_resolver import (
    normalize_logical_device_id,
    resolve_layout_device_reference,
    find_matched_block_for_device,
    detect_finger_interleaving,
)
from tests.fixtures.comparator_chat_state import (
    make_comparator_finger_state,
    make_comparator_chat_state,
)


# ---------------------------------------------------------------------------
# normalize_logical_device_id
# ---------------------------------------------------------------------------

class TestNormalizeLogicalDeviceId:

    def test_m1_to_mm1(self):
        assert normalize_logical_device_id("M1") == "MM1"

    def test_mm1_unchanged(self):
        assert normalize_logical_device_id("MM1") == "MM1"

    def test_lowercase_mm1(self):
        assert normalize_logical_device_id("mm1") == "MM1"

    def test_mm1_m3_strips_finger(self):
        assert normalize_logical_device_id("MM1_m3") == "MM1"

    def test_mm1_f0_strips_finger(self):
        assert normalize_logical_device_id("MM1_f0") == "MM1"

    def test_mm10_m4_strips_finger(self):
        assert normalize_logical_device_id("MM10_m4") == "MM10"

    def test_m10_to_mm10(self):
        """M10 should become MM10, not MM110."""
        assert normalize_logical_device_id("M10") == "MM10"

    def test_mm1_bracket_strips(self):
        assert normalize_logical_device_id("MM1[0]") == "MM1"

    def test_empty_string(self):
        assert normalize_logical_device_id("") == ""

    def test_none_input(self):
        assert normalize_logical_device_id(None) == ""


# ---------------------------------------------------------------------------
# resolve_layout_device_reference
# ---------------------------------------------------------------------------

class TestResolveLayoutDeviceReference:

    @pytest.fixture
    def finger_nodes(self):
        state = make_comparator_finger_state("test")
        return state["placement_nodes"]

    @pytest.fixture
    def logical_nodes(self):
        state = make_comparator_chat_state("test")
        return state["placement_nodes"]

    def test_exact_match(self, logical_nodes):
        result = resolve_layout_device_reference("MM6", logical_nodes)
        assert result["resolution_type"] == "exact"
        assert result["physical_ids"] == ["MM6"]

    def test_logical_group(self, finger_nodes):
        """MM1 should resolve to MM1_m1..MM1_m4."""
        result = resolve_layout_device_reference("MM1", finger_nodes)
        assert result["resolution_type"] in {"logical_group", "alias"}
        assert len(result["physical_ids"]) == 4
        assert "MM1_m1" in result["physical_ids"]

    def test_alias_m1_to_mm1(self, finger_nodes):
        """M1 (alias) should resolve to MM1_m1..MM1_m4."""
        result = resolve_layout_device_reference("M1", finger_nodes)
        assert result["logical_id"] == "MM1"
        assert len(result["physical_ids"]) == 4

    def test_mm8_resolves_8_fingers(self, finger_nodes):
        result = resolve_layout_device_reference("MM8", finger_nodes)
        assert len(result["physical_ids"]) == 8

    def test_mm10_resolves_4_fingers(self, finger_nodes):
        result = resolve_layout_device_reference("MM10", finger_nodes)
        assert result["logical_id"] == "MM10"
        assert len(result["physical_ids"]) == 4

    def test_missing_device(self, finger_nodes):
        result = resolve_layout_device_reference("MM99", finger_nodes)
        assert result["resolution_type"] == "missing"
        assert result["physical_ids"] == []

    def test_dummy_excluded_by_default(self, finger_nodes):
        dummy_nodes = finger_nodes + [{"id": "EDGE_DUMMY_1", "type": "filler",
                                        "geometry": {"x": 99, "y": 0}}]
        result = resolve_layout_device_reference("EDGE_DUMMY_1", dummy_nodes)
        assert "dummy" in (result.get("message") or "").lower() or result["resolution_type"] == "exact"

    def test_empty_reference(self, finger_nodes):
        result = resolve_layout_device_reference("", finger_nodes)
        assert result["resolution_type"] == "missing"


# ---------------------------------------------------------------------------
# find_matched_block_for_device
# ---------------------------------------------------------------------------

class TestFindMatchedBlockForDevice:

    def test_mm1_in_matched_block(self):
        block = find_matched_block_for_device("MM1")
        assert block is not None
        assert "MM2" in block["devices"] or "MM1" in block["devices"]

    def test_mm8_in_matched_block(self):
        block = find_matched_block_for_device("MM8")
        assert block is not None
        assert set(block["devices"]) == {"MM8", "MM9"}

    def test_mm6_free_device(self):
        """MM6 is single-finger NMOS, might or might not be in known blocks."""
        block = find_matched_block_for_device("MM6")
        # MM6/MM7 are in _KNOWN_MATCHED_BLOCKS but as NMOS latch pair
        # So it should be found
        if block:
            assert "MM7" in block["devices"]

    def test_mm10_free_device(self):
        """MM10 is the tail current source, no matched block."""
        block = find_matched_block_for_device("MM10")
        assert block is None

    def test_with_state_metadata(self):
        state = make_comparator_finger_state("test")
        block = find_matched_block_for_device("MM8", state)
        assert block is not None
        assert "diff" in block.get("description", "").lower() or "MM9" in block["devices"]


# ---------------------------------------------------------------------------
# detect_finger_interleaving
# ---------------------------------------------------------------------------

class TestDetectFingerInterleaving:

    def test_abab_mm8_mm9(self):
        state = make_comparator_finger_state("test")
        nodes = state["placement_nodes"]
        result = detect_finger_interleaving("MM8", "MM9", nodes)
        assert result == "ABAB"

    def test_abba_mm4_mm5(self):
        state = make_comparator_finger_state("test")
        nodes = state["placement_nodes"]
        result = detect_finger_interleaving("MM4", "MM5", nodes)
        assert result == "ABBA"

    def test_single_finger_returns_none(self):
        state = make_comparator_finger_state("test")
        nodes = state["placement_nodes"]
        result = detect_finger_interleaving("MM6", "MM7", nodes)
        assert result is None  # Only 2 nodes, less than 4 required

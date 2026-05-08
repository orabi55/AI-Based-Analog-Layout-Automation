"""
test_slot_filling.py
====================
Tests for:
  - Device extraction with logical IDs (MM1 resolving through finger-expanded nodes)
  - Partial intent building (_build_partial_move_intent)
  - Slot-filling (try_fill_edit_slots)
  - Two-turn clarification roundtrip via run_session_chat_agent
"""

import pytest

from ai_agent.agents.session_chat_agent import (
    DEVICE_RE,
    _extract_devices,
    _build_partial_move_intent,
    try_fill_edit_slots,
    parse_direct_edit_command,
    run_session_chat_agent,
)


# ══════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════

def _finger_nodes(base="MM1", count=4, device_type="nmos"):
    """Create finger-expanded placement nodes for a logical device."""
    return [
        {
            "id": f"{base}_f{i}",
            "parent_id": base,
            "type": device_type,
            "geometry": {"x": i * 0.5, "y": 0.0, "width": 0.294, "height": 0.668},
        }
        for i in range(count)
    ]


def _mixed_nodes():
    """Create a mix of finger-expanded and regular nodes."""
    return [
        *_finger_nodes("MM1", 4, "nmos"),
        *_finger_nodes("MM0", 4, "nmos"),
        {"id": "M3", "type": "pmos", "geometry": {"x": 5.0, "y": 2.0, "width": 0.294}},
    ]


# ══════════════════════════════════════════════════════════════════
# Device regex coverage
# ══════════════════════════════════════════════════════════════════


class TestDeviceRegex:
    """DEVICE_RE should match all common analog device name patterns."""

    @pytest.mark.parametrize("name", [
        "M1", "M10", "MM1", "MM10", "MN1", "MP1", "MP2",
        "XM1", "XM10",
    ])
    def test_regex_matches_common_names(self, name):
        assert DEVICE_RE.search(name), f"DEVICE_RE should match {name}"

    def test_regex_preserves_case(self):
        assert DEVICE_RE.findall("Move mm1 left") == ["mm1"]
        assert DEVICE_RE.findall("Move MM1 left") == ["MM1"]


# ══════════════════════════════════════════════════════════════════
# Device extraction with logical IDs
# ══════════════════════════════════════════════════════════════════


class TestExtractDevicesLogical:
    """_extract_devices should match logical device names via parent_id
    and logical_base_device_id even when placement only has fingers."""

    def test_mm1_matches_via_parent_id(self):
        nodes = _finger_nodes("MM1", 4)
        result = _extract_devices("Move MM1 left", nodes)
        assert "MM1" in result

    def test_mm0_matches_via_parent_id(self):
        nodes = _finger_nodes("MM0", 4)
        result = _extract_devices("swap MM0 and MM1", nodes)
        assert "MM0" in result

    def test_mm1_matches_via_logical_base(self):
        # Nodes WITHOUT explicit parent_id — must match via logical_base_device_id
        nodes = [{"id": "MM1_f0", "type": "nmos"}, {"id": "MM1_f1", "type": "nmos"}]
        result = _extract_devices("flip MM1", nodes)
        assert "MM1" in result

    def test_exact_id_still_works(self):
        nodes = [{"id": "M3", "type": "pmos"}]
        result = _extract_devices("move M3 left", nodes)
        assert result == ["M3"]

    def test_both_logical_and_exact_match(self):
        nodes = _mixed_nodes()
        result = _extract_devices("swap MM1 and M3", nodes)
        assert "MM1" in result
        assert "M3" in result

    def test_no_match_when_device_not_in_nodes(self):
        nodes = [{"id": "M99", "type": "nmos"}]
        result = _extract_devices("move MM1 left", nodes)
        assert result == []  # MM1 not present in any form


# ══════════════════════════════════════════════════════════════════
# Parse command with finger-expanded nodes
# ══════════════════════════════════════════════════════════════════


class TestParseWithFingerNodes:
    """parse_direct_edit_command should succeed for logical device names
    when placement has finger-expanded nodes."""

    def test_move_mm1_left(self):
        # MM1 is in a matched block; raw parsing without placement_nodes
        # still produces a direct move command.
        cmds = parse_direct_edit_command("Move MM1 to the left")
        assert cmds
        assert cmds[0]["action"] == "move"
        assert cmds[0]["device_id"] == "MM1"
        assert cmds[0]["dx"] == -1

    def test_flip_mm1(self):
        nodes = _finger_nodes("MM1", 2)
        cmds = parse_direct_edit_command("flip MM1", nodes)
        assert cmds
        assert cmds[0]["action"] == "flip"
        assert cmds[0]["device_id"] == "MM1"

    def test_delete_mm1(self):
        nodes = _finger_nodes("MM1", 2)
        cmds = parse_direct_edit_command("delete MM1", nodes)
        assert cmds
        assert cmds[0]["action"] == "delete"
        assert cmds[0]["device_id"] == "MM1"

    def test_swap_mm1_mm0(self):
        nodes = [*_finger_nodes("MM1", 2), *_finger_nodes("MM0", 2)]
        cmds = parse_direct_edit_command("swap MM1 and MM0", nodes)
        assert cmds
        assert cmds[0]["action"] == "swap"
        assert {cmds[0].get("device_a"), cmds[0].get("device_b")} == {"MM1", "MM0"}


# ══════════════════════════════════════════════════════════════════
# Partial intent builder
# ══════════════════════════════════════════════════════════════════


class TestBuildPartialMoveIntent:
    def test_move_left(self):
        result = _build_partial_move_intent("move left")
        assert result is not None
        assert result["action"] == "move"
        assert result["dx"] == -1
        assert result["dy"] == 0
        assert "device_id" in result["missing"]

    def test_move_right_by_3(self):
        result = _build_partial_move_intent("move right 3")
        assert result is not None
        assert result["dx"] == 3
        assert result["dy"] == 0

    def test_shift_up(self):
        result = _build_partial_move_intent("shift up")
        assert result is not None
        assert result["action"] == "move"
        assert result["dy"] == 1

    def test_flip_alone(self):
        result = _build_partial_move_intent("flip")
        assert result is not None
        assert result["action"] == "flip"
        assert "device_id" in result["missing"]

    def test_flip_vertical(self):
        result = _build_partial_move_intent("flip vertical")
        assert result is not None
        assert result["orientation"] == "vertical"

    def test_delete_alone(self):
        result = _build_partial_move_intent("delete")
        assert result is not None
        assert result["action"] == "delete"
        assert "device_id" in result["missing"]

    def test_move_without_direction_returns_none(self):
        """move with no direction is truly ambiguous — no partial intent."""
        result = _build_partial_move_intent("move")
        assert result is None

    def test_random_text_returns_none(self):
        result = _build_partial_move_intent("what is the strategy?")
        assert result is None


# ══════════════════════════════════════════════════════════════════
# Slot-filling
# ══════════════════════════════════════════════════════════════════


class TestFillEditSlots:
    def test_fill_device_from_plain_name(self):
        pending = {"action": "move", "dx": -1, "dy": 0, "missing": ["device_id"]}
        result = try_fill_edit_slots("MM1", pending)
        assert result is not None
        assert result["device_id"] == "MM1"
        assert result["action"] == "move"
        assert result["dx"] == -1
        assert "missing" not in result

    def test_fill_from_target_device_is(self):
        pending = {"action": "move", "dx": -1, "dy": 0, "missing": ["device_id"]}
        result = try_fill_edit_slots("Target device is MM1", pending)
        assert result is not None
        assert result["device_id"] == "MM1"

    def test_fill_from_use_device(self):
        pending = {"action": "flip", "orientation": "horizontal", "missing": ["device_id"]}
        result = try_fill_edit_slots("use M3", pending)
        assert result is not None
        assert result["device_id"] == "M3"
        assert result["action"] == "flip"

    def test_fill_with_placement_validation(self):
        pending = {"action": "move", "dx": -1, "dy": 0, "missing": ["device_id"]}
        nodes = _finger_nodes("MM1", 4)
        result = try_fill_edit_slots("MM1", pending, nodes)
        assert result is not None
        assert result["device_id"] == "MM1"

    def test_fill_returns_none_when_no_device(self):
        pending = {"action": "move", "dx": -1, "dy": 0, "missing": ["device_id"]}
        result = try_fill_edit_slots("yes please", pending)
        assert result is None

    def test_fill_returns_none_when_no_missing(self):
        pending = {"action": "move", "device_id": "M1", "dx": -1, "dy": 0}
        result = try_fill_edit_slots("MM1", pending)
        assert result is None

    def test_fill_returns_none_when_no_pending(self):
        result = try_fill_edit_slots("MM1", None)
        assert result is None


# ══════════════════════════════════════════════════════════════════
# Two-turn roundtrip via run_session_chat_agent
# ══════════════════════════════════════════════════════════════════


class TestTwoTurnSlotFilling:
    """Simulate the exact user interaction:
    Turn 1: "Move MM1 to the left" with finger-expanded nodes → should succeed directly.
    """

    def test_move_mm1_left_with_fingers_succeeds_directly(self):
        """MM1 is in a matched block, so with placement_nodes the
        matched-block safety returns clarify_matched_block. The test
        verifies this safety mechanism works correctly."""
        result = run_session_chat_agent({
            "user_message": "Move MM1 to the left",
            "placement_nodes": _finger_nodes("MM1", 4),
        })
        assert result["session_route"] == "command_edit"
        assert result["pending_cmds"]
        cmd = result["pending_cmds"][0]
        # matched-block safety returns clarify_matched_block
        assert cmd["action"] == "clarify_matched_block"
        assert cmd["device_id"] == "MM1"

    def test_move_left_without_device_creates_partial_intent(self):
        """When device is missing, should create partial intent."""
        result = run_session_chat_agent({
            "user_message": "move left",
            "placement_nodes": _finger_nodes("MM1", 4),
        })
        assert result["session_route"] == "clarify"
        assert result.get("pending_edit_intent") is not None
        intent = result["pending_edit_intent"]
        assert intent["action"] == "move"
        assert intent["dx"] == -1
        assert "device_id" in intent["missing"]
        # Should NOT have any commands
        assert result["pending_cmds"] == []

    def test_second_turn_fills_slot(self):
        """Second message fills the missing device_id."""
        # Simulate state after turn 1
        state = {
            "user_message": "Target device is MM1",
            "placement_nodes": _finger_nodes("MM1", 4),
            "pending_edit_intent": {
                "action": "move",
                "dx": -1,
                "dy": 0,
                "missing": ["device_id"],
            },
        }
        result = run_session_chat_agent(state)
        assert result["session_route"] == "command_edit"
        assert result["pending_cmds"]
        cmd = result["pending_cmds"][0]
        assert cmd["action"] == "move"
        assert cmd["device_id"] == "MM1"
        assert cmd["dx"] == -1
        # Pending intent should be cleared
        assert result.get("pending_edit_intent") is None

    def test_second_turn_bare_device_name(self):
        """User just types the device name."""
        state = {
            "user_message": "MM1",
            "placement_nodes": _finger_nodes("MM1", 4),
            "pending_edit_intent": {
                "action": "move",
                "dx": -1,
                "dy": 0,
                "missing": ["device_id"],
            },
        }
        result = run_session_chat_agent(state)
        assert result["session_route"] == "command_edit"
        assert result["pending_cmds"][0]["device_id"] == "MM1"

    def test_second_turn_flip_slot_fill(self):
        """Slot-filling for flip command."""
        state = {
            "user_message": "M3",
            "placement_nodes": [{"id": "M3", "type": "pmos"}],
            "pending_edit_intent": {
                "action": "flip",
                "orientation": "horizontal",
                "missing": ["device_id"],
            },
        }
        result = run_session_chat_agent(state)
        assert result["session_route"] == "command_edit"
        cmd = result["pending_cmds"][0]
        assert cmd["action"] == "flip"
        assert cmd["device_id"] == "M3"
        assert cmd["orientation"] == "horizontal"


# ══════════════════════════════════════════════════════════════════
# Safety: validator never receives commands without action
# ══════════════════════════════════════════════════════════════════


class TestValidatorSafety:
    """Ensure partial intents are never sent to the validator as commands."""

    def test_partial_intent_not_in_pending_cmds(self):
        """When a partial intent is stored, pending_cmds should be empty."""
        result = run_session_chat_agent({
            "user_message": "move left",
            "placement_nodes": _finger_nodes("MM1", 4),
        })
        for cmd in result.get("pending_cmds", []):
            assert "action" in cmd, "Commands must have an 'action' field"

    def test_filled_command_always_has_action(self):
        pending = {"action": "move", "dx": -1, "dy": 0, "missing": ["device_id"]}
        result = try_fill_edit_slots("MM1", pending)
        assert result is not None
        assert "action" in result

    def test_fill_with_no_action_returns_none(self):
        """Edge case: if pending_intent somehow lacks action, reject it."""
        pending = {"dx": -1, "dy": 0, "missing": ["device_id"]}
        result = try_fill_edit_slots("MM1", pending)
        assert result is None


# ══════════════════════════════════════════════════════════════════
# Edge cases
# ══════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_move_m1_left_without_placement_nodes(self):
        """Without placement_nodes, trust regex."""
        cmds = parse_direct_edit_command("Move MM1 to the left")
        assert cmds
        assert cmds[0]["device_id"] == "MM1"

    def test_move_mm10_works(self):
        nodes = _finger_nodes("MM10", 2)
        cmds = parse_direct_edit_command("move MM10 right", nodes)
        assert cmds
        assert cmds[0]["device_id"] == "MM10"

    def test_slot_fill_clears_intent_in_result(self):
        """After slot-filling, pending_edit_intent must be None."""
        state = {
            "user_message": "MM1",
            "placement_nodes": [],
            "pending_edit_intent": {
                "action": "delete",
                "missing": ["device_id"],
            },
        }
        result = run_session_chat_agent(state)
        assert result["session_route"] == "command_edit"
        assert result["pending_edit_intent"] is None

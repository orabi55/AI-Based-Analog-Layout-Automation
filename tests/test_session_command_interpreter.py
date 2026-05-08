"""
test_session_command_interpreter.py
====================================
Tests for:
  Fix 1 — Deterministic command interpreter (parse_direct_edit_command)
          and integration with run_session_chat_agent.
  Fix 2 — Conditional post-validator routing (route_after_command_validator).

All tests are self-contained — no LLM calls, no heavy imports.
"""

import pytest

from ai_agent.agents.session_chat_agent import (
    parse_direct_edit_command,
    run_session_chat_agent,
    DEVICE_RE,
)
from ai_agent.graph.edges import route_after_command_validator


# ══════════════════════════════════════════════════════════════════
# Fix 1 — parse_direct_edit_command
# ══════════════════════════════════════════════════════════════════


class TestMoveCommands:
    """Move / shift / place → move action."""

    def test_parse_move_left(self):
        cmds = parse_direct_edit_command("move M1 left")
        assert cmds == [{"action": "move", "device_id": "M1", "dx": -1, "dy": 0}]

    def test_parse_move_right(self):
        cmds = parse_direct_edit_command("move M1 right")
        assert cmds == [{"action": "move", "device_id": "M1", "dx": 1, "dy": 0}]

    def test_parse_move_up(self):
        cmds = parse_direct_edit_command("move M1 up")
        assert cmds == [{"action": "move", "device_id": "M1", "dx": 0, "dy": 1}]

    def test_parse_move_down(self):
        cmds = parse_direct_edit_command("move M1 down")
        assert cmds == [{"action": "move", "device_id": "M1", "dx": 0, "dy": -1}]

    def test_parse_shift_right(self):
        cmds = parse_direct_edit_command("shift M1 right")
        assert cmds == [{"action": "move", "device_id": "M1", "dx": 1, "dy": 0}]

    def test_parse_place_left(self):
        cmds = parse_direct_edit_command("place M1 left")
        assert cmds == [{"action": "move", "device_id": "M1", "dx": -1, "dy": 0}]

    def test_parse_move_with_amount(self):
        cmds = parse_direct_edit_command("move M1 left 3")
        assert cmds == [{"action": "move", "device_id": "M1", "dx": -3, "dy": 0}]

    def test_parse_move_right_amount(self):
        cmds = parse_direct_edit_command("move M1 right 2")
        assert cmds == [{"action": "move", "device_id": "M1", "dx": 2, "dy": 0}]

    def test_parse_move_mm3_right(self):
        """MM-prefixed device names should be extracted."""
        cmds = parse_direct_edit_command("move MM3 right")
        assert cmds == [{"action": "move", "device_id": "MM3", "dx": 1, "dy": 0}]

    def test_parse_move_explicit_dx_dy(self):
        cmds = parse_direct_edit_command("move M1 dx=-2 dy=0")
        assert cmds == [{"action": "move", "device_id": "M1", "dx": -2, "dy": 0}]

    def test_parse_move_by_deltas(self):
        cmds = parse_direct_edit_command("move M1 by -3 0")
        assert cmds == [{"action": "move", "device_id": "M1", "dx": -3, "dy": 0}]


class TestSwapCommands:
    """Swap commands."""

    def test_parse_swap(self):
        cmds = parse_direct_edit_command("swap M1 and M2")
        assert cmds[0]["action"] == "swap"
        assert cmds[0]["device_a"] == "M1"
        assert cmds[0]["device_b"] == "M2"

    def test_parse_swap_with(self):
        cmds = parse_direct_edit_command("swap M1 with M2")
        assert len(cmds) == 1
        assert cmds[0]["device_a"] == "M1"
        assert cmds[0]["device_b"] == "M2"

    def test_swap_single_device_returns_empty(self):
        """Swap with only one device should fail gracefully."""
        cmds = parse_direct_edit_command("swap M1")
        assert cmds == []


class TestFlipCommands:
    """Flip commands."""

    def test_parse_flip(self):
        cmds = parse_direct_edit_command("flip M3")
        assert cmds[0]["action"] == "flip"
        assert cmds[0]["device_id"] == "M3"
        assert cmds[0]["orientation"] == "horizontal"  # default

    def test_parse_flip_horizontal(self):
        cmds = parse_direct_edit_command("flip M1 horizontal")
        assert cmds[0]["orientation"] == "horizontal"

    def test_parse_flip_vertical(self):
        cmds = parse_direct_edit_command("flip M1 vertical")
        assert cmds[0]["orientation"] == "vertical"

    def test_flip_no_device_returns_empty(self):
        cmds = parse_direct_edit_command("flip it")
        assert cmds == []


class TestDeleteCommands:
    """Delete / remove commands."""

    def test_parse_delete(self):
        cmds = parse_direct_edit_command("delete M1")
        assert cmds == [{"action": "delete", "device_id": "M1"}]

    def test_parse_remove(self):
        cmds = parse_direct_edit_command("remove M2")
        assert cmds == [{"action": "delete", "device_id": "M2"}]

    def test_delete_no_device_returns_empty(self):
        cmds = parse_direct_edit_command("delete this")
        assert cmds == []


class TestAlignCommands:
    """Align commands — NOT SUPPORTED, should return empty."""

    def test_parse_align_returns_empty(self):
        cmds = parse_direct_edit_command("align M1 with M2")
        assert cmds == []

    def test_align_single_device_returns_empty(self):
        cmds = parse_direct_edit_command("align M1")
        assert cmds == []


class TestAbutCommands:
    """Abut commands."""

    def test_parse_abut(self):
        cmds = parse_direct_edit_command("abut M1 with M2")
        assert cmds[0]["action"] == "abut"
        assert cmds[0]["device_a"] == "M1"
        assert cmds[0]["device_b"] == "M2"


class TestMergeCommands:
    """Merge commands — NOT SUPPORTED, should return empty."""

    def test_parse_merge_returns_empty(self):
        cmds = parse_direct_edit_command("merge M1 and M2")
        assert cmds == []


class TestAddDummyCommands:
    """Add dummy commands — now require context."""

    def test_add_dummy_near_device(self):
        cmds = parse_direct_edit_command("add dummy near M1")
        assert cmds[0]["action"] == "add_dummy"
        assert cmds[0]["target"] == "M1"

    def test_add_dummy_global_returns_empty(self):
        """Vague 'add dummy' without target now returns empty for clarify."""
        cmds = parse_direct_edit_command("add dummy")
        assert cmds == []


class TestRotateCommands:
    """Rotate commands — NOT SUPPORTED, should return empty."""

    def test_parse_rotate_returns_empty(self):
        cmds = parse_direct_edit_command("rotate M1")
        assert cmds == []


class TestDeviceNameVariants:
    """Various device naming conventions should be supported."""

    def test_mm_prefix(self):
        cmds = parse_direct_edit_command("move MM12 left")
        assert cmds[0]["device_id"] == "MM12"

    def test_xm_prefix(self):
        cmds = parse_direct_edit_command("move XM1 left")
        assert cmds[0]["device_id"] == "XM1"

    def test_mn_prefix(self):
        cmds = parse_direct_edit_command("move MN1 left")
        assert cmds[0]["device_id"] == "MN1"

    def test_mp_prefix(self):
        cmds = parse_direct_edit_command("flip MP1")
        assert cmds[0]["device_id"] == "MP1"


class TestDeviceValidation:
    """Placement nodes should be used for device validation when provided."""

    def test_known_device_passes(self):
        nodes = [{"id": "M1"}, {"id": "M2"}]
        cmds = parse_direct_edit_command("move M1 left", nodes)
        assert cmds[0]["device_id"] == "M1"

    def test_unknown_device_rejected(self):
        """Device not in placement_nodes should produce empty result."""
        nodes = [{"id": "M1"}]
        cmds = parse_direct_edit_command("move M99 left", nodes)
        assert cmds == []

    def test_case_insensitive_matching(self):
        nodes = [{"id": "M1"}]
        cmds = parse_direct_edit_command("move m1 left", nodes)
        assert cmds[0]["device_id"] == "M1"  # canonical form from nodes

    def test_device_id_key(self):
        nodes = [{"device_id": "M1"}]
        cmds = parse_direct_edit_command("move M1 left", nodes)
        assert cmds[0]["device_id"] == "M1"

    def test_name_key(self):
        nodes = [{"name": "M1"}]
        cmds = parse_direct_edit_command("move M1 left", nodes)
        assert cmds[0]["device_id"] == "M1"


class TestAmbiguousMessages:
    """Ambiguous / unsafe messages should return empty list."""

    def test_ambiguous_move_returns_empty(self):
        assert parse_direct_edit_command("move it left") == []

    def test_fix_this_returns_empty(self):
        assert parse_direct_edit_command("fix this") == []

    def test_make_it_better_returns_empty(self):
        assert parse_direct_edit_command("make it better") == []

    def test_swap_the_pair_returns_empty(self):
        assert parse_direct_edit_command("swap the pair") == []

    def test_empty_message_returns_empty(self):
        assert parse_direct_edit_command("") == []

    def test_none_message_returns_empty(self):
        assert parse_direct_edit_command(None) == []

    def test_move_no_direction_returns_empty(self):
        """Move with device but no direction should be ambiguous."""
        assert parse_direct_edit_command("move M1") == []


class TestDeviceRegex:
    """DEVICE_RE should match expected patterns."""

    def test_m_prefix(self):
        assert DEVICE_RE.findall("M1 M2 M3") == ["M1", "M2", "M3"]

    def test_mm_prefix(self):
        assert DEVICE_RE.findall("MM1 MM12") == ["MM1", "MM12"]

    def test_xm_prefix(self):
        assert DEVICE_RE.findall("XM1") == ["XM1"]

    def test_mn_mp_prefix(self):
        found = DEVICE_RE.findall("MN1 MP2")
        assert "MN1" in found
        assert "MP2" in found


# ══════════════════════════════════════════════════════════════════
# Fix 1 — Integration: run_session_chat_agent produces commands
# ══════════════════════════════════════════════════════════════════


class TestRunSessionChatAgentCommandEdit:
    """run_session_chat_agent should produce pending_cmds for command_edit."""

    def test_command_edit_generates_pending_cmds(self):
        result = run_session_chat_agent({
            "user_message": "move M1 left",
            "placement_nodes": [{"id": "M1"}],
        })
        assert result["session_route"] == "command_edit"
        assert result["session_commands"]
        assert result["pending_cmds"]
        assert result["session_commands"][0]["action"] == "move"
        assert result["session_commands"][0]["device_id"] == "M1"
        assert result["session_commands"][0]["dx"] == -1

    def test_command_edit_swap_generates_cmds(self):
        result = run_session_chat_agent({
            "user_message": "swap M1 and M2",
            "placement_nodes": [{"id": "M1"}, {"id": "M2"}],
        })
        assert result["session_route"] == "command_edit"
        assert result["session_commands"][0]["action"] == "swap"

    def test_command_edit_flip_generates_cmds(self):
        result = run_session_chat_agent({
            "user_message": "flip M3",
            "placement_nodes": [{"id": "M3"}],
        })
        assert result["session_route"] == "command_edit"
        assert result["pending_cmds"][0]["action"] == "flip"

    def test_command_edit_delete_generates_cmds(self):
        result = run_session_chat_agent({
            "user_message": "delete M1",
            "placement_nodes": [{"id": "M1"}],
        })
        assert result["session_route"] == "command_edit"
        assert result["pending_cmds"][0]["action"] == "delete"

    def test_command_edit_align_routes_to_clarify(self):
        """Align is unsupported — should route to clarify."""
        result = run_session_chat_agent({
            "user_message": "align M1 with M2",
            "placement_nodes": [{"id": "M1"}, {"id": "M2"}],
        })
        assert result["session_route"] == "clarify"
        assert "not currently supported" in result["assistant_text"].lower()

    def test_command_edit_abut_generates_cmds(self):
        result = run_session_chat_agent({
            "user_message": "abut M1 with M2",
            "placement_nodes": [{"id": "M1"}, {"id": "M2"}],
        })
        assert result["session_route"] == "command_edit"
        assert result["pending_cmds"][0]["action"] == "abut"

    def test_ambiguous_command_edit_falls_back_to_clarify(self):
        """Messages that trigger command_edit keywords but have no device
        should fall back to clarify, not produce empty commands."""
        result = run_session_chat_agent({
            "user_message": "move it left",
        })
        assert result["session_route"] == "clarify"
        assert result["pending_cmds"] == []
        # With slot-filling, a partial intent is created and the user is
        # asked for the device name instead of a generic fallback.
        assert (
            "which device" in result["assistant_text"].lower()
            or "could not" in result["assistant_text"].lower()
        )

    def test_no_llm_called_for_deterministic_edit(self, monkeypatch):
        """Deterministic edits should not invoke the LLM."""
        called = {"llm": False}

        def fake_llm(*args, **kwargs):
            called["llm"] = True
            return "{}"

        monkeypatch.setattr(
            "ai_agent.agents.session_chat_agent.call_session_router_llm",
            fake_llm,
            raising=False,
        )

        result = run_session_chat_agent({
            "user_message": "move M1 left",
            "placement_nodes": [{"id": "M1"}],
        })
        assert result["session_route"] == "command_edit"
        assert result["session_commands"]
        assert called["llm"] is False

    def test_assistant_text_populated_for_command_edit(self):
        result = run_session_chat_agent({
            "user_message": "move M1 left",
            "placement_nodes": [{"id": "M1"}],
        })
        assert result["assistant_text"]  # non-empty


# ══════════════════════════════════════════════════════════════════
# Fix 2 — route_after_command_validator
# ══════════════════════════════════════════════════════════════════


class TestRouteAfterCommandValidator:
    """Conditional routing after command validation."""

    def test_valid_commands_routes_to_human_viewer(self):
        state = {"pending_cmds": [{"action": "move", "device_id": "M1"}]}
        assert route_after_command_validator(state) == "node_human_viewer"

    def test_empty_pending_cmds_routes_to_finalizer(self):
        state = {"pending_cmds": []}
        assert route_after_command_validator(state) == "node_session_finalizer"

    def test_none_pending_cmds_routes_to_finalizer(self):
        state = {"pending_cmds": None}
        assert route_after_command_validator(state) == "node_session_finalizer"

    def test_missing_pending_cmds_routes_to_finalizer(self):
        state = {}
        assert route_after_command_validator(state) == "node_session_finalizer"

    def test_clarify_route_routes_to_finalizer(self):
        state = {"session_route": "clarify", "pending_cmds": []}
        assert route_after_command_validator(state) == "node_session_finalizer"

    def test_clarify_with_commands_still_routes_to_finalizer(self):
        """If validator set route to clarify, don't go to viewer even with cmds."""
        state = {
            "session_route": "clarify",
            "pending_cmds": [{"action": "move", "device_id": "M1"}],
        }
        assert route_after_command_validator(state) == "node_session_finalizer"

    def test_warnings_do_not_block_human_viewer(self):
        state = {
            "pending_cmds": [{"action": "move", "device_id": "M1"}],
            "validation_warnings": ["M1 is in matched group"],
        }
        assert route_after_command_validator(state) == "node_human_viewer"

    def test_validation_errors_with_no_cmds_goes_to_finalizer(self):
        state = {
            "pending_cmds": [],
            "validation_errors": ["Command 1: Unknown action 'teleport'"],
        }
        assert route_after_command_validator(state) == "node_session_finalizer"


# ══════════════════════════════════════════════════════════════════
# Fix 2 — Integration: validator → conditional → correct node
# ══════════════════════════════════════════════════════════════════

# Load node modules directly to avoid heavy __init__ chain
import importlib
import importlib.util
import sys
from pathlib import Path


def _load_module(name, relpath):
    if name in sys.modules:
        return sys.modules[name]
    import ai_agent.utils.logging  # noqa
    mod_path = Path(__file__).resolve().parents[1] / relpath
    spec = importlib.util.spec_from_file_location(name, mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_cv_mod = _load_module(
    "ai_agent.nodes.command_validator",
    "ai_agent/nodes/command_validator.py",
)
node_command_validator = _cv_mod.node_command_validator


class TestValidatorToRouterIntegration:
    """End-to-end: validator → route_after_command_validator → correct node."""

    def test_valid_command_flows_to_viewer(self):
        val_input = {
            "session_route": "command_edit",
            "pending_cmds": [{"action": "move", "device_id": "M1", "dx": -1, "dy": 0}],
            "placement_nodes": [{"id": "M1"}],
        }
        val_result = node_command_validator(val_input)
        merged = {**val_input, **val_result}
        assert route_after_command_validator(merged) == "node_human_viewer"

    def test_command_edit_no_valid_commands_ends_in_finalizer(self):
        """command_edit with all commands rejected should end in finalizer."""
        val_input = {
            "session_route": "command_edit",
            "pending_cmds": [{"action": "teleport", "device_id": "M1"}],
            "placement_nodes": [{"id": "M1"}],
        }
        val_result = node_command_validator(val_input)
        merged = {**val_input, **val_result}
        # Validator should have set route to clarify and emptied pending_cmds
        assert merged["pending_cmds"] == []
        assert route_after_command_validator(merged) == "node_session_finalizer"

    def test_command_edit_empty_cmds_ends_in_finalizer(self):
        """command_edit with no commands at all should end in finalizer."""
        val_input = {
            "session_route": "command_edit",
            "pending_cmds": [],
            "placement_nodes": [{"id": "M1"}],
        }
        val_result = node_command_validator(val_input)
        merged = {**val_input, **val_result}
        assert route_after_command_validator(merged) == "node_session_finalizer"

    def test_warnings_allow_flow_to_viewer(self):
        """Symmetry warnings should not prevent human review."""
        val_input = {
            "session_route": "command_edit",
            "pending_cmds": [{"action": "move", "device_id": "M1", "dx": 1}],
            "placement_nodes": [{"id": "M1"}],
            "initial_agent_trace": {
                "strategy": "Matched pairs: M1 and M2, using symmetry.",
            },
        }
        val_result = node_command_validator(val_input)
        merged = {**val_input, **val_result}
        assert len(merged["pending_cmds"]) > 0
        assert merged.get("validation_warnings")  # warning should be present
        assert route_after_command_validator(merged) == "node_human_viewer"

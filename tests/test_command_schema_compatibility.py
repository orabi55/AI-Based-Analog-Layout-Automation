"""
test_command_schema_compatibility.py
=====================================
Tests for Fix A — command schema key compatibility across all layers:
  - apply_cmds_to_nodes accepts device_id / device / id for all actions
  - parse_direct_edit_command → apply_cmds_to_nodes roundtrip works
  - Validator ALLOWED_ACTIONS is a subset of SUPPORTED_COMMAND_ACTIONS + extras
"""

import pytest
import copy

from ai_agent.tools.cmd_parser import apply_cmds_to_nodes
from ai_agent.tools.command_schema import (
    BATCH_SUPPORTED_ACTIONS,
    GUI_SUPPORTED_ACTIONS,
    SUPPORTED_COMMAND_ACTIONS,
    get_cmd_device,
    get_cmd_device_a,
    get_cmd_device_b,
    logical_base_device_id,
)
from ai_agent.agents.session_chat_agent import parse_direct_edit_command


# ══════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════

def _make_nodes():
    """Return a minimal set of placement nodes with geometry."""
    return [
        {"id": "M1", "type": "nmos", "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.668, "orientation": "R0"}},
        {"id": "M2", "type": "nmos", "geometry": {"x": 0.5, "y": 0.0, "width": 0.294, "height": 0.668, "orientation": "R0"}},
    ]


# ══════════════════════════════════════════════════════════════════
# Fix A — apply_cmds_to_nodes key compatibility
# ══════════════════════════════════════════════════════════════════


class TestFlipAcceptsDeviceId:
    """Flip must work with device_id key (emitted by deterministic parser)."""

    def test_flip_with_device_id(self):
        nodes = _make_nodes()
        out = apply_cmds_to_nodes(nodes, [{"action": "flip", "device_id": "M1"}])
        assert out is not None
        m1 = next(n for n in out if n["id"] == "M1")
        assert m1["geometry"]["orientation"] != "R0"

    def test_flip_with_device_key(self):
        nodes = _make_nodes()
        out = apply_cmds_to_nodes(nodes, [{"action": "flip", "device": "M1"}])
        m1 = next(n for n in out if n["id"] == "M1")
        assert m1["geometry"]["orientation"] != "R0"

    def test_flip_with_id_key(self):
        nodes = _make_nodes()
        out = apply_cmds_to_nodes(nodes, [{"action": "flip", "id": "M1"}])
        m1 = next(n for n in out if n["id"] == "M1")
        assert m1["geometry"]["orientation"] != "R0"


class TestDeleteAcceptsDeviceId:
    """Delete must work with device_id key."""

    def test_delete_with_device_id(self):
        nodes = _make_nodes()
        out = apply_cmds_to_nodes(nodes, [{"action": "delete", "device_id": "M1"}])
        ids = {n.get("id") for n in out}
        assert "M1" not in ids
        assert "M2" in ids

    def test_delete_with_device_key(self):
        nodes = _make_nodes()
        out = apply_cmds_to_nodes(nodes, [{"action": "delete", "device": "M1"}])
        ids = {n.get("id") for n in out}
        assert "M1" not in ids

    def test_delete_with_id_key(self):
        nodes = _make_nodes()
        out = apply_cmds_to_nodes(nodes, [{"action": "delete", "id": "M1"}])
        ids = {n.get("id") for n in out}
        assert "M1" not in ids


class TestMoveAcceptsDeviceId:
    """Move should accept all key variants."""

    def test_move_with_device_id(self):
        nodes = _make_nodes()
        out = apply_cmds_to_nodes(nodes, [{"action": "move", "device_id": "M1", "x": 5.0}])
        m1 = next(n for n in out if n["id"] == "M1")
        assert m1["geometry"]["x"] == 5.0

    def test_move_with_device_key(self):
        nodes = _make_nodes()
        out = apply_cmds_to_nodes(nodes, [{"action": "move", "device": "M1", "x": 5.0}])
        m1 = next(n for n in out if n["id"] == "M1")
        assert m1["geometry"]["x"] == 5.0


class TestSwapAcceptsVariants:
    """Swap should accept device_a/device_b and a/b and source/target."""

    def test_swap_standard_keys(self):
        nodes = _make_nodes()
        out = apply_cmds_to_nodes(nodes, [{"action": "swap", "device_a": "M1", "device_b": "M2"}])
        m1 = next(n for n in out if n["id"] == "M1")
        m2 = next(n for n in out if n["id"] == "M2")
        assert m1["geometry"]["x"] == 0.5
        assert m2["geometry"]["x"] == 0.0

    def test_swap_short_keys(self):
        nodes = _make_nodes()
        out = apply_cmds_to_nodes(nodes, [{"action": "swap", "a": "M1", "b": "M2"}])
        m1 = next(n for n in out if n["id"] == "M1")
        assert m1["geometry"]["x"] == 0.5

    def test_swap_source_target_keys(self):
        nodes = _make_nodes()
        out = apply_cmds_to_nodes(nodes, [{"action": "swap", "source": "M1", "target": "M2"}])
        m1 = next(n for n in out if n["id"] == "M1")
        assert m1["geometry"]["x"] == 0.5


# ══════════════════════════════════════════════════════════════════
# Parser → Executor roundtrip
# ══════════════════════════════════════════════════════════════════


class TestInterpreterFlipCommandApplies:
    """Commands from parse_direct_edit_command must apply via apply_cmds_to_nodes."""

    def test_flip_roundtrip(self):
        cmd = parse_direct_edit_command("flip M1")[0]
        assert cmd["action"] == "flip"
        nodes = _make_nodes()
        out = apply_cmds_to_nodes(nodes, [cmd])
        assert out is not None
        m1 = next(n for n in out if n["id"] == "M1")
        assert m1["geometry"]["orientation"] != "R0"

    def test_delete_roundtrip(self):
        cmd = parse_direct_edit_command("delete M1")[0]
        nodes = _make_nodes()
        out = apply_cmds_to_nodes(nodes, [cmd])
        assert "M1" not in {n.get("id") for n in out}


# ══════════════════════════════════════════════════════════════════
# Schema constants consistency
# ══════════════════════════════════════════════════════════════════


class TestSchemaConstants:
    """Verify schema sets are consistent."""

    def test_batch_is_subset_of_gui(self):
        """Batch actions should be a subset of GUI actions."""
        assert BATCH_SUPPORTED_ACTIONS.issubset(GUI_SUPPORTED_ACTIONS)

    def test_supported_equals_gui(self):
        """SUPPORTED_COMMAND_ACTIONS should equal GUI_SUPPORTED_ACTIONS."""
        assert SUPPORTED_COMMAND_ACTIONS == GUI_SUPPORTED_ACTIONS

    def test_validator_allowed_subset_of_supported(self):
        """Validator ALLOWED_ACTIONS must be a subset of SUPPORTED + extras."""
        import importlib.util, sys
        from pathlib import Path

        mod_path = Path(__file__).resolve().parents[1] / "ai_agent" / "nodes" / "command_validator.py"
        spec = importlib.util.spec_from_file_location("cv", mod_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("cv_test", mod)
        spec.loader.exec_module(mod)

        allowed = mod.ALLOWED_ACTIONS
        expected_superset = SUPPORTED_COMMAND_ACTIONS | {"move_pair", "add dummy"}
        assert allowed.issubset(expected_superset), (
            f"ALLOWED_ACTIONS has unexpected entries: {allowed - expected_superset}"
        )


# ══════════════════════════════════════════════════════════════════
# Device-key helpers
# ══════════════════════════════════════════════════════════════════


class TestGetCmdDevice:
    def test_device_id_priority(self):
        assert get_cmd_device({"device_id": "M1", "device": "M2"}) == "M1"

    def test_device_fallback(self):
        assert get_cmd_device({"device": "M2"}) == "M2"

    def test_id_fallback(self):
        assert get_cmd_device({"id": "M3"}) == "M3"

    def test_name_fallback(self):
        assert get_cmd_device({"name": "M4"}) == "M4"

    def test_empty(self):
        assert get_cmd_device({}) is None


class TestLogicalBaseDeviceId:
    def test_f_suffix(self):
        assert logical_base_device_id("M1_f0") == "M1"

    def test_finger_suffix(self):
        assert logical_base_device_id("M1_finger0") == "M1"

    def test_bracket_suffix(self):
        assert logical_base_device_id("M1[0]") == "M1"

    def test_double_underscore(self):
        assert logical_base_device_id("M1__finger0") == "M1"

    def test_no_suffix(self):
        assert logical_base_device_id("M1") == "M1"

    def test_complex_name(self):
        assert logical_base_device_id("MM28_f3") == "MM28"

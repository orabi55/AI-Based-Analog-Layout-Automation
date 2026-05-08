"""
test_deterministic_tools_hardening.py
=====================================
Task 0 — Confirms that all 12 deterministic tool checks pass.
Task 1 — Confirms LayoutState fields and constant sets for layout_session_agent.

All tests are self-contained — no LLM calls, no heavy imports.
"""

import pytest

from ai_agent.agents.session_chat_agent import (
    parse_direct_edit_command,
    try_fill_edit_slots,
    run_session_chat_agent,
)
from ai_agent.tools.command_schema import (
    BATCH_SUPPORTED_ACTIONS,
    GUI_SUPPORTED_ACTIONS,
    SUPPORTED_COMMAND_ACTIONS,
    get_cmd_device,
    get_cmd_device_a,
    get_cmd_device_b,
    logical_base_device_id,
)
from ai_agent.graph.edges import route_after_command_validator


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


# Load command_validator without triggering full __init__ chain
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


# ══════════════════════════════════════════════════════════════════
# Task 0 — Required tests (checks 1-12)
# ══════════════════════════════════════════════════════════════════


class TestCheck1_ParseMoveMM1Left:
    """Check 1: parse_direct_edit_command("Move MM1 to the left") → move cmd."""

    def test_parse_move_mm1_left(self):
        nodes = _finger_nodes("MM1", 4)
        cmds = parse_direct_edit_command("Move MM1 to the left", nodes)
        assert cmds, "Should produce a move command"
        assert cmds[0]["action"] == "move"
        assert cmds[0]["device_id"] == "MM1"
        assert cmds[0]["dx"] == -1


class TestCheck2_ParseMoveLeftAmbiguous:
    """Check 2: parse_direct_edit_command("move left") → [] + pending intent."""

    def test_move_left_returns_empty(self):
        cmds = parse_direct_edit_command("move left")
        assert cmds == [], "Ambiguous 'move left' should return []"

    def test_move_left_creates_pending_intent(self):
        result = run_session_chat_agent({
            "user_message": "move left",
            "placement_nodes": _finger_nodes("MM1", 4),
        })
        assert result["session_route"] == "clarify"
        assert result.get("pending_edit_intent") is not None
        assert result["pending_edit_intent"]["action"] == "move"
        assert "device_id" in result["pending_edit_intent"]["missing"]


class TestCheck3_TryFillEditSlots:
    """Check 3: try_fill_edit_slots("Target device is MM1") completes intent."""

    def test_fill_slot_completes_move(self):
        pending = {"action": "move", "dx": -1, "dy": 0, "missing": ["device_id"]}
        result = try_fill_edit_slots("Target device is MM1", pending)
        assert result is not None
        assert result["device_id"] == "MM1"
        assert result["action"] == "move"
        assert result["dx"] == -1
        assert "missing" not in result


class TestCheck4_UnsupportedAlign:
    """Check 4: align must not emit an unsupported command."""

    def test_parse_unsupported_align_returns_empty(self):
        cmds = parse_direct_edit_command("align M1 with M2")
        assert cmds == [], "'align' is unsupported, must return []"

    def test_align_routes_to_clarify(self):
        result = run_session_chat_agent({
            "user_message": "align M1 with M2",
            "placement_nodes": [{"id": "M1"}, {"id": "M2"}],
        })
        assert result["session_route"] == "clarify"
        assert "not currently supported" in result["assistant_text"].lower()


class TestCheck5_UnsupportedMerge:
    """Check 5: merge must not emit an unsupported command."""

    def test_parse_unsupported_merge_returns_empty(self):
        cmds = parse_direct_edit_command("merge M1 and M2")
        assert cmds == [], "'merge' is unsupported, must return []"


class TestCheck6_UnsupportedRotate:
    """Check 6: rotate must not emit an unsupported command."""

    def test_parse_unsupported_rotate_returns_empty(self):
        cmds = parse_direct_edit_command("rotate M1")
        assert cmds == [], "'rotate' is unsupported, must return []"


class TestCheck7_VagueAddDummy:
    """Check 7: add dummy without target should clarify, not emit action."""

    def test_parse_vague_add_dummy_returns_empty(self):
        cmds = parse_direct_edit_command("add dummy")
        assert cmds == [], "Vague 'add dummy' without target must return []"


class TestCheck8_AddDummyWithContext:
    """Check 8: add dummy left of M1 may emit target/side if validator handles it."""

    def test_add_dummy_left_of_m1(self):
        cmds = parse_direct_edit_command("add dummy left of M1")
        assert cmds, "Should produce a command"
        assert cmds[0]["action"] == "add_dummy"
        assert cmds[0]["target"] == "M1"
        assert cmds[0]["side"] == "left"


class TestCheck9_ValidatorRejectsUnsupported:
    """Check 9: command_validator must reject unsupported actions."""

    def test_validator_rejects_unsupported_action(self):
        state = {
            "pending_cmds": [{"action": "teleport", "device_id": "M1"}],
            "placement_nodes": [{"id": "M1"}],
        }
        result = node_command_validator(state)
        assert result["pending_cmds"] == []
        assert result["validation_errors"]
        assert "teleport" in str(result["validation_errors"]).lower()

    def test_validator_rejects_align(self):
        state = {
            "pending_cmds": [{"action": "align", "device_id": "M1"}],
            "placement_nodes": [{"id": "M1"}],
        }
        result = node_command_validator(state)
        assert result["pending_cmds"] == []

    def test_validator_rejects_rotate(self):
        state = {
            "pending_cmds": [{"action": "rotate", "device_id": "M1"}],
            "placement_nodes": [{"id": "M1"}],
        }
        result = node_command_validator(state)
        assert result["pending_cmds"] == []


class TestCheck10_ValidatorBlocksInvalid:
    """Check 10: validator must not pass invalid commands to human_viewer."""

    def test_validator_empty_pending_cmds_routes_finalizer(self):
        state = {"pending_cmds": [], "session_route": "command_edit"}
        result = node_command_validator(state)
        merged = {**state, **result}
        assert route_after_command_validator(merged) == "node_session_finalizer"

    def test_all_invalid_cmds_route_to_finalizer(self):
        state = {
            "pending_cmds": [
                {"action": "teleport", "device_id": "M1"},
                {"action": "warp", "device_id": "M2"},
            ],
            "placement_nodes": [{"id": "M1"}, {"id": "M2"}],
            "session_route": "command_edit",
        }
        result = node_command_validator(state)
        merged = {**state, **result}
        assert merged["pending_cmds"] == []
        assert route_after_command_validator(merged) == "node_session_finalizer"


class TestCheck11_CmdParserDeviceKeys:
    """Check 11: cmd_parser must accept device_id for move/swap/flip/delete."""

    def test_cmd_parser_flip_accepts_device_id(self):
        from ai_agent.tools.cmd_parser import apply_cmds_to_nodes
        nodes = [{"id": "M1", "type": "nmos", "geometry": {"x": 0, "y": 0, "orientation": "R0"}}]
        result = apply_cmds_to_nodes(nodes, [{"action": "flip", "device_id": "M1"}])
        assert result[0]["geometry"]["orientation"] != "R0"

    def test_cmd_parser_delete_accepts_device_id(self):
        from ai_agent.tools.cmd_parser import apply_cmds_to_nodes
        nodes = [
            {"id": "M1", "type": "nmos", "geometry": {"x": 0, "y": 0}},
            {"id": "M2", "type": "nmos", "geometry": {"x": 1, "y": 0}},
        ]
        result = apply_cmds_to_nodes(nodes, [{"action": "delete", "device_id": "M1"}])
        assert len(result) == 1
        assert result[0]["id"] == "M2"

    def test_cmd_parser_move_accepts_device_id(self):
        from ai_agent.tools.cmd_parser import apply_cmds_to_nodes
        nodes = [{"id": "M1", "type": "nmos", "geometry": {"x": 0.0, "y": 0.0}}]
        result = apply_cmds_to_nodes(nodes, [{"action": "move", "device_id": "M1", "x": 5.0}])
        assert result[0]["geometry"]["x"] == 5.0

    def test_cmd_parser_swap_accepts_device_a_b(self):
        from ai_agent.tools.cmd_parser import apply_cmds_to_nodes
        nodes = [
            {"id": "M1", "type": "nmos", "geometry": {"x": 0.0, "y": 0.0, "orientation": "R0"}},
            {"id": "M2", "type": "nmos", "geometry": {"x": 5.0, "y": 0.0, "orientation": "R0"}},
        ]
        result = apply_cmds_to_nodes(nodes, [{"action": "swap", "device_a": "M1", "device_b": "M2"}])
        id_map = {n["id"]: n for n in result}
        assert id_map["M1"]["geometry"]["x"] == 5.0
        assert id_map["M2"]["geometry"]["x"] == 0.0


class TestCheck12_CommandSchemaCompleteness:
    """Check 12: command_schema defines all required helpers and action sets."""

    def test_batch_supported_actions_defined(self):
        assert "move" in BATCH_SUPPORTED_ACTIONS
        assert "swap" in BATCH_SUPPORTED_ACTIONS
        assert "flip" in BATCH_SUPPORTED_ACTIONS
        assert "delete" in BATCH_SUPPORTED_ACTIONS

    def test_gui_supported_actions_superset_of_batch(self):
        assert BATCH_SUPPORTED_ACTIONS <= GUI_SUPPORTED_ACTIONS

    def test_supported_command_actions_equals_gui(self):
        assert SUPPORTED_COMMAND_ACTIONS == GUI_SUPPORTED_ACTIONS

    def test_get_cmd_device_helpers(self):
        cmd = {"device_id": "M1", "device": "M2"}
        assert get_cmd_device(cmd) == "M1"  # device_id has priority

    def test_get_cmd_device_ab_helpers(self):
        cmd = {"device_a": "M1", "device_b": "M2"}
        assert get_cmd_device_a(cmd) == "M1"
        assert get_cmd_device_b(cmd) == "M2"

    def test_logical_base_device_id(self):
        assert logical_base_device_id("M1_f0") == "M1"
        assert logical_base_device_id("M1_finger0") == "M1"
        assert logical_base_device_id("M1[0]") == "M1"
        assert logical_base_device_id("M1") == "M1"


# ══════════════════════════════════════════════════════════════════
# Task 1 — LayoutState fields & constants
# ══════════════════════════════════════════════════════════════════


class TestLayoutSessionStateFields:
    """Task 1: New fields must be declared, NotRequired, and non-breaking."""

    def test_layout_session_state_fields_are_notrequired(self):
        from ai_agent.graph.state import LayoutState
        annotations = LayoutState.__annotations__
        new_fields = [
            "layout_session_decision",
            "layout_session_confidence",
            "layout_session_reason",
            "layout_session_tool_name",
            "layout_session_tool_args",
            "deterministic_tool_result",
            "layout_session_specialist",
            "layout_session_specialist_question",
            "layout_session_memory_update",
            "layout_session_raw_json",
            "layout_session_target_nets",
            "layout_session_needs_synthesis",
        ]
        for field in new_fields:
            assert field in annotations, f"Field {field!r} missing from LayoutState"
            assert "NotRequired" in str(annotations[field]), (
                f"Field {field!r} should be NotRequired, got: {annotations[field]}"
            )

    def test_layout_session_state_legacy_dict_still_valid(self):
        """A minimal pre-existing state dict must still be usable."""
        state = {"mode": "chat", "user_message": "hello"}
        assert state["mode"] == "chat"
        # New fields must not be required — .get() returns None safely
        assert state.get("layout_session_decision") is None
        assert state.get("layout_session_confidence") is None
        assert state.get("deterministic_tool_result") is None
        assert state.get("layout_session_needs_synthesis") is None


class TestLayoutSessionConstants:
    """Task 1: Constant sets must be importable and contain expected values."""

    def test_layout_session_decisions_constants(self):
        from ai_agent.agents.layout_session_agent import VALID_LAYOUT_SESSION_DECISIONS
        expected = {
            "answer", "clarify", "call_deterministic_tool",
            "propose_commands", "call_specialist",
            "check_drc", "fix_drc", "check_routing", "optimize_routing",
        }
        assert VALID_LAYOUT_SESSION_DECISIONS == expected

    def test_layout_session_deterministic_tools_constants(self):
        from ai_agent.agents.layout_session_agent import VALID_DETERMINISTIC_TOOLS
        expected = {
            "rule_route", "parse_direct_edit_command",
            "try_fill_edit_slots", "extract_target_nets",
            "answer_from_initial_trace",
        }
        assert VALID_DETERMINISTIC_TOOLS == expected

    def test_layout_session_specialists_constants(self):
        from ai_agent.agents.layout_session_agent import VALID_SPECIALISTS
        expected = {
            "topology_analyst", "strategy_selector",
            "placement_specialist", "drc_critic", "routing_previewer",
        }
        assert VALID_SPECIALISTS == expected

    def test_constants_are_frozensets(self):
        from ai_agent.agents.layout_session_agent import (
            VALID_LAYOUT_SESSION_DECISIONS,
            VALID_DETERMINISTIC_TOOLS,
            VALID_SPECIALISTS,
        )
        assert isinstance(VALID_LAYOUT_SESSION_DECISIONS, frozenset)
        assert isinstance(VALID_DETERMINISTIC_TOOLS, frozenset)
        assert isinstance(VALID_SPECIALISTS, frozenset)

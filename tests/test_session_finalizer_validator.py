"""
Tests for node_session_finalizer (Task 7) and node_command_validator (Task 8).

All tests are self-contained — no LLM calls, no heavy imports.
Modules are loaded via importlib.util to bypass the ai_agent.nodes.__init__.py
chain that pulls in langchain.
"""

import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

# Pre-import lightweight ai_agent sub-packages so they are real packages
# in sys.modules (not stubs).  We only need to dodge ai_agent.nodes.__init__.py.
import ai_agent.utils.logging  # noqa: F401  — lightweight, no langchain
import ai_agent.agents.session_chat_agent  # noqa: F401  — for validator's ALLOWED_ACTIONS reference


# ---------------------------------------------------------------------------
# Load individual node modules by file path, skipping __init__.py
# ---------------------------------------------------------------------------

def _load_node_module(filename: str, mod_name: str):
    """Import a single node .py by absolute path."""
    if mod_name in sys.modules:
        return sys.modules[mod_name]

    mod_path = (
        Path(__file__).resolve().parents[1]
        / "ai_agent" / "nodes" / filename
    )
    spec = importlib.util.spec_from_file_location(mod_name, mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


_finalizer_mod = _load_node_module("session_finalizer.py", "ai_agent.nodes.session_finalizer")
_validator_mod = _load_node_module("command_validator.py", "ai_agent.nodes.command_validator")

node_session_finalizer = _finalizer_mod.node_session_finalizer
node_command_validator = _validator_mod.node_command_validator


# ══════════════════════════════════════════════════════════════════
# Task 7 — node_session_finalizer
# ══════════════════════════════════════════════════════════════════

class TestFinalizerExistingText:
    """Existing assistant_text should pass through unchanged."""

    def test_uses_existing_assistant_text(self):
        result = node_session_finalizer({"assistant_text": "Existing answer."})
        assert result["assistant_text"] == "Existing answer."

    def test_strips_whitespace_only_text(self):
        result = node_session_finalizer({"assistant_text": "   "})
        # Whitespace-only is treated as empty → fallback
        assert result["assistant_text"] != "   "
        assert len(result["assistant_text"]) > 0


class TestFinalizerDRC:
    """DRC-related routes should produce meaningful summaries."""

    def test_drc_pass(self):
        result = node_session_finalizer({
            "session_route": "need_drc",
            "drc_pass": True,
        })
        assert "passed" in result["assistant_text"].lower()

    def test_drc_flags_string_list(self):
        result = node_session_finalizer({
            "session_route": "need_drc",
            "drc_pass": False,
            "drc_flags": [{"description": "overlap M1 M2"}],
        })
        assert "overlap" in result["assistant_text"]

    def test_drc_flags_dict_with_value_key(self):
        result = node_session_finalizer({
            "session_route": "need_drc",
            "drc_pass": False,
            "drc_flags": [{"value": "spacing violation near M3"}],
        })
        assert "spacing" in result["assistant_text"]

    def test_drc_no_flags_no_pass(self):
        result = node_session_finalizer({
            "session_route": "need_drc",
            "drc_pass": False,
            "drc_flags": [],
        })
        assert "completed" in result["assistant_text"].lower()

    def test_drc_truncates_many_flags(self):
        flags = [{"description": f"violation {i}"} for i in range(20)]
        result = node_session_finalizer({
            "session_route": "need_drc",
            "drc_pass": False,
            "drc_flags": flags,
        })
        assert "more" in result["assistant_text"].lower()


class TestFinalizerRouting:
    """Routing-related routes should extract log_text or summary."""

    def test_routing_log_text(self):
        result = node_session_finalizer({
            "session_route": "need_routing",
            "routing_result": {"log_text": "2 crossings found"},
        })
        assert "2 crossings" in result["assistant_text"]

    def test_routing_summary_fallback(self):
        result = node_session_finalizer({
            "session_route": "need_routing",
            "routing_result": {"summary": "All nets routed."},
        })
        assert "All nets" in result["assistant_text"]

    def test_routing_empty_result(self):
        result = node_session_finalizer({
            "session_route": "need_routing",
            "routing_result": {},
        })
        assert "completed" in result["assistant_text"].lower()


class TestFinalizerTopology:

    def test_topology_with_analysis(self):
        result = node_session_finalizer({
            "session_route": "need_topology",
            "Analysis_result": "Found 2 diff pairs and 1 current mirror.",
        })
        assert "diff pair" in result["assistant_text"]

    def test_topology_empty(self):
        result = node_session_finalizer({"session_route": "need_topology"})
        assert "completed" in result["assistant_text"].lower()


class TestFinalizerStrategy:

    def test_strategy_with_result(self):
        result = node_session_finalizer({
            "session_route": "need_strategy",
            "strategy_result": "Using common-centroid for current mirrors.",
        })
        assert "common-centroid" in result["assistant_text"]

    def test_strategy_empty(self):
        result = node_session_finalizer({"session_route": "need_strategy"})
        assert "completed" in result["assistant_text"].lower()


class TestFinalizerClarify:

    def test_clarify_route(self):
        result = node_session_finalizer({"session_route": "clarify"})
        assert "detail" in result["assistant_text"].lower()


class TestFinalizerEmptyState:

    def test_empty_state(self):
        result = node_session_finalizer({})
        assert result["assistant_text"] == "Done."

    def test_answer_only_no_text(self):
        result = node_session_finalizer({"session_route": "answer_only"})
        assert result["assistant_text"] == "Done."


# ══════════════════════════════════════════════════════════════════
# Task 8 — node_command_validator
# ══════════════════════════════════════════════════════════════════

class TestValidatorActionCheck:
    """Unknown actions must be rejected."""

    def test_rejects_unknown_action(self):
        state = {
            "pending_cmds": [{"action": "teleport", "device_id": "M1"}],
            "placement_nodes": [{"id": "M1"}],
        }
        result = node_command_validator(state)
        assert result["pending_cmds"] == []
        assert result["validation_errors"]
        assert "teleport" in str(result["validation_errors"])

    def test_accepts_all_allowed_actions(self):
        """Every action in ALLOWED_ACTIONS should pass (with valid fields)."""
        from ai_agent.nodes.command_validator import ALLOWED_ACTIONS
        for action in ALLOWED_ACTIONS:
            # move_pair requires ≥2 devices and a delta (Fix 11)
            if action == "move_pair":
                cmd = {"action": action, "devices": ["M1", "M2"], "dx": 1, "dy": 0}
                nodes = [{"id": "M1"}, {"id": "M2"}]
            else:
                cmd = {"action": action, "device_id": "M1"}
                nodes = [{"id": "M1"}]
            state = {
                "pending_cmds": [cmd],
                "placement_nodes": nodes,
            }
            result = node_command_validator(state)
            assert len(result["pending_cmds"]) == 1, f"Action '{action}' was rejected"


class TestValidatorDeviceCheck:
    """Commands referencing unknown devices must be rejected."""

    def test_rejects_unknown_device(self):
        state = {
            "pending_cmds": [{"action": "move", "device_id": "M9", "dx": 1, "dy": 0}],
            "placement_nodes": [{"id": "M1"}],
        }
        result = node_command_validator(state)
        assert result["pending_cmds"] == []
        assert "M9" in str(result["validation_errors"])

    def test_accepts_known_device(self):
        state = {
            "pending_cmds": [{"action": "move", "device_id": "M1", "dx": 1, "dy": 0}],
            "placement_nodes": [{"id": "M1", "type": "nmos", "x": 0, "y": 100}],
        }
        result = node_command_validator(state)
        assert len(result["pending_cmds"]) == 1

    def test_swap_both_devices_must_exist(self):
        state = {
            "pending_cmds": [{"action": "swap", "device_a": "M1", "device_b": "M99"}],
            "placement_nodes": [{"id": "M1"}],
        }
        result = node_command_validator(state)
        assert result["pending_cmds"] == []
        assert "M99" in str(result["validation_errors"])

    def test_swap_both_devices_valid(self):
        state = {
            "pending_cmds": [{"action": "swap", "device_a": "M1", "device_b": "M2"}],
            "placement_nodes": [{"id": "M1"}, {"id": "M2"}],
        }
        result = node_command_validator(state)
        assert len(result["pending_cmds"]) == 1

    def test_handles_name_key(self):
        """Validator should handle 'name' as device identifier."""
        state = {
            "pending_cmds": [{"action": "move", "name": "M1", "dx": 1}],
            "placement_nodes": [{"id": "M1"}],
        }
        result = node_command_validator(state)
        assert len(result["pending_cmds"]) == 1


class TestValidatorEmptyCommands:
    """Empty command list on command_edit route should clarify."""

    def test_empty_command_edit_clarifies(self):
        state = {
            "session_route": "command_edit",
            "pending_cmds": [],
            "placement_nodes": [{"id": "M1"}],
        }
        result = node_command_validator(state)
        assert result["session_route"] == "clarify"
        assert result["pending_cmds"] == []

    def test_non_edit_route_with_no_cmds(self):
        state = {
            "session_route": "answer_only",
            "pending_cmds": [],
        }
        result = node_command_validator(state)
        assert result.get("session_route") != "clarify"


class TestValidatorMixedCommands:
    """With a mix of valid and invalid commands, only valid pass through."""

    def test_partial_validation(self):
        state = {
            "pending_cmds": [
                {"action": "move", "device_id": "M1", "dx": 1, "dy": 0},
                {"action": "teleport", "device_id": "M1"},
                {"action": "move", "device_id": "M2", "dx": -1, "dy": 0},
            ],
            "placement_nodes": [{"id": "M1"}, {"id": "M2"}],
        }
        result = node_command_validator(state)
        assert len(result["pending_cmds"]) == 2
        assert len(result["validation_errors"]) == 1


class TestValidatorNonDictCommand:
    """Non-dict commands should be rejected."""

    def test_string_command_rejected(self):
        state = {
            "pending_cmds": ["move M1 left"],
            "placement_nodes": [{"id": "M1"}],
        }
        result = node_command_validator(state)
        assert result["pending_cmds"] == []
        assert result["validation_errors"]


class TestValidatorSymmetryWarning:
    """Symmetry warnings should appear but not block the command."""

    def test_symmetry_warning_non_blocking(self):
        state = {
            "pending_cmds": [{"action": "move", "device_id": "M1", "dx": 1}],
            "placement_nodes": [{"id": "M1"}],
            "initial_agent_trace": {
                "strategy": "Use symmetry axis. Matched pairs: M1 and M2.",
            },
        }
        result = node_command_validator(state)
        # Command still passes
        assert len(result["pending_cmds"]) == 1
        # But warning is emitted
        assert result["validation_warnings"]


class TestValidatorAssistantText:
    """Validator should produce meaningful assistant_text."""

    def test_valid_commands_text(self):
        state = {
            "pending_cmds": [{"action": "move", "device_id": "M1", "dx": 1}],
            "placement_nodes": [{"id": "M1"}],
        }
        result = node_command_validator(state)
        assert "1 command" in result["assistant_text"]

    def test_all_rejected_text(self):
        state = {
            "pending_cmds": [{"action": "teleport", "device_id": "M1"}],
            "placement_nodes": [{"id": "M1"}],
        }
        result = node_command_validator(state)
        assert "could not" in result["assistant_text"].lower()

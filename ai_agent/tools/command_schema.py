"""
Command Schema
==============
Single source of truth for command action sets and device-key extraction
helpers shared by the session chat agent, command validator, batch executor
(``cmd_parser.apply_cmds_to_nodes``), and the GUI command handler.

Constants:
- BATCH_SUPPORTED_ACTIONS  : actions implemented in ``apply_cmds_to_nodes``
- GUI_SUPPORTED_ACTIONS    : actions implemented in the GUI ``_handle_ai_command``
- SUPPORTED_COMMAND_ACTIONS: union used by the validator (= GUI set, since
                             chatbot commands flow through visual-review/GUI)

Functions:
- get_cmd_device    : extract a single device ID from any key variant
- get_cmd_device_a  : extract device-A for swap-style commands
- get_cmd_device_b  : extract device-B for swap-style commands
- logical_base_device_id : strip finger suffixes (M1_f0 → M1)
"""

from __future__ import annotations

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Action sets
# ---------------------------------------------------------------------------

#: Actions implemented by ``cmd_parser.apply_cmds_to_nodes`` (batch path).
BATCH_SUPPORTED_ACTIONS: frozenset[str] = frozenset({
    "move", "move_device",
    "swap", "swap_devices",
    "flip", "flip_h", "flip_v",
    "delete",
})

#: Actions implemented by the GUI ``_handle_ai_command`` in layout_tab.py.
GUI_SUPPORTED_ACTIONS: frozenset[str] = frozenset({
    "move", "move_device",
    "swap", "swap_devices",
    "flip", "flip_h", "flip_v",
    "delete",
    "abut",
    "add_dummy", "add_dummies", "dummy",
    "move_row", "move_row_devices",
    "net_priority",
    "wire_width",
    "wire_spacing",
    "net_reroute",
})

#: For session chatbot validation, use GUI actions because chatbot commands
#: are applied through visual-review/GUI flow.
SUPPORTED_COMMAND_ACTIONS: frozenset[str] = GUI_SUPPORTED_ACTIONS


# ---------------------------------------------------------------------------
# Device-key extraction helpers
# ---------------------------------------------------------------------------

def get_cmd_device(cmd: dict) -> Optional[str]:
    """Return the single device ID from *cmd*, checking common key variants.

    Priority order: ``device_id`` > ``device`` > ``id`` > ``name``.
    Returns ``None`` if no key is present.
    """
    return (
        cmd.get("device_id")
        or cmd.get("device")
        or cmd.get("id")
        or cmd.get("name")
    )


def get_cmd_device_a(cmd: dict) -> Optional[str]:
    """Return the first / source device for swap-style commands."""
    return cmd.get("device_a") or cmd.get("a") or cmd.get("source")


def get_cmd_device_b(cmd: dict) -> Optional[str]:
    """Return the second / target device for swap-style commands."""
    return cmd.get("device_b") or cmd.get("b") or cmd.get("target")


# ---------------------------------------------------------------------------
# Finger / logical ID helpers
# ---------------------------------------------------------------------------

#: Regex that strips physical-finger suffixes.
_FINGER_SUFFIX_RE = re.compile(
    r"(?:_f\d+|_m\d+|_finger\d+|__finger\d+|\[\d+\])$", re.IGNORECASE
)


def _is_filler_or_dummy(node_id: str) -> bool:
    """Return True if *node_id* looks like a filler or edge dummy device."""
    upper = str(node_id or "").upper()
    return bool(
        "DUMMY" in upper
        or "FILLER" in upper
        or "EDGE_DUMMY" in upper
        or upper.startswith("FILL_")
    )


def logical_base_device_id(device_id: str) -> str:
    """Strip physical-finger suffixes from *device_id*.

    Examples::

        >>> logical_base_device_id("M1_f0")
        'M1'
        >>> logical_base_device_id("M1_finger0")
        'M1'
        >>> logical_base_device_id("M1[0]")
        'M1'
        >>> logical_base_device_id("M1__finger0")
        'M1'
        >>> logical_base_device_id("M1")
        'M1'
    """
    return _FINGER_SUFFIX_RE.sub("", device_id)

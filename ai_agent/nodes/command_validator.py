"""
Command Validator Node
======================
A LangGraph node that validates candidate layout commands before they reach
the GUI / human_viewer.  Only commands that pass all checks are forwarded
in ``pending_cmds``; invalid commands are stripped and reported in
``validation_errors``.

Functions:
- node_command_validator: Validates pending_cmds and returns sanitised state.
  - Inputs: state (dict)
  - Outputs: state update with pending_cmds, assistant_text,
             validation_errors, validation_warnings.
"""

from __future__ import annotations

from ai_agent.utils.logging import vprint


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Actions the validator considers safe to pass through.
ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "move", "move_device",
    "swap", "swap_devices",
    "flip", "flip_h", "flip_v",
    "delete",
    "align",
    "abut",
    "merge",
    "add_dummy", "add dummy",
    "rotate",
    "move_pair",
})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_node_ids(placement_nodes: list) -> set[str]:
    """Collect all known device IDs from placement nodes."""
    ids: set[str] = set()
    for n in placement_nodes:
        if isinstance(n, dict):
            nid = n.get("id") or n.get("name") or n.get("device_id")
            if nid:
                ids.add(str(nid))
    return ids


def _extract_device_ids(cmd: dict) -> list[str]:
    """Return all device IDs referenced by a single command dict."""
    ids: list[str] = []
    for key in ("device_id", "device", "id", "name"):
        val = cmd.get(key)
        if val:
            ids.append(str(val))
    # Swap-style commands reference two devices
    for key in ("device_a", "a", "device_b", "b"):
        val = cmd.get(key)
        if val:
            ids.append(str(val))
    return ids


def _validate_action(cmd: dict) -> str | None:
    """Return an error string if the action is invalid, else None."""
    if not isinstance(cmd, dict):
        return "Command is not a dict."
    action = str(cmd.get("action", "")).lower().strip()
    if not action:
        return "Command has no 'action' field."
    if action not in ALLOWED_ACTIONS:
        return f"Unknown action '{action}'. Allowed: {sorted(ALLOWED_ACTIONS)}"
    return None


def _validate_device_refs(cmd: dict, known_ids: set[str]) -> str | None:
    """Return an error string if any referenced device is unknown, else None."""
    refs = _extract_device_ids(cmd)
    if not refs:
        # Some actions (e.g. add_dummy) may not reference existing devices.
        return None
    unknown = [r for r in refs if r not in known_ids]
    if unknown:
        return f"Unknown device(s): {', '.join(unknown)}"
    return None


def _detect_symmetry_warning(
    cmd: dict, initial_trace: dict,
) -> str | None:
    """Emit a warning if a command targets a device in a matched group
    without also editing its partner.

    Returns a warning string or None.
    """
    strategy = initial_trace.get("strategy")
    if not strategy or not isinstance(strategy, str):
        return None

    refs = _extract_device_ids(cmd)
    if not refs:
        return None

    # Simple heuristic: if the strategy text mentions "symmetry" or "matched"
    # and the command only targets one device, warn.
    strategy_lower = strategy.lower()
    if "symmetr" not in strategy_lower and "matched" not in strategy_lower:
        return None

    for ref in refs:
        if ref.lower() in strategy_lower:
            return (
                f"Device '{ref}' may be part of a matched/symmetric group. "
                f"Consider editing its partner too."
            )
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def node_command_validator(state: dict) -> dict:
    """Validate candidate layout commands and filter out unsafe ones.

    Valid commands are forwarded in ``pending_cmds``.  Invalid ones are
    stripped and described in ``validation_errors``.  Warnings (e.g.
    symmetry concerns) are reported in ``validation_warnings`` but do
    **not** block the command.

    If no commands survive validation for a ``command_edit`` route, the
    route is downgraded to ``clarify`` so the user gets feedback.
    """
    vprint("[VALIDATOR] Checking pending commands", flush=True)

    commands = (
        state.get("pending_cmds")
        or state.get("session_commands")
        or []
    )
    placement_nodes = state.get("placement_nodes") or state.get("nodes") or []
    initial_trace = state.get("initial_agent_trace") or {}
    known_ids = _get_node_ids(placement_nodes)

    validated: list[dict] = []
    errors: list[str] = []
    warnings: list[str] = []

    for i, cmd in enumerate(commands):
        # --- Action check ---------------------------------------------------
        action_err = _validate_action(cmd)
        if action_err:
            errors.append(f"Command {i + 1}: {action_err}")
            continue

        # --- Device reference check -----------------------------------------
        ref_err = _validate_device_refs(cmd, known_ids)
        if ref_err:
            errors.append(f"Command {i + 1}: {ref_err}")
            continue

        # --- Symmetry warning (non-blocking) --------------------------------
        sym_warn = _detect_symmetry_warning(cmd, initial_trace)
        if sym_warn:
            warnings.append(f"Command {i + 1}: {sym_warn}")

        validated.append(cmd)

    vprint(
        f"[VALIDATOR] {len(validated)} valid, {len(errors)} rejected, "
        f"{len(warnings)} warnings",
        flush=True,
    )

    # --- Build output -------------------------------------------------------
    update: dict = {
        "pending_cmds": validated,
        "validation_errors": errors,
        "validation_warnings": warnings,
    }

    if validated:
        n = len(validated)
        update["assistant_text"] = (
            f"{n} command(s) validated and ready for review."
        )
    elif errors:
        # All commands failed — downgrade to clarify
        update["assistant_text"] = (
            "I could not safely convert that request into layout commands. "
            + " ".join(errors[:3])
        )
        update["session_route"] = "clarify"
    else:
        # command_edit route but no commands at all
        route = state.get("session_route")
        if route == "command_edit":
            update["assistant_text"] = (
                "No layout commands were generated. "
                "Please specify the device and the action you'd like."
            )
            update["session_route"] = "clarify"
        else:
            update["assistant_text"] = state.get("assistant_text") or "Done."

    return update

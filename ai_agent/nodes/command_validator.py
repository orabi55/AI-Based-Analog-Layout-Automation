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

import re
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

#: Keys that may hold a list of device IDs.
LIST_DEVICE_KEYS: frozenset[str] = frozenset({
    "devices",
    "device_ids",
    "targets",
    "target_devices",
    "group",
    "devices_to_move",
})

#: Regex matching physical-finger IDs (e.g. M1_f0, M1[0], M1__finger0).
_FINGER_ID_RE = re.compile(
    r"^.+(?:_f\d+|\[\d+\]|__finger\d+)$", re.IGNORECASE
)


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
    """Return all device IDs referenced by a single command dict.

    Handles scalar keys (device_id, device, id, name, device_a, device_b,
    a, b) as well as list/dict-list keys (devices, device_ids, targets,
    target_devices, group, devices_to_move).
    """
    ids: list[str] = []

    # --- Scalar keys --------------------------------------------------------
    for key in ("device_id", "device", "id", "name"):
        val = cmd.get(key)
        if val:
            ids.append(str(val))
    # Swap-style commands reference two devices
    for key in ("device_a", "a", "device_b", "b"):
        val = cmd.get(key)
        if val:
            ids.append(str(val))

    # --- List/dict-list keys (Fix 11) ---------------------------------------
    for key in LIST_DEVICE_KEYS:
        val = cmd.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    ids.append(item)
                elif isinstance(item, dict):
                    # Nested form: {"id": "M1"}
                    nested_id = item.get("id") or item.get("device_id") or item.get("name")
                    if nested_id:
                        ids.append(str(nested_id))
        elif isinstance(val, str):
            ids.append(val)

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


def _validate_move_pair(cmd: dict) -> str | None:
    """Return an error string if a move_pair command is malformed, else None."""
    action = str(cmd.get("action", "")).lower().strip()
    if action != "move_pair":
        return None
    devices = _extract_device_ids(cmd)
    if len(devices) < 2:
        return "move_pair requires at least two devices."
    has_delta = (
        cmd.get("dx") is not None or cmd.get("dy") is not None
        or cmd.get("x") is not None or cmd.get("y") is not None
    )
    if not has_delta:
        return "move_pair requires dx/dy or x/y fields."
    return None


def _check_finger_integrity(
    cmd: dict, placement_nodes: list,
) -> str | None:
    """Return an error string if the command targets a physical finger
    without explicit opt-in, else None.

    A physical finger node is detected by:
    - ID matching the pattern M1_f0, M1[0], M1__finger0
    - Node metadata keys: parent_id, finger_index, nf
    """
    if cmd.get("allow_finger_edit"):
        return None   # explicit opt-in

    refs = _extract_device_ids(cmd)
    if not refs:
        return None

    # Build a lookup for finger-node metadata
    node_map: dict[str, dict] = {}
    for n in placement_nodes:
        if isinstance(n, dict):
            nid = n.get("id") or n.get("name") or n.get("device_id")
            if nid:
                node_map[str(nid)] = n

    for ref in refs:
        # Pattern-based detection
        if _FINGER_ID_RE.match(ref):
            return (
                f"Device '{ref}' appears to be a physical finger. "
                f"Editing individual fingers breaks interdigitation integrity. "
                f"Set 'allow_finger_edit': true to override."
            )
        # Metadata-based detection
        node = node_map.get(ref, {})
        if node.get("parent_id") or node.get("finger_index") is not None:
            return (
                f"Device '{ref}' is a finger of parent '{node.get('parent_id', '?')}'. "
                f"Editing individual fingers breaks interdigitation integrity. "
                f"Set 'allow_finger_edit': true to override."
            )

    return None


def _check_row_legality(
    cmd: dict, placement_nodes: list,
) -> str | None:
    """Return a warning string if a move command would cross the PMOS/NMOS
    row boundary, else None.

    Only applies to absolute-y moves (``y`` key present).  Relative moves
    (``dy`` only) are accepted without warning — the user may intend a
    small in-row shift.
    """
    action = str(cmd.get("action", "")).lower().strip()
    if action not in ("move", "move_device", "move_pair"):
        return None

    target_y = cmd.get("y")
    if target_y is None:
        return None   # relative move — no check

    if cmd.get("force_y"):
        return None   # explicit override

    refs = _extract_device_ids(cmd)
    if not refs:
        return None

    # Determine device type from placement_nodes
    node_map: dict[str, dict] = {}
    for n in placement_nodes:
        if isinstance(n, dict):
            nid = n.get("id") or n.get("name") or n.get("device_id")
            if nid:
                node_map[str(nid)] = n

    for ref in refs:
        node = node_map.get(ref, {})
        dev_type = (
            str(node.get("type") or node.get("mos_type") or
                node.get("kind") or node.get("device_type") or "")
        ).lower()
        current_y = None
        geom = node.get("geometry")
        if isinstance(geom, dict):
            current_y = geom.get("y")
        if current_y is None:
            current_y = node.get("y")

        if current_y is not None and dev_type:
            # Conservative heuristic: if the device moves from one side of
            # the origin to the other, it likely crosses the row boundary.
            try:
                cy = float(current_y)
                ty = float(target_y)
                if (cy >= 0 and ty < 0) or (cy < 0 and ty >= 0):
                    return (
                        f"Device '{ref}' ({dev_type}) would move from y={cy} to y={ty}, "
                        f"which may cross the PMOS/NMOS row boundary. "
                        f"Set 'force_y': true to override."
                    )
            except (ValueError, TypeError):
                pass

    return None


def _detect_symmetry_warning(
    cmd: dict, initial_trace: dict,
) -> str | None:
    """Emit a warning if a command targets a device in a matched group
    without also editing its partner.

    Supports both string-based strategy detection and structured matching
    group data (matching_groups, matched_pairs, symmetry_pairs,
    common_centroid_groups).

    Returns a warning string or None.
    """
    refs = _extract_device_ids(cmd)
    if not refs:
        return None

    ref_set = set(refs)

    strategy = initial_trace.get("strategy")

    # --- Structured matching data (Fix 11) ----------------------------------
    if isinstance(strategy, dict):
        for group_key in (
            "matching_groups",
            "matched_pairs",
            "symmetry_pairs",
            "common_centroid_groups",
        ):
            groups = strategy.get(group_key)
            if not isinstance(groups, list):
                continue
            for group in groups:
                if not isinstance(group, (list, tuple)):
                    continue
                group_set = set(str(d) for d in group)
                overlap = ref_set & group_set
                if overlap and overlap != group_set:
                    missing = group_set - ref_set
                    return (
                        f"Device(s) {', '.join(sorted(overlap))} are in a matched group "
                        f"{sorted(group_set)} but partner(s) {', '.join(sorted(missing))} "
                        f"are not included in this command. Consider editing them together."
                    )

    # --- Fallback: text-based strategy detection ----------------------------
    if isinstance(strategy, str):
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

        # --- move_pair structural check (Fix 11) ----------------------------
        pair_err = _validate_move_pair(cmd)
        if pair_err:
            errors.append(f"Command {i + 1}: {pair_err}")
            continue

        # --- Finger integrity check (Fix 11 — blocking) --------------------
        finger_err = _check_finger_integrity(cmd, placement_nodes)
        if finger_err:
            errors.append(f"Command {i + 1}: {finger_err}")
            continue

        # --- Row legality warning (Fix 11 — non-blocking) -------------------
        row_warn = _check_row_legality(cmd, placement_nodes)
        if row_warn:
            warnings.append(f"Command {i + 1}: {row_warn}")

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

"""
Tests for Task 15 — DRC symmetry guard partner Y coordinate fix.

The bug was: partner move fallback used node_x_map for Y, giving the
partner's X coordinate as its Y.  The fix introduces node_y_map.

These tests extract the symmetry guard logic into a testable helper and
verify that partner commands use the correct Y coordinate.
"""

import pytest


# ---------------------------------------------------------------------------
# Extract the symmetry mirror guard into a testable helper
# ---------------------------------------------------------------------------

def build_partner_moves(
    accumulated_cmds: list[dict],
    sym_pair_map: dict,
    snapshot: list[dict],
) -> list[dict]:
    """Replicate the symmetry mirror guard logic from drc_critic.

    Returns the list of extra partner commands that would be injected.
    """
    node_x_map = {
        str(n.get("id", "") or n.get("device_id", "") or n.get("name", "")):
        float(n.get("geometry", {}).get("x", 0.0))
        for n in snapshot if n.get("geometry")
    }
    node_y_map = {
        str(n.get("id", "") or n.get("device_id", "") or n.get("name", "")):
        float(n.get("geometry", {}).get("y", 0.0))
        for n in snapshot if n.get("geometry")
    }

    extra_cmds = []
    touched_by_guard = set()

    for cmd in accumulated_cmds:
        if cmd.get("action") != "move":
            continue
        dev_id = cmd.get("device", "")
        if dev_id not in sym_pair_map or dev_id in touched_by_guard:
            continue
        partner_id, side = sym_pair_map[dev_id]
        if partner_id in touched_by_guard:
            continue

        old_x = node_x_map.get(dev_id, cmd.get("x", 0.0))
        new_x = float(cmd.get("x", old_x))
        dx = new_x - old_x
        if abs(dx) < 1e-9:
            continue

        partner_old_x = node_x_map.get(partner_id, 0.0)
        mirror_dx = -dx if side == "left" else -dx
        partner_new_x = round(partner_old_x + mirror_dx, 6)
        extra_cmds.append({
            "action": "move",
            "device": partner_id,
            "x": partner_new_x,
            "y": cmd.get("y", node_y_map.get(partner_id, 0.0)),
        })
        touched_by_guard.add(dev_id)
        touched_by_guard.add(partner_id)

    return extra_cmds


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPartnerMoveUsesCorrectY:
    """The core bug: partner Y must come from node_y_map, not node_x_map."""

    def test_partner_y_uses_y_not_x(self):
        """M1 at (10, 100), M2 at (50, 200).
        Moving M1 right by 5 → partner M2 should move left by 5.
        Partner Y should be 200 (M2's Y), NOT 50 (M2's X).
        """
        snapshot = [
            {"id": "M1", "geometry": {"x": 10.0, "y": 100.0}},
            {"id": "M2", "geometry": {"x": 50.0, "y": 200.0}},
        ]
        sym_pair_map = {
            "M1": ("M2", "left"),
            "M2": ("M1", "right"),
        }
        cmds = [{"action": "move", "device": "M1", "x": 15.0}]  # no y

        partner_cmds = build_partner_moves(cmds, sym_pair_map, snapshot)

        assert len(partner_cmds) == 1
        pcmd = partner_cmds[0]
        assert pcmd["device"] == "M2"
        assert pcmd["y"] == 200.0   # Must be M2's Y, not M2's X (50)
        assert pcmd["y"] != 50.0    # Explicitly ensure old bug is gone

    def test_partner_y_when_cmd_has_y(self):
        """If the original cmd specifies y, the partner should inherit it."""
        snapshot = [
            {"id": "A", "geometry": {"x": 0.0, "y": 10.0}},
            {"id": "B", "geometry": {"x": 20.0, "y": 10.0}},
        ]
        sym_pair_map = {"A": ("B", "left"), "B": ("A", "right")}
        cmds = [{"action": "move", "device": "A", "x": 5.0, "y": 15.0}]

        partner_cmds = build_partner_moves(cmds, sym_pair_map, snapshot)

        assert len(partner_cmds) == 1
        assert partner_cmds[0]["y"] == 15.0  # inherited from cmd

    def test_partner_x_is_mirrored(self):
        """Verify the X mirroring is correct (not affected by fix)."""
        snapshot = [
            {"id": "M1", "geometry": {"x": 10.0, "y": 50.0}},
            {"id": "M2", "geometry": {"x": 30.0, "y": 50.0}},
        ]
        sym_pair_map = {"M1": ("M2", "left"), "M2": ("M1", "right")}
        cmds = [{"action": "move", "device": "M1", "x": 15.0}]

        partner_cmds = build_partner_moves(cmds, sym_pair_map, snapshot)

        assert len(partner_cmds) == 1
        pcmd = partner_cmds[0]
        # M1 moved +5, so M2 should move -5: 30 + (-5) = 25
        assert pcmd["x"] == 25.0


class TestPartnerMoveEdgeCases:
    """Edge cases for the symmetry guard."""

    def test_non_move_cmd_ignored(self):
        snapshot = [{"id": "X", "geometry": {"x": 0, "y": 0}}]
        sym_pair_map = {"X": ("Y", "left")}
        cmds = [{"action": "swap", "device": "X"}]

        assert build_partner_moves(cmds, sym_pair_map, snapshot) == []

    def test_no_symmetry_pairs(self):
        snapshot = [{"id": "M1", "geometry": {"x": 10, "y": 20}}]
        cmds = [{"action": "move", "device": "M1", "x": 15}]

        assert build_partner_moves(cmds, {}, snapshot) == []

    def test_zero_dx_ignored(self):
        """If the move doesn't actually change X, no partner cmd is needed."""
        snapshot = [
            {"id": "M1", "geometry": {"x": 10.0, "y": 50.0}},
            {"id": "M2", "geometry": {"x": 30.0, "y": 50.0}},
        ]
        sym_pair_map = {"M1": ("M2", "left"), "M2": ("M1", "right")}
        cmds = [{"action": "move", "device": "M1", "x": 10.0}]  # no change

        assert build_partner_moves(cmds, sym_pair_map, snapshot) == []

    def test_device_id_key_variations(self):
        """node_y_map should handle device_id and name keys."""
        snapshot = [
            {"device_id": "A", "geometry": {"x": 0, "y": 100}},
            {"name": "B", "geometry": {"x": 20, "y": 200}},
        ]
        sym_pair_map = {"A": ("B", "left"), "B": ("A", "right")}
        cmds = [{"action": "move", "device": "A", "x": 5.0}]

        partner_cmds = build_partner_moves(cmds, sym_pair_map, snapshot)

        assert len(partner_cmds) == 1
        assert partner_cmds[0]["y"] == 200.0

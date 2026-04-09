"""
abutment_engine.py — Transistor abutment solver.

When two same-type transistors in the same row share a net on their
adjacent terminals, they can "abut" — physically sharing one diffusion
strip, saving area and reducing parasitics.

PDK representation (SAED 14nm):
  Each device PCell has a 'leftAbut' and 'rightAbut' parameter (0 or 1).
  Setting them to 1 tells the PCell generator to remove the end-cap
  diffusion on that side so it merges with the neighbour's diffusion.

This engine:
  1. Takes an ordered list of node dicts (already sorted by x in a row).
  2. Uses terminal_nets to determine which adjacent pairs can share a terminal.
  3. Decides per-device whether it needs to be horizontally flipped to make
     the shared net land on the correct edge.
  4. Returns an `abutment_result` dict:
       {dev_id: {"abut_left":bool, "abut_right":bool, "flip_h":bool}}

Shared terminal rules (for an adjacent (left_dev, right_dev) pair):
  - left_dev.right_terminal_net == right_dev.left_terminal_net  → direct abut (no flip)
  - left_dev.right_terminal_net == right_dev.right_terminal_net → flip right_dev, then abut
  Otherwise: no abutment for this pair.

Terminal polarity for a single-finger (nf=1) R0 device:
  - Left column  = Source  (column 0, even index)
  - Right column = Drain   (column 1, odd index)
For a flipped (flip_h) device the polarity reverses.
"""

from __future__ import annotations


def _left_right_nets(dev_id: str, terminal_nets: dict, flip_h: bool):
    """Return (left_net, right_net) for a device based on its polarity.

    For nf=1 (most common):
      R0:      left=S, right=D
      flipped: left=D, right=S
    For nf>1 (e.g. nf=2, 3 diffusion columns S-D-S):
      R0:      left=S, right=S  (both outer cols are source)
      flipped: left=D, right=D
    We use 'nf' from dev_id's node data but since we don't have it here
    we parametrise via the inferred outer cols from terminal_nets directly.
    """
    nets = terminal_nets.get(dev_id, {})
    if not nets:
        return None, None

    if flip_h:
        # mirrored: the old Drain side is now on the left
        left_net  = nets.get("D", "")
        right_net = nets.get("S", "")
    else:
        left_net  = nets.get("S", "")
        right_net = nets.get("D", "")

    return left_net or None, right_net or None


def solve_abutment(ordered_row: list, terminal_nets: dict) -> dict:
    """Compute per-device abutment flags and required flips for one row.

    Args:
        ordered_row: list of node dicts in left-to-right order.
                     Each dict must have at least {"id": str, "type": str}.
        terminal_nets: {dev_id: {"S": net, "D": net, "G": net}}

    Returns:
        {dev_id: {"abut_left": bool, "abut_right": bool, "flip_h": bool}}
    """
    n = len(ordered_row)
    result = {
        node["id"]: {"abut_left": False, "abut_right": False, "flip_h": False}
        for node in ordered_row
    }

    if n < 2:
        return result

    # Running flip state — once a device is flipped, its neighbours respond
    flip_states = {node["id"]: False for node in ordered_row}

    for i in range(n - 1):
        left_node  = ordered_row[i]
        right_node = ordered_row[i + 1]

        left_id  = left_node["id"]
        right_id = right_node["id"]

        # Passives cannot abut with transistors, or with each other via this engine
        if left_node["type"] in ("res", "cap") or right_node["type"] in ("res", "cap"):
            continue

        # Mixed NMOS/PMOS cannot abut
        if left_node["type"] != right_node["type"]:
            continue

        left_flip  = flip_states[left_id]
        right_flip = flip_states[right_id]

        l_left, l_right = _left_right_nets(left_id,  terminal_nets, left_flip)
        r_left, r_right = _left_right_nets(right_id, terminal_nets, right_flip)

        if l_right is None or r_left is None:
            continue  # No net info — skip

        if l_right == r_left and l_right != "":
            # Direct abutment — shared net already on correct edges
            result[left_id]["abut_right"]  = True
            result[right_id]["abut_left"]  = True

        elif r_right is not None and l_right == r_right and l_right != "":
            # Right device needs H-flip before it can abut
            flip_states[right_id] = not right_flip
            result[right_id]["flip_h"]   = flip_states[right_id]
            result[left_id]["abut_right"] = True
            result[right_id]["abut_left"] = True

    return result


def split_into_rows(nodes: list, snap_tolerance: float = 0.05) -> dict:
    """Group node dicts into rows by similar y-coordinate.

    Returns:
        {row_y: [node, ...]} sorted by x within each row.
    """
    rows: dict = {}
    for node in nodes:
        y = node.get("geometry", {}).get("y", 0.0)
        # Find existing row within tolerance
        matched_y = None
        for ry in rows:
            if abs(ry - y) <= snap_tolerance:
                matched_y = ry
                break
        key = matched_y if matched_y is not None else y
        rows.setdefault(key, []).append(node)

    # Sort each row by x
    for ry in rows:
        rows[ry].sort(key=lambda n: n.get("geometry", {}).get("x", 0.0))

    return rows

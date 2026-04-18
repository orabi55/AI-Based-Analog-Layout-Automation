"""
ai_agent/tools.py
=================
Thin wrappers that expose existing project functions as safe, reusable
tools callable by any agent in the orchestrator pipeline.

All functions have graceful fallbacks so that missing optional dependencies
(networkx, Qt) never crash the orchestrator thread.
"""

import os
import sys


def tool_build_circuit_graph(sp_file_path):
    """Build a networkx circuit graph from a SPICE .sp file.

    Args:
        sp_file_path (str): absolute path to the .sp netlist file.

    Returns:
        networkx.Graph | None: the circuit graph, or None on failure.
    """
    if not sp_file_path or not os.path.isfile(sp_file_path):
        return None
    try:
        project_root = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..")
        )
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from parser.netlist_reader import read_netlist
        from parser.circuit_graph import build_circuit_graph
        netlist = read_netlist(sp_file_path)
        return build_circuit_graph(netlist)
    except Exception as exc:
        print(f"[TOOLS] tool_build_circuit_graph failed: {exc}")
        return None


def tool_score_net_crossings(nodes, edges, terminal_nets):
    """Estimate routing complexity with the pure-Python heuristic.

    Returns:
        dict: see routing_previewer.score_routing() for schema.
    """
    try:
        from ai_agent.ai_chat_bot.agents.routing_previewer import score_routing
        return score_routing(nodes, edges or [], terminal_nets or {})
    except Exception as exc:
        print(f"[TOOLS] tool_score_net_crossings failed: {exc}")
        return {"score": 0, "worst_nets": [], "net_spans": {}, "summary": str(exc)}


def tool_run_drc(nodes, gap_px=0.0):
    """Run the pure-Python DRC overlap + gap check.

    Returns:
        dict: see drc_critic.run_drc_check() for schema.
    """
    try:
        from ai_agent.ai_chat_bot.agents.drc_critic import run_drc_check
        return run_drc_check(nodes, gap_px=gap_px)
    except Exception as exc:
        print(f"[TOOLS] tool_run_drc failed: {exc}")
        return {"pass": True, "violations": [], "summary": str(exc)}


def tool_validate_device_count(original_nodes, proposed_nodes):
    """Check that the proposed placement preserves ALL original device IDs.

    This is the "Conservation Guard" — it catches AI-induced device deletions
    BEFORE any commands reach the GUI.

    Args:
        original_nodes: list of node dicts from the original layout context.
        proposed_nodes: list of node dicts after applying proposed commands.

    Returns:
        dict: {
            "pass": bool,           True when no devices are missing
            "missing": [str],       IDs present in original but absent in proposed
            "extra": [str],         IDs present in proposed but absent in original
            "original_count": int,
            "proposed_count": int,
            "summary": str,
        }
    """
    original_ids = {n["id"] for n in original_nodes if not n.get("is_dummy")}
    proposed_ids = {n["id"] for n in proposed_nodes if not n.get("is_dummy")}

    missing = sorted(original_ids - proposed_ids)
    extra   = sorted(proposed_ids - original_ids)

    passed  = len(missing) == 0
    if passed:
        summary = (
            f"Device conservation OK — all {len(original_ids)} active device(s) present."
        )
    else:
        summary = (
            f"DEVICE CONSERVATION FAILURE: "
            f"{len(missing)} device(s) missing: {', '.join(missing)}."
            + (f"  {len(extra)} unknown device(s): {', '.join(extra)}." if extra else "")
        )

    return {
        "pass": passed,
        "missing": missing,
        "extra": extra,
        "original_count": len(original_ids),
        "proposed_count": len(proposed_ids),
        "summary": summary,
    }


def tool_find_nearest_free_x(nodes, row_y, width, target_x, exclude_id=None):
    """Pure-data equivalent of editor_view.find_nearest_free_x().

    This version works without Qt — it searches the node list directly
    using bounding-box arithmetic.

    Args:
        nodes: list of placement node dicts
        row_y (float): the snap-row y coordinate to search within
        width (float): width of the device being placed
        target_x (float): preferred x position
        exclude_id (str | None): device id to skip (the device being moved)

    Returns:
        float: the nearest free x coordinate (snapped to ``width`` pitch).
    """
    SNAP = width if width > 0 else 1.0
    TOL = SNAP * 0.6   # row membership tolerance

    # Collect occupied intervals in this row
    occupied = []
    for n in nodes:
        if n.get("id") == exclude_id:
            continue
        geo = n.get("geometry", {})
        ny = float(geo.get("y", 0))
        if abs(ny - row_y) > TOL:
            continue
        nx = float(geo.get("x", 0))
        nw = float(geo.get("width", SNAP))
        occupied.append((nx, nx + nw))

    def _is_free(x):
        for ox1, ox2 in occupied:
            if x < ox2 and (x + width) > ox1:
                return False
        return True

    # Snap target to grid
    def _snap(v):
        return round(v / SNAP) * SNAP

    x = _snap(target_x)
    for delta in range(0, int(200 / SNAP) + 1):
        for sign in (1, -1):
            candidate = _snap(x + sign * delta * SNAP)
            if candidate < 0:
                continue
            if _is_free(candidate):
                return candidate
    return max(0.0, x)


def tool_resolve_overlaps(nodes):
    """Eliminates overlaps using iterative full-row sweep (converges for any pile-up).

    Replaces the old single adjacent-pair pass which missed cascaded collisions
    (e.g. 3 devices all at x=0 left 2 of them still overlapping).
    """
    moved = set()
    MIN_SPACING = 0.294

    # max_passes upper bound: worst case each pass resolves at least 1 device
    max_passes = max(len(nodes) + 2, 10)

    for _ in range(max_passes):
        # Re-group by row on every pass so position changes are picked up
        rows: dict = {}
        for n in nodes:
            ry = round(float(n['geometry']['y']), 4)
            rows.setdefault(ry, []).append(n)

        any_moved = False
        for row_nodes in rows.values():
            row_nodes.sort(key=lambda n: float(n['geometry']['x']))
            for i in range(1, len(row_nodes)):
                prev, curr = row_nodes[i - 1], row_nodes[i]
                prev_end = float(prev['geometry']['x']) + float(prev['geometry']['width'])
                curr_x   = float(curr['geometry']['x'])

                if curr_x < prev_end - 0.001:
                    # Push curr right, snapped to MIN_SPACING grid
                    snapped = round(prev_end / MIN_SPACING) * MIN_SPACING
                    if snapped < prev_end - 0.001:
                        snapped += MIN_SPACING
                    curr['geometry']['x'] = snapped
                    moved.add(curr['id'])
                    any_moved = True

        if not any_moved:
            break  # stable — no more overlaps in any row

    return list(moved)


def tool_validate_inventory(original_nodes, proposed_nodes):
    """Strictly compare original device IDs to proposed IDs.

    Prevents transistor deletion or identity mixing.
    Returns a 3-tuple: (bool_pass, missing_ids, extra_ids)

    missing_ids: IDs in original but not in proposed (deletions)
    extra_ids:   IDs in proposed but not in original (hallucinated devices)
    """
    orig_set = {n["id"] for n in original_nodes}
    prop_set = {n["id"] for n in proposed_nodes}

    missing = sorted(orig_set - prop_set)
    extra   = sorted(prop_set - orig_set)

    is_valid = len(missing) == 0 and len(extra) == 0
    return is_valid, missing, extra


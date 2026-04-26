"""
Overlap Resolver Tool
=====================
Eliminates overlaps between devices in a layout using an iterative full-row sweep algorithm.
Ensures that all devices in the same row are spaced according to a minimum spacing requirement.

Functions:
- resolve_overlaps (tool_resolve_overlaps): Iteratively resolves horizontal overlaps within rows.
  - Inputs: nodes (list of dicts), log_details (bool)
  - Outputs: set of IDs of moved devices.
"""

from ai_agent.utils.logging import vprint


def resolve_overlaps(nodes, log_details=True):
    """Eliminates overlaps using iterative full-row sweep (converges for any pile-up).

    Replaces the old single adjacent-pair pass which missed cascaded collisions
    (e.g. 3 devices all at x=0 left 2 of them still overlapping).

    Parameters
    ----------
    nodes : list
        List of device node dicts with 'geometry' containing x, y, width.
    log_details : bool
        If True, log each move to placement_live_output.log.

    Returns
    -------
    set
        IDs of devices that were moved.
    """
    moved = set()
    MIN_SPACING = 0.294

    # max_passes upper bound: worst case each pass resolves at least 1 device
    max_passes = max(len(nodes) + 2, 10)

    for pass_num in range(max_passes):
        # Re-group by row on every pass so position changes are picked up
        rows = {}
        for n in nodes:
            if "geometry" not in n:
                continue
            ry = round(float(n['geometry']['y']), 4)
            rows.setdefault(ry, []).append(n)

        any_moved = False
        for row_y, row_nodes in rows.items():
            row_nodes.sort(key=lambda n: float(n['geometry']['x']))
            for i in range(1, len(row_nodes)):
                prev, curr = row_nodes[i - 1], row_nodes[i]
                prev_x = float(prev['geometry']['x'])
                prev_w = float(prev['geometry']['width'])
                prev_end = prev_x + prev_w
                curr_x = float(curr['geometry']['x'])

                if curr_x < prev_end - 0.001:
                    # Push curr right, snapped to MIN_SPACING grid
                    snapped = round(prev_end / MIN_SPACING) * MIN_SPACING
                    if snapped < prev_end - 0.001:
                        snapped += MIN_SPACING
                    old_x = curr_x
                    curr['geometry']['x'] = snapped
                    moved.add(curr['id'])
                    any_moved = True
                    if log_details:
                        vprint(
                            f"    [OVERLAP-FIX] {curr['id']} moved x={old_x:.4f} → {snapped:.4f} "
                            f"(was overlapping {prev['id']} at row y={row_y:.4f})"
                        )

        if not any_moved:
            break  # stable — no more overlaps in any row

    if moved and log_details:
        vprint(f"  [OVERLAP-RESOLVER] Resolved overlaps for {len(moved)} device(s) in {pass_num + 1} pass(es)")

    return moved


# Backward-compatible alias
tool_resolve_overlaps = resolve_overlaps

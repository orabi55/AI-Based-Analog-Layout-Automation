"""
Positioning Tool
================
Provides utilities for finding legal device positions within a layout, 
ensuring no overlaps and adherence to grid snapping.

Functions:
- find_nearest_free_x (tool_find_nearest_free_x): Searches for the nearest available x-coordinate in a row.
  - Inputs: nodes (list), row_y (float), width (float), target_x (float), exclude_id (optional str)
  - Outputs: nearest free x coordinate (float).
"""


def find_nearest_free_x(nodes, row_y, width, target_x, exclude_id=None):
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


# Backward-compatible alias
tool_find_nearest_free_x = find_nearest_free_x

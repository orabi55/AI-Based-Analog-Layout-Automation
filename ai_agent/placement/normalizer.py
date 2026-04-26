"""
Placement Normalization Utilities
=================================
Provides utilities for normalizing and restoring coordinates in the layout, 
ensuring consistency across different extraction frames.

Functions:
- _normalise_coords: Shifts Y-coordinates so the minimum value is 0.0.
  - Inputs: nodes (list)
  - Outputs: tuple (normalised_nodes, y_offset).
- _restore_coords: Reverses the Y-coordinate shift.
  - Inputs: placed_nodes (list), y_offset (float)
  - Outputs: list of restored node dictionaries.
- _ensure_placement_dict: Normalizes LLM JSON output into a consistent dictionary structure.
  - Inputs: parsed (dict or list)
  - Outputs: dictionary with a "nodes" key.
"""

import copy


def _normalise_coords(nodes: list) -> tuple:
    """
    Shift all node Y-coordinates uniformly so that the minimum Y value is 0.0.

    This ensures that components always sit on or above the positive Y-axis,
    normalizing topologies plotted in negative coordinate space (e.g. from
    certain extraction tools) before passing them to the AI.

    Parameters
    ----------
    nodes : list
        List of node dictionaries with absolute 'geometry' coordinates.

    Returns
    -------
    tuple
        A 2-tuple `(normalised_nodes, y_offset)` where:
        - `normalised_nodes` is a deep copy of the original nodes with shifted Y values.
        - `y_offset` is the float amount that was ADDED to the original coordinates.
    """
    if not nodes:
        return nodes, 0.0

    all_ys = [n.get("geometry", {}).get("y", 0.0) for n in nodes if "geometry" in n]
    if not all_ys:
        return nodes, 0.0

    min_y    = min(all_ys)
    y_offset = -min_y

    if abs(y_offset) < 1e-9:
        return nodes, 0.0

    normalised = copy.deepcopy(nodes)
    for n in normalised:
        geo = n.get("geometry", {})
        if "y" in geo:
            geo["y"] = round(geo["y"] + y_offset, 6)

    return normalised, y_offset


def _restore_coords(placed_nodes: list, y_offset: float) -> list:
    """
    Un-shift Y coordinates back to their original extraction frame.

    This reverses the arithmetic applied by `_normalise_coords` so that the
    AI's layout strictly aligns with the source schematic's bounding box logic.

    Parameters
    ----------
    placed_nodes : list
        List of node dictionaries with AI-assigned coordinates.
    y_offset : float
        The float amount originally added by `_normalise_coords`.

    Returns
    -------
    list
        A deep copy of the `placed_nodes` with Y coordinates shifted downwards
        by `y_offset`.
    """
    if abs(y_offset) < 1e-9:
        return placed_nodes
    restored = copy.deepcopy(placed_nodes)
    for n in restored:
        geo = n.get("geometry", {})
        if "y" in geo:
            geo["y"] = round(geo["y"] - y_offset, 6)
    return restored


def _ensure_placement_dict(parsed) -> dict:
    """
    Normalize the result of sanitize_json() to always be a dictionary
    containing a "nodes" key.

    Different LLMs might return a bare list `[{}, {}]` or nest the nodes under
    keys like "placement", "result", or "layout", instead of the expected
    `{"nodes": [{}, {}]}` structure. This function enforces consistency.

    Parameters
    ----------
    parsed : dict | list
        The raw Python object obtained after successfully parsing the LLM JSON.

    Returns
    -------
    dict
        A dictionary strictly containing a "nodes" key mapping to a list.

    Raises
    ------
    ValueError
        If the parsed object structure is entirely unrecognized.
    """
    if isinstance(parsed, list):
        return {"nodes": parsed}
    if isinstance(parsed, dict):
        if "nodes" not in parsed:
            for key in ("placement", "result", "layout", "devices", "placements"):
                if key in parsed and isinstance(parsed[key], list):
                    return {"nodes": parsed[key]}
        return parsed
    raise ValueError(f"Unexpected JSON type from LLM: {type(parsed).__name__}.")

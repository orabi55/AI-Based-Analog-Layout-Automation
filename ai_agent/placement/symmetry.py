"""
Symmetry Enforcement
====================
Provides utilities for enforcing vertical-axis reflection symmetry for matched 
device pairs in a layout.

Functions:
- enforce_reflection_symmetry: Ensures that matched block pairs are positioned as mirror images.
  - Inputs: nodes (list)
  - Outputs: list of symmetry-corrected node dictionaries.
"""

from ai_agent.utils.logging import vprint


def enforce_reflection_symmetry(nodes: list) -> list:
    """
    Enforce vertical-axis reflection symmetry for matched block pairs.

    For every pair of matched blocks that are in the same row (e.g.,
    cross-coupled NMOS latch pair, differential pair loads), this function
    computes the center axis of the row and ensures the two blocks are
    placed as perfect mirror images. It works on both logical block nodes
    and physical finger nodes.
    """
    if not nodes:
        return nodes

    # Find matched blocks
    _REFLECT_TECHNIQUES = {"symmetric_cross_coupled", "ABBA_diff_pair", "ABAB_load_pair"}

    # Group nodes by row (Y value)
    row_buckets: dict = {}
    for n in nodes:
        y = round(float(n.get("geometry", {}).get("y", 0.0)), 3)
        row_buckets.setdefault(y, []).append(n)

    for y_key, row_nodes in row_buckets.items():
        # Identify nodes that belong to a matching technique
        matched_in_row = [
            n for n in row_nodes
            if n.get("_matched_block") and n.get("_technique") in _REFLECT_TECHNIQUES
        ]

        if not matched_in_row:
            continue

        # Group by technique, then by block_id
        tech_groups: dict = {}
        for n in matched_in_row:
            tech = n.get("_technique", "")
            # Use _block_id if it's physical fingers, otherwise the node ID itself is the block
            block_id = n.get("_block_id", n["id"])

            if tech not in tech_groups:
                tech_groups[tech] = {}
            if block_id not in tech_groups[tech]:
                tech_groups[tech][block_id] = []
            tech_groups[tech][block_id].append(n)

        for tech, blocks_dict in tech_groups.items():
            block_ids = list(blocks_dict.keys())
            if len(block_ids) != 2:
                continue  # Need exactly two blocks for reflection

            block_a_id, block_b_id = block_ids[0], block_ids[1]
            nodes_a = blocks_dict[block_a_id]
            nodes_b = blocks_dict[block_b_id]

            # Compute bounding box for Block A
            xs_a = [float(n.get("geometry", {}).get("x", 0.0)) for n in nodes_a]
            ends_a = [float(n.get("geometry", {}).get("x", 0.0)) + float(n.get("geometry", {}).get("width", 0.294)) for n in nodes_a]
            min_x_a, max_x_a = min(xs_a), max(ends_a)
            center_a = (min_x_a + max_x_a) / 2.0

            # Compute bounding box for Block B
            xs_b = [float(n.get("geometry", {}).get("x", 0.0)) for n in nodes_b]
            ends_b = [float(n.get("geometry", {}).get("x", 0.0)) + float(n.get("geometry", {}).get("width", 0.294)) for n in nodes_b]
            min_x_b, max_x_b = min(xs_b), max(ends_b)
            center_b = (min_x_b + max_x_b) / 2.0

            # Axis of symmetry
            axis = (center_a + center_b) / 2.0

            # New centers
            new_center_a = 2 * axis - center_a
            new_center_b = 2 * axis - center_b

            # Shift amounts
            shift_a = new_center_a - center_a
            shift_b = new_center_b - center_b

            # Apply shifts
            for n in nodes_a:
                n["geometry"]["x"] = round(float(n["geometry"]["x"]) + shift_a, 6)
            for n in nodes_b:
                n["geometry"]["x"] = round(float(n["geometry"]["x"]) + shift_b, 6)

            vprint(f"[REFLECT] {block_a_id} <-> {block_b_id} "
                   f"axis={axis:.4f} | centers: A={center_a:.4f}->{new_center_a:.4f}, B={center_b:.4f}->{new_center_b:.4f}")

    return nodes

"""
OAS Layout Writer — updates cell reference positions in an OAS/GDS file
based on optimized placement data from the symbolic editor.

Usage:
    update_oas_placement(
        oas_path="Xor_Automation.oas",
        sp_path="Xor_Automation.sp",
        nodes=<list of node dicts from editor>,
        output_path="Xor_Automation_updated.oas",
    )
"""

import os
import sys
import math

# Add project root so parser imports work when invoked from any directory
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import gdstk

from parser.layout_reader import extract_layout_instances
from parser.netlist_reader import read_netlist
from parser.device_matcher import match_devices


# ------------------------------------------------------------------
# Diffusion merge helper
# ------------------------------------------------------------------

def _merge_diffusion(top_cell, diff_layer, diff_datatype=0):
    """
    Flatten *top_cell* and merge all touching/overlapping polygons on
    *diff_layer* into the minimum set of non-overlapping shapes.

    This makes abutted transistor fingers render as one continuous
    shared-diffusion region in KLayout instead of collinear separate
    rectangles — exactly the physical behaviour you want for an abutted
    layout.

    NOTE: This flattens the cell hierarchy (all sub-cell geometry is
    inlined into top_cell, references are removed).  Only call this on
    the *export* copy of the layout, not on the source OAS for editing.

    Args:
        top_cell:      gdstk.Cell — top-level cell to process (modified
                                    in-place).
        diff_layer:    int  — GDS layer of the diffusion/active region.
                              Common values:
                                65  — FinFET ACTIVE (FreePDK45 / generic FinFET)
                                22  — SKY130 diff
                                1   — generic / mock PDK
                              Must match the layer used in your PDK cells.
        diff_datatype: int  — GDS datatype (default 0).
    """
    # Collect ALL diff polygons from the full hierarchy with transforms applied
    all_diff = top_cell.get_polygons(layer=diff_layer, datatype=diff_datatype)

    if not all_diff:
        print(f"[OAS Writer] _merge_diffusion: no polygons on "
              f"layer {diff_layer}/{diff_datatype} — skipping.")
        return

    print(f"[OAS Writer] Merging {len(all_diff)} DIFF polygon(s) "
          f"(layer {diff_layer}/{diff_datatype}) ...")

    # Boolean OR merges all overlapping / edge-touching polygons
    merged = gdstk.boolean(
        all_diff, [],
        "or",
        layer=diff_layer,
        datatype=diff_datatype,
    )

    # Flatten so sub-cell polygons are now owned by top_cell directly
    top_cell.flatten()

    # Remove the now-inlined (un-merged) diff polygons
    old_diff = [p for p in top_cell.polygons
                if p.layer == diff_layer and p.datatype == diff_datatype]
    for p in old_diff:
        top_cell.remove(p)

    # Insert the merged result
    for p in merged:
        top_cell.add(p)

    print(f"[OAS Writer] Merge complete: "
          f"{len(all_diff)} raw → {len(merged)} merged polygon(s).")


# ------------------------------------------------------------------
# Orientation helpers
# ------------------------------------------------------------------
_ORIENT_TO_GDSTK = {
    # orientation_string -> (rotation_degrees, x_reflection)
    "R0":        (0,   False),
    # gdstk applies x_reflection first (mirror across X-axis), then rotation.
    # So for left/right (horizontal) mirror we need 180° + x_reflection.
    "R0_FH":     (180, True),    # flipped horizontally (left/right)
    "R0_FV":     (0,   True),    # flipped vertically   (top/bottom)
    "R0_FH_FV":  (180, False),   # both flips           = 180° rotation
    "MX":        (0,   True),    # gdstk mirror convention
    "MY":        (180, True),
    "R90":       (90,  False),
    "R180":      (180, False),
    "R270":      (270, False),
}


def _orient_to_gdstk(orient_str):
    """Convert an orientation string to (rotation_rad, x_reflection)."""
    orient_str = (orient_str or "R0").strip()
    deg, mirror = _ORIENT_TO_GDSTK.get(orient_str, (0, False))
    return math.radians(deg), mirror


# ------------------------------------------------------------------
# Main API
# ------------------------------------------------------------------
def update_oas_placement(oas_path, sp_path, nodes, output_path,
                         output_format=None,
                         merge_diff_layer=None):
    """Read the original OAS, apply new positions from *nodes*, write output.

    Args:
        oas_path:       Path to the original .oas (or .gds) layout file.
        sp_path:        Path to the SPICE netlist (.sp) for device matching.
        nodes:          List of node dicts (same schema as placement JSON).
                        Each must have 'id' and 'geometry' with 'x', 'y',
                        and optionally 'orientation'.
        output_path:    Where to write the updated layout.
        output_format:  'oas' or 'gds'.  Auto-detected from output_path
                        extension if None.

    Returns:
        output_path on success.

    Raises:
        FileNotFoundError:  If oas_path or sp_path do not exist.
        ValueError:         If device counts mismatch or format is unknown.
    """
    # ---- validate inputs ------------------------------------------------
    if not os.path.isfile(oas_path):
        raise FileNotFoundError(f"OAS file not found: {oas_path}")
    if not os.path.isfile(sp_path):
        raise FileNotFoundError(f"SPICE file not found: {sp_path}")

    # ---- read original OAS ----------------------------------------------
    if oas_path.lower().endswith(".gds"):
        lib = gdstk.read_gds(oas_path)
    else:
        lib = gdstk.read_oas(oas_path)

    top_cell = lib.top_level()[0]
    refs = list(top_cell.references)

    # ---- build device_id → layout_ref_index mapping ---------------------
    layout_devices = extract_layout_instances(oas_path)
    netlist = read_netlist(sp_path)
    mapping = match_devices(netlist, layout_devices)
    # mapping: {device_id: layout_index}

    # ---- build lookup: device_id → node dict ----------------------------
    node_by_id = {n["id"]: n for n in nodes if not n.get("is_dummy")}

    # ---- apply new positions --------------------------------------------
    updated_count = 0
    for dev_id, layout_idx in mapping.items():
        node = node_by_id.get(dev_id)
        if node is None:
            print(f"[OAS Writer] ⚠ Device {dev_id} not found in nodes, "
                  "skipping.")
            continue

        geom = node.get("geometry", {})
        new_x = geom.get("x", 0)
        new_y = geom.get("y", 0)
        orient = geom.get("orientation", "R0")

        ref = refs[layout_idx]
        old_origin = tuple(ref.origin)

        # Update position
        ref.origin = (new_x, new_y)

        # Update orientation
        rotation_rad, x_reflection = _orient_to_gdstk(orient)
        ref.rotation = rotation_rad
        ref.x_reflection = x_reflection

        updated_count += 1
        if old_origin != (new_x, new_y):
            print(f"[OAS Writer] {dev_id}: "
                  f"({old_origin[0]:.4f}, {old_origin[1]:.4f}) -> "
                  f"({new_x:.4f}, {new_y:.4f})  orient={orient}")

    print(f"[OAS Writer] Updated {updated_count}/{len(mapping)} devices.")

    # ---- merge abutted diffusion (optional) -----------------------------
    if merge_diff_layer is not None:
        _merge_diffusion(top_cell, diff_layer=int(merge_diff_layer))

    # ---- determine output format ----------------------------------------
    if output_format is None:
        ext = os.path.splitext(output_path)[1].lower()
        output_format = "gds" if ext == ".gds" else "oas"

    # ---- write output ---------------------------------------------------
    if output_format == "gds":
        lib.write_gds(output_path)
    else:
        lib.write_oas(output_path)

    print(f"[OAS Writer] OK - Written to {output_path}")
    return output_path

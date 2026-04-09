"""
Layout reader — extracts device instances from OAS/GDS layout files.

Supports both flat and hierarchical layouts:
- Flat: top cell contains PCell references (nfet/pfet) directly
- Hierarchical: top cell references sub-cells that contain PCells
  (recursively descends to find leaf transistor instances)
"""

import gdstk
import math


def _is_transistor_cell(cell_name):
    """Check if a cell name looks like a transistor PCell."""
    name_lower = cell_name.lower()
    return ("nfet" in name_lower or "pfet" in name_lower or
            "nmos" in name_lower or "pmos" in name_lower)


def _is_resistor_cell(cell_name):
    """Check if a cell name looks like a resistor PCell."""
    name_lower = cell_name.lower()
    return ("rppoly" in name_lower or "rnwell" in name_lower or
            "rpoly" in name_lower or name_lower.startswith("res_"))


def _is_capacitor_cell(cell_name):
    """Check if a cell name looks like a capacitor PCell."""
    name_lower = cell_name.lower()
    return ("ccap" in name_lower or "mimcap" in name_lower or
            "mim" in name_lower or "vncap" in name_lower or
            name_lower.startswith("cap_"))


def _is_via_or_utility(cell_name):
    """Check if a cell name is a VIA or other non-transistor utility cell."""
    name_lower = cell_name.lower()
    return ("via" in name_lower or "stdvia" in name_lower or
            "fill" in name_lower or "tap" in name_lower or
            "boundary" in name_lower)


def _parse_abut_flags(ref_cell):
    """Parse leftAbut / rightAbut flags from a PCell's property list.

    The SAED PDK encodes these as bytes in a 'pcell' property entry, e.g.:
        [b'pcell', b'SAED_PDK_14', b'nfet', b'layout',
         b'leftAbut##32##5##32##1', b'rightAbut##32##5##32##0', ...]
    The last token after '##32##' is '1' (active) or '0' (inactive).

    Returns:
        dict with keys 'abut_left' (bool) and 'abut_right' (bool).
    """
    result = {"abut_left": False, "abut_right": False}
    try:
        for prop in ref_cell.properties:
            if not prop or prop[0] != "pcell":
                continue
            for entry in prop[1:]:
                if isinstance(entry, (bytes, bytearray)):
                    s = entry.decode("utf-8", errors="ignore")
                elif isinstance(entry, str):
                    s = entry
                else:
                    continue
                if "leftAbut" in s:
                    val = s.split("##32##")[-1].strip()
                    result["abut_left"] = (val == "1")
                elif "rightAbut" in s:
                    val = s.split("##32##")[-1].strip()
                    result["abut_right"] = (val == "1")
    except Exception:
        pass
    return result



def _ref_origin_rotation(ref):
    """Extract origin, rotation, and orientation string from a reference."""
    x, y = ref.origin
    rotation = ref.rotation if ref.rotation else 0
    mirrored = ref.x_reflection

    if mirrored:
        orientation = "MX"
    else:
        deg = round(math.degrees(rotation)) % 360
        orientation = f"R{deg}"

    return x, y, rotation, mirrored, orientation


def _extract_recursive(cell, lib, offset_x=0.0, offset_y=0.0,
                       parent_rotation=0.0, parent_mirror=False,
                       prefix=""):
    """Recursively extract leaf transistor instances from a cell hierarchy.

    Args:
        cell:            The gdstk Cell to traverse.
        lib:             The gdstk Library (for cell lookup).
        offset_x/y:      Accumulated position offset from parent transforms.
        parent_rotation: Accumulated rotation from parent transforms.
        parent_mirror:   Accumulated mirror from parent transforms.
        prefix:          Hierarchical instance name prefix (e.g., "XI0_").

    Returns:
        List of device dicts with absolute positions.
    """
    devices = []

    for ref in cell.references:
        cell_name = ref.cell.name if hasattr(ref.cell, 'name') else str(ref.cell)
        ref_cell = ref.cell if hasattr(ref.cell, 'references') else lib[ref.cell]

        rx, ry = ref.origin
        rotation = ref.rotation if ref.rotation else 0
        mirrored = ref.x_reflection

        # Compute absolute position by applying parent transform
        # For simplicity in placement: we use the ref origin + parent offset
        abs_x = offset_x + rx
        abs_y = offset_y + ry

        if _is_transistor_cell(cell_name):
            # Leaf transistor PCell — record it
            if mirrored:
                orientation = "MX"
            else:
                deg = round(math.degrees(rotation)) % 360
                orientation = f"R{deg}"

            bbox = ref_cell.bounding_box() if hasattr(ref_cell, 'bounding_box') else None
            if bbox is not None:
                (xmin, ymin), (xmax, ymax) = bbox
                width = xmax - xmin
                height = ymax - ymin
            else:
                width = 0
                height = 0

            abut_flags = _parse_abut_flags(ref_cell)
            devices.append({
                "cell": cell_name,
                "x": abs_x,
                "y": abs_y,
                "width": width,
                "height": height,
                "orientation": orientation,
                "hier_prefix": prefix,
                "abut_left":  abut_flags["abut_left"],
                "abut_right": abut_flags["abut_right"],
            })

        elif _is_resistor_cell(cell_name) or _is_capacitor_cell(cell_name):
            # Passive PCell — record it with passive_type tag
            passive_type = "res" if _is_resistor_cell(cell_name) else "cap"
            if mirrored:
                orientation = "MX"
            else:
                deg = round(math.degrees(rotation)) % 360
                orientation = f"R{deg}"

            bbox = ref_cell.bounding_box() if hasattr(ref_cell, 'bounding_box') else None
            if bbox is not None:
                (xmin, ymin), (xmax, ymax) = bbox
                width = xmax - xmin
                height = ymax - ymin
            else:
                width = 0
                height = 0

            devices.append({
                "cell": cell_name,
                "x": abs_x,
                "y": abs_y,
                "width": width,
                "height": height,
                "orientation": orientation,
                "hier_prefix": prefix,
                "passive_type": passive_type,
            })

        elif _is_via_or_utility(cell_name):
            # Skip vias and utility cells
            continue

        else:
            # Intermediate sub-cell — recurse into it
            sub_prefix = f"{prefix}{cell_name}_" if prefix else f"{cell_name}_"
            sub_devices = _extract_recursive(
                ref_cell, lib,
                offset_x=abs_x,
                offset_y=abs_y,
                parent_rotation=rotation,
                parent_mirror=mirrored,
                prefix=sub_prefix,
            )
            devices.extend(sub_devices)

    return devices


def extract_layout_instances(layout_file):
    """Extract device instances from an OAS/GDS layout file.

    Handles flat layouts (transistors and/or passives directly in top cell)
    and hierarchical layouts (sub-cells contain the leaf PCells).
    """
    if layout_file.endswith(".gds"):
        lib = gdstk.read_gds(layout_file)
    elif layout_file.endswith(".oas"):
        lib = gdstk.read_oas(layout_file)
    else:
        raise ValueError("Unsupported layout format")

    top_cell = lib.top_level()[0]

    def _is_known_device(cell_name):
        return (_is_transistor_cell(cell_name) or
                _is_resistor_cell(cell_name) or
                _is_capacitor_cell(cell_name))

    # Check if the top cell directly contains any known device PCells (flat layout)
    has_direct_devices = any(
        _is_known_device(ref.cell.name if hasattr(ref.cell, 'name') else str(ref.cell))
        for ref in top_cell.references
    )

    if has_direct_devices:
        # Flat layout — extract all known device types directly
        devices = []
        for ref in top_cell.references:
            cell_name = ref.cell.name if hasattr(ref.cell, 'name') else str(ref.cell)
            if not _is_known_device(cell_name):
                continue

            x, y, rotation, mirrored, orientation = _ref_origin_rotation(ref)
            ref_cell = ref.cell if hasattr(ref.cell, 'bounding_box') else None
            bbox = ref_cell.bounding_box() if ref_cell else None

            if bbox is not None:
                (xmin, ymin), (xmax, ymax) = bbox
                width = xmax - xmin
                height = ymax - ymin
            else:
                width = 0
                height = 0

            entry = {
                "cell": cell_name,
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "orientation": orientation,
            }
            # Tag passives
            if _is_resistor_cell(cell_name):
                entry["passive_type"] = "res"
            elif _is_capacitor_cell(cell_name):
                entry["passive_type"] = "cap"

            # Parse abutment flags from PCell properties
            ref_cell_obj = ref.cell if hasattr(ref.cell, 'bounding_box') else None
            if ref_cell_obj is not None and _is_transistor_cell(cell_name):
                abut_flags = _parse_abut_flags(ref_cell_obj)
                entry["abut_left"]  = abut_flags["abut_left"]
                entry["abut_right"] = abut_flags["abut_right"]

            devices.append(entry)
        return devices

    # Hierarchical layout — recursive extraction
    return _extract_recursive(top_cell, lib)
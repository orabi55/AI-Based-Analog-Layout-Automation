"""
Layout reader — extracts device instances from OAS/GDS layout files.

Supports both flat and hierarchical layouts:
- Flat: top cell contains PCell references (nfet/pfet) directly
- Hierarchical: top cell references sub-cells that contain PCells
  (recursively descends to find leaf transistor instances)
"""

import gdstk
import math
import logging


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


def _parse_pcell_params(*objs):
    """Parse parameters from multiple objects (e.g. Cell and Reference) and merge them.

    The SAED PDK encodes these as bytes in a 'pcell' property entry.
    Returns: dict of parameters {key: value_string}.
    """
    params = {}
    for obj in objs:
        if not hasattr(obj, "properties"):
            continue
        try:
            for prop in obj.properties:
                if not prop or len(prop) < 1:
                    continue
                
                # Check for 'pcell' key (could be bytes or str)
                key = prop[0]
                if isinstance(key, bytes):
                    key = key.decode("utf-8", errors="ignore")
                
                if str(key).lower() != "pcell":
                    continue

                for entry in prop[1:]:
                    if isinstance(entry, (bytes, bytearray)):
                        s = entry.decode("utf-8", errors="ignore")
                    elif isinstance(entry, str):
                        s = entry
                    else:
                        continue

                    if "##32##" in s:
                        parts = s.split("##32##")
                        if len(parts) >= 3:
                            param_key = parts[0].strip()
                            val = parts[-1].strip()
                            params[param_key] = val
        except (AttributeError, IndexError, TypeError):
            logging.debug("Failed to parse pcell property", exc_info=True)
    return params


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


def _identity_transform():
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _compose_transforms(parent, child):
    pa, pb, pc, pd, ptx, pty = parent
    ca, cb, cc, cd, ctx, cty = child
    return (
        pa * ca + pb * cc,
        pa * cb + pb * cd,
        pc * ca + pd * cc,
        pc * cb + pd * cd,
        pa * ctx + pb * cty + ptx,
        pc * ctx + pd * cty + pty,
    )


def _apply_transform(transform, x, y):
    a, b, c, d, tx, ty = transform
    return (a * x + b * y + tx, c * x + d * y + ty)


def invert_transform_point(parent_transform, x, y):
    """Convert a root-space point back into the local coordinates of a child ref."""
    a, b, c, d, tx, ty = parent_transform
    det = a * d - b * c
    if abs(det) < 1e-12:
        raise ValueError("Parent transform is not invertible.")

    dx = x - tx
    dy = y - ty
    return (
        (d * dx - b * dy) / det,
        (-c * dx + a * dy) / det,
    )


def _ref_transform(ref):
    x, y = ref.origin
    rotation = ref.rotation if ref.rotation else 0.0
    mirrored = bool(ref.x_reflection)
    cos_t = math.cos(rotation)
    sin_t = math.sin(rotation)

    if mirrored:
        linear = (cos_t, sin_t, sin_t, -cos_t)
    else:
        linear = (cos_t, -sin_t, sin_t, cos_t)

    return (*linear, x, y)


def _walk_layout_references(
    cell,
    lib,
    parent_transform=None,
    prefix="",
    include_references=False,
):
    """Recursively walk layout references while preserving parent transforms."""
    if parent_transform is None:
        parent_transform = _identity_transform()

    devices = []
    for ref in cell.references:
        cell_name = ref.cell.name if hasattr(ref.cell, "name") else str(ref.cell)
        ref_cell = ref.cell if hasattr(ref.cell, "references") else lib[ref.cell]
        absolute_transform = _compose_transforms(parent_transform, _ref_transform(ref))
        abs_x, abs_y = absolute_transform[4], absolute_transform[5]
        _, _, _, mirrored, orientation = _ref_origin_rotation(ref)

        bbox = ref_cell.bounding_box() if hasattr(ref_cell, "bounding_box") else None
        if bbox is not None:
            (xmin, ymin), (xmax, ymax) = bbox
            width = xmax - xmin
            height = ymax - ymin
        else:
            width = 0
            height = 0

        if _is_transistor_cell(cell_name):
            params = _parse_pcell_params(ref_cell, ref)
            entry = {
                "cell": cell_name,
                "x": abs_x,
                "y": abs_y,
                "width": width,
                "height": height,
                "orientation": orientation if not mirrored else "MX",
                "hier_prefix": prefix,
                "params": params,
                "abut_left": params.get("leftAbut") == "1",
                "abut_right": params.get("rightAbut") == "1",
            }
        elif _is_resistor_cell(cell_name) or _is_capacitor_cell(cell_name):
            passive_type = "res" if _is_resistor_cell(cell_name) else "cap"
            params = _parse_pcell_params(ref_cell, ref)
            entry = {
                "cell": cell_name,
                "x": abs_x,
                "y": abs_y,
                "width": width,
                "height": height,
                "orientation": orientation if not mirrored else "MX",
                "hier_prefix": prefix,
                "passive_type": passive_type,
                "params": params,
            }
        elif _is_via_or_utility(cell_name):
            continue
        else:
            sub_prefix = f"{prefix}{cell_name}_" if prefix else f"{cell_name}_"
            devices.extend(
                _walk_layout_references(
                    ref_cell,
                    lib,
                    parent_transform=absolute_transform,
                    prefix=sub_prefix,
                    include_references=include_references,
                )
            )
            continue

        if include_references:
            entry["reference"] = ref
            entry["parent_transform"] = parent_transform
        devices.append(entry)

    return devices


def extract_layout_instances_from_library(lib, include_references=False):
    """Extract device instances from an already-loaded OAS/GDS library."""
    top_cells = lib.top_level()
    if not top_cells:
        raise ValueError("Layout file has no top-level cells.")
    return _walk_layout_references(
        top_cells[0],
        lib,
        include_references=include_references,
    )


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

            params = _parse_pcell_params(ref_cell, ref)
            devices.append({
                "cell": cell_name,
                "x": abs_x,
                "y": abs_y,
                "width": width,
                "height": height,
                "orientation": orientation,
                "hier_prefix": prefix,
                "params": params,
                "abut_left":  params.get("leftAbut") == "1",
                "abut_right": params.get("rightAbut") == "1",
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

            params = _parse_pcell_params(ref_cell)
            devices.append({
                "cell": cell_name,
                "x": abs_x,
                "y": abs_y,
                "width": width,
                "height": height,
                "orientation": orientation,
                "hier_prefix": prefix,
                "passive_type": passive_type,
                "params": params,
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

    return extract_layout_instances_from_library(lib)

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

            x, y = ref.origin
            rotation = ref.rotation if ref.rotation else 0
            mirrored = ref.x_reflection

            if mirrored:
                orientation = "MX"
            else:
                deg = round(math.degrees(rotation)) % 360
                orientation = f"R{deg}"

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

            # Parse all PCell properties
            ref_cell_obj = ref.cell if hasattr(ref.cell, 'bounding_box') else None
            if ref_cell_obj is not None:
                params = _parse_pcell_params(ref_cell_obj, ref)
                entry["params"] = params
                if _is_transistor_cell(cell_name):
                    entry["abut_left"]  = (params.get("leftAbut") == "1")
                    entry["abut_right"] = (params.get("rightAbut") == "1")

            devices.append(entry)
        return devices

    # Hierarchical layout — recursive extraction
    return _extract_recursive(top_cell, lib)

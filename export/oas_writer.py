"""
OAS Layout Writer — updates cell reference positions in an OAS/GDS file
based on optimized placement data from the symbolic editor.

Abutment strategy
-----------------
SAED14nm abutment is encoded as distinct *cell* definitions: the same PCell
is cloned with leftAbut / rightAbut flags set in the cell-level 'pcell'
property.  Virtuoso re-reads the cell pcell property on import and
regenerates the PCell geometry (removing the end-cap diffusion on the
flagged side so two adjacent cells share one diffusion strip).

The writer:
  1. Reads the original OAS file.
  2. Builds a catalog of existing cell variants (keyed by base device type
     and param hash, separated from abutment flags).
  3. For each device that has a manual abutment annotation:
       - If a matching variant (same params, same abut flags) already exists
         in the catalog, redirect the reference to it.
       - Otherwise create a new variant cell by cloning the base cell and
         patching its 'pcell' property abutment entries, then redirect.
  4. Updates every reference origin / orientation.
  5. Writes the output OAS/GDS file.
"""

import os
import sys
import math
import hashlib
from collections import defaultdict

# Add project root so parser imports work when invoked from any directory
_project_root = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import gdstk

from parser.layout_reader import (
    extract_layout_instances_from_library,
    invert_transform_point,
)
from parser.netlist_reader import read_netlist
from parser.device_matcher import match_devices


# ------------------------------------------------------------------
# Orientation helpers
# ------------------------------------------------------------------
_ORIENT_TO_GDSTK = {
    "R0":        (0,   False),
    # gdstk/GDS x_reflection is an MX transform (mirror about the X axis).
    # Symbolic FH means left/right mirror, so it maps to MY: MX + R180.
    "R0_FH":     (180, True),
    "R0_FV":     (0,   True),
    "R0_FH_FV":  (180, False),
    "FH":        (180, True),
    "FV":        (0,   True),
    "MX":        (0,   True),
    "MY":        (180, True),
    "MXR90":     (90,  True),
    "MXR270":    (270, True),
    "R90":       (90,  False),
    "R180":      (180, False),
    "R270":      (270, False),
}


def _orient_to_gdstk(orient_str):
    orient_str = (orient_str or "R0").strip().upper()
    deg, mirror = _ORIENT_TO_GDSTK.get(orient_str, (0, False))
    return math.radians(deg), mirror


# ------------------------------------------------------------------
# PCell property helpers
# ------------------------------------------------------------------

def _decode(v):
    """Decode bytes to str; leave str unchanged."""
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", errors="ignore")
    return str(v)


def _encode(s):
    """Encode str to bytes."""
    return s.encode("utf-8") if isinstance(s, str) else s


def _get_pcell_prop(obj):
    """Return the raw pcell property list from obj, or None.

    The returned list is [key, token1, token2, ...] where key is b'pcell'.
    """
    for prop in getattr(obj, "properties", []) or []:
        if not prop:
            continue
        if _decode(prop[0]).lower() == "pcell":
            return list(prop)
    return None


def _parse_pcell_params_from_prop(raw_prop):
    """Parse a raw pcell prop list into (base_type, param_dict).

    raw_prop = [b'pcell', lib_bytes, type_bytes, view_bytes, param1, param2, ...]

    Returns:
        base_type  str  e.g. 'nfet'
        params     dict  {key: value_str}  for all '##32##'-encoded params
    """
    if not raw_prop or len(raw_prop) < 4:
        return None, {}

    base_type = _decode(raw_prop[2])   # index 2 = cell type (nfet/pfet/...)
    params = {}
    for token in raw_prop[4:]:         # actual parameters start at index 4
        s = _decode(token)
        if "##32##" in s:
            parts = s.split("##32##")
            if len(parts) >= 3:
                params[parts[0].strip()] = parts[-1].strip()
    return base_type, params


def _param_hash(params: dict) -> str:
    """Hash of non-abutment params (for catalog key)."""
    keys = sorted(k for k in params if k not in ("leftAbut", "rightAbut"))
    blob = "".join(f"{k}={params[k]}" for k in keys)
    return hashlib.md5(blob.encode()).hexdigest()[:8]


def _make_variant_cell(lib, base_cell, base_type, raw_prop, al: bool, ar: bool) -> "gdstk.Cell":
    """Clone base_cell and patch its pcell property abutment flags.
    Also physically trims the geometry to create the abutment effect.
    """
    al_str = "1" if al else "0"
    ar_str = "1" if ar else "0"
    variant_name = f"{base_type}_manual_l{al_str}r{ar_str}_{base_cell.name[-4:]}"

    for c in lib.cells:
        if c.name == variant_name:
            return c

    # Create a fresh copy manually to avoid duplication/linkage issues
    new_cell = gdstk.Cell(variant_name)
    lib.add(new_cell)

    # ── GEOMETRIC CLIPPING ENGINE (SAED 14nm Asymmetric Rules) ──────────
    # Based on analysis of Xor_abut_ex.oas:
    # 1. Left Abutment trims polygons at centerX < -0.01 on basic AND aggressive layers.
    # 2. Right Abutment trims polygons at centerX > 0.05 ONLY on basic layers.
    # 3. Aggressive layers (19, 81, 17) are NOT trimmed by Right Abutment.
    
    aggressive_layers = (19, 81, 17)
    basic_layers      = (13, 83, 2)
    
    for poly in base_cell.polygons:
        pb = poly.bounding_box()
        pcx = (pb[0][0] + pb[1][0]) / 2.0
        
        keep = True
        # Rule 1: Left Abutment
        if al and pcx < -0.01:
            if poly.layer in basic_layers or poly.layer in aggressive_layers:
                keep = False
        
        # Rule 2: Right Abutment
        if ar and pcx > 0.05:
            if poly.layer in basic_layers:
                keep = False
        
        if keep:
            new_cell.add(poly.copy())
    
    for path in base_cell.paths:
        pb = path.bounding_box()
        pcx = (pb[0][0] + pb[1][0]) / 2.0
        if al and pcx < -0.01: continue
        if ar and pcx > 0.05: continue
        new_cell.add(path.copy())
        
    for label in base_cell.labels:
        lx = label.origin[0]
        if al and lx < -0.01: continue
        if ar and lx > 0.05: continue
        new_cell.add(label.copy())

    # ── Property Patching ───────────────────────────────────────────────
    header   = raw_prop[:4]
    OLD_KEYS = {"leftAbut", "rightAbut"}
    left_template = "leftAbut##32##5##32##0"
    right_template = "rightAbut##32##5##32##0"

    kept = []
    for token in raw_prop[4:]:
        s = _decode(token)
        if "##32##" in s:
            key = s.split("##32##")[0].strip()
            if key == "leftAbut":
                left_template = s
                continue
            if key == "rightAbut":
                right_template = s
                continue
        kept.append(_encode(s) if isinstance(token, str) else token)

    def patch_val(template, val):
        parts = template.split("##32##")
        if len(parts) >= 3:
            parts[-1] = str(val)
            return "##32##".join(parts)
        return f"{parts[0]}##32##5##32##{val}"

    abut_tokens = [
        _encode(patch_val(right_template, ar_str)),
        _encode(patch_val(left_template, al_str)),
    ]

    new_prop_value = header[1:] + abut_tokens + kept
    new_cell.delete_property("pcell")
    new_cell.set_property("pcell", new_prop_value)

    return new_cell


def _is_resistor_cell(cell_name):
    name_lower = cell_name.lower()
    return ("rppoly" in name_lower or "rnwell" in name_lower or
            "rpoly" in name_lower or name_lower.startswith("res_"))

def _is_capacitor_cell(cell_name):
    name_lower = cell_name.lower()
    return ("ccap" in name_lower or "mimcap" in name_lower or
            "mim" in name_lower or "vncap" in name_lower or
            name_lower.startswith("cap_"))

def _is_transistor_cell(cell_name):
    name_lower = cell_name.lower()
    return ("nfet" in name_lower or "pfet" in name_lower or
            "nmos" in name_lower or "pmos" in name_lower)

def _is_dummy_cell(cell_name):
    name_lower = cell_name.lower()
    return (
        "dummy" in name_lower
        or name_lower.startswith(("dmy", "dum_"))
        or name_lower.startswith(("filler_dummy", "edge_dummy"))
    )

def _is_known_device(cell_name):
    return (_is_dummy_cell(cell_name) or
            _is_transistor_cell(cell_name) or
            _is_resistor_cell(cell_name) or
            _is_capacitor_cell(cell_name))

def _is_via_or_utility(cell_name):
    if _is_dummy_cell(cell_name):
        return False
    name_lower = cell_name.lower()
    return ("via" in name_lower or "stdvia" in name_lower or
            "fill" in name_lower or "tap" in name_lower or
            "boundary" in name_lower)

def _get_refs_recursive(cell, lib):
    devices = []
    for ref in cell.references:
        cell_name = ref.cell.name if hasattr(ref.cell, 'name') else str(ref.cell)
        if _is_known_device(cell_name):
            devices.append(ref)
        elif _is_via_or_utility(cell_name):
            continue
        else:
            # Intermediate sub-cell — recurse if it's a Cell object
            ref_cell = ref.cell if hasattr(ref.cell, 'references') else lib[ref.cell]
            sub_devices = _get_refs_recursive(ref_cell, lib)
            devices.extend(sub_devices)
    return devices

# ------------------------------------------------------------------
# Main API
# ------------------------------------------------------------------

def update_oas_placement(oas_path, sp_path, nodes, output_path,
                         output_format=None):
    """Read the original OAS, apply new positions from *nodes*, write output."""
    if not os.path.isfile(oas_path):
        raise FileNotFoundError(f"OAS file not found: {oas_path}")
    if not os.path.isfile(sp_path):
        raise FileNotFoundError(f"SPICE file not found: {sp_path}")

    if oas_path.lower().endswith(".gds"):
        lib = gdstk.read_gds(oas_path)
    else:
        lib = gdstk.read_oas(oas_path)

    # Load and import all cells from tests/taps.oas if it exists
    taps_oas_path = "tests/taps.oas"
    if not os.path.exists(taps_oas_path):
        project_root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
        taps_oas_path = os.path.join(project_root, "tests", "taps.oas")
    if os.path.exists(taps_oas_path):
        try:
            taps_lib = gdstk.read_oas(taps_oas_path)
            for c in taps_lib.cells:
                # Remove any existing cell with the same name first to avoid conflicts
                existing = [cell for cell in lib.cells if cell.name == c.name]
                for cell in existing:
                    try:
                        lib.remove(cell)
                    except ValueError:
                        pass
                lib.add(c)
        except Exception as e:
            import logging
            logging.warning(f"Failed to read taps from {taps_oas_path}: {e}")

    top_cells = lib.top_level()
    if not top_cells:
        raise ValueError("OAS file has no top-level cells.")
    top_cell = top_cells[0]

    # Match the flat/hierarchical logic of layout_reader.py
    has_direct_devices = any(
        _is_known_device(ref.cell.name if hasattr(ref.cell, 'name') else str(ref.cell))
        for ref in top_cell.references
    )

    # Find the top cell by name in lib.cells to be ABSOLUTELY SURE we modify the right object
    orig_top_name = top_cell.name
    top_cell = next(c for c in lib.cells if c.name == orig_top_name)

    # Re-extract references from THIS specific top_cell instance
    if has_direct_devices:
        refs = [r for r in top_cell.references if _is_known_device(r.cell.name if hasattr(r.cell, 'name') else str(r.cell))]
    else:
        refs = _get_refs_recursive(top_cell, lib)

    # ── Build catalog: {(base_type, param_hash): {(al,ar): cell}} ────────
    # This lets us find an existing abutment variant for a given combo, or
    # know which base cell to clone when we need to create a new variant.
    catalog = {}   # {(base_type, phash): {(al, ar): gdstk.Cell}}
    for cell in lib.cells:
        raw = _get_pcell_prop(cell)
        if not raw:
            continue
        base_type, params = _parse_pcell_params_from_prop(raw)
        if not base_type:
            continue
        al = params.get("leftAbut",  "0") == "1"
        ar = params.get("rightAbut", "0") == "1"
        ph = _param_hash(params)
        catalog.setdefault((base_type, ph), {})[( al, ar)] = cell

    # ── Device mapping ────────────────────────────────────────────────────
    layout_devices = extract_layout_instances_from_library(lib, include_references=True)
    netlist        = read_netlist(sp_path)
    mapping        = match_devices(netlist, layout_devices)
    node_by_id     = {n["id"]: n for n in nodes}

    # ── Apply updates ─────────────────────────────────────────────────────
    # Spatial matching to map netlist devices to references
    layout_to_nodes = defaultdict(list)
    explicit_layout_node_ids = set()
    for node in nodes:
        try:
            layout_idx = int(node.get("layout_index"))
        except (TypeError, ValueError):
            continue
        if 0 <= layout_idx < len(layout_devices):
            layout_to_nodes[layout_idx].append(node)
            explicit_layout_node_ids.add(node.get("id"))

    layout_idx_to_dev_id = {}
    for dev_id, l_idx in mapping.items():
        layout_idx_to_dev_id[l_idx] = dev_id

    for dev_id, layout_idx in mapping.items():
        if dev_id in explicit_layout_node_ids:
            continue
        node = node_by_id.get(dev_id)
        if node is None:
            # Fallback to parent ID (e.g. MM8_m1 -> MM8)
            from ai_agent.placement.finger_grouper import _parse_id
            parent_id, _, _ = _parse_id(dev_id)
            node = node_by_id.get(parent_id)
        if node is not None:
            layout_to_nodes[layout_idx].append(node)

    def _node_sort_key(node):
        electrical = node.get("electrical", {})
        return (
            electrical.get("array_index") or 0,
            electrical.get("multiplier_index") or 0,
            electrical.get("finger_index") or 0,
            node.get("id", ""),
        )

    def _node_is_dummy(node):
        node_id = str(node.get("id", "")).upper()
        return (
            bool(node.get("is_dummy"))
            or node_id.startswith(("DUMMY", "FILLER_DUMMY", "EDGE_DUMMY", "TAP_"))
            or node.get("type") == "tap"
        )

    def _layout_entry_type(entry):
        if entry.get("passive_type") in ("res", "cap"):
            return entry.get("passive_type")
        dtype = str(entry.get("dummy_type") or entry.get("type") or "").lower()
        if dtype in ("nmos", "pmos"):
            return dtype
        cell = str(entry.get("cell", "")).lower()
        if any(token in cell for token in ("pfet", "pmos", "pch")):
            return "pmos"
        if any(token in cell for token in ("nfet", "nmos", "nch")):
            return "nmos"
        return None

    def _node_type(node):
        dtype = str(node.get("type") or "").strip().lower()
        return dtype if dtype in ("nmos", "pmos", "res", "cap") else None

    template_refs_by_type = {}
    dummy_template_refs_by_type = {}
    for idx, entry in enumerate(layout_devices):
        ref = entry.get("reference")
        if ref is None:
            continue
        dtype = _layout_entry_type(entry)
        if not dtype:
            continue
        if entry.get("is_dummy"):
            dummy_template_refs_by_type.setdefault(dtype, ref)
        else:
            template_refs_by_type.setdefault(dtype, ref)

    def _template_ref_for_dummy_node(node):
        try:
            template_idx = int(node.get("template_layout_index"))
        except (TypeError, ValueError):
            template_idx = None
        if template_idx is not None and 0 <= template_idx < len(layout_devices):
            ref = layout_devices[template_idx].get("reference")
            if ref is not None:
                return ref

        dtype = _node_type(node)
        if not dtype:
            return None
        return dummy_template_refs_by_type.get(dtype) or template_refs_by_type.get(dtype)

    primary_node_by_layout_idx = {}
    ref_to_node = {}

    for layout_idx, group_nodes in layout_to_nodes.items():
        if layout_idx >= len(layout_devices):
            continue

        layout_entry = layout_devices[layout_idx]
        ref = layout_entry.get("reference")
        if ref is None:
            continue

        group_nodes = sorted(group_nodes, key=_node_sort_key)
        node = group_nodes[0]
        primary_node_by_layout_idx[layout_idx] = node
        ref_to_node[id(ref)] = node
        geom = dict(node.get("geometry", {}))
        if len(group_nodes) > 1:
            geom["x"] = min(n.get("geometry", {}).get("x", geom.get("x", 0)) for n in group_nodes)

        # Apply X offset for collapsed fingers
        dev_id = layout_idx_to_dev_id.get(layout_idx)
        x_offset = 0.0
        if dev_id and node.get("id") != dev_id:
            from ai_agent.placement.finger_grouper import _parse_id
            parent_id, mult_idx, finger_idx = _parse_id(dev_id)
            f_idx = 0
            if finger_idx is not None:
                f_idx = finger_idx
            elif mult_idx is not None:
                f_idx = mult_idx
            # Since these are internal fingers of the same logical device, they are touch-abutted (0.070um pitch).
            x_offset = f_idx * 0.070

        orient = geom.get("orientation", "R0")

        local_x, local_y = invert_transform_point(
            layout_entry.get("parent_transform", (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)),
            geom.get("x", 0) + x_offset,
            geom.get("y", 0),
        )

        orient = geom.get("orientation", "R0")
        
        # Position & orientation
        ref.origin = (local_x, local_y)
        rot_rad, x_mirror = _orient_to_gdstk(orient)
        ref.rotation     = rot_rad
        ref.x_reflection = x_mirror

        # Abutment
        abutment  = node.get("abutment")
        target_al = bool(abutment.get("abut_left",  False)) if abutment else False
        target_ar = bool(abutment.get("abut_right", False)) if abutment else False
        is_manual = (abutment is not None) or (dev_id and node.get("id") != dev_id)

        # If this is a fallback finger of a multi-finger device, override target_al/target_ar to ensure internal fingers share diffusion:
        if dev_id and node.get("id") != dev_id:
            from ai_agent.placement.finger_grouper import _parse_id
            parent_id, mult_idx, finger_idx = _parse_id(dev_id)
            f_idx = 0
            if finger_idx is not None:
                f_idx = finger_idx
            elif mult_idx is not None:
                f_idx = mult_idx
            
            # Count how many fingers this parent has in mapping
            sibling_indices = []
            for sibling_id in mapping.keys():
                sib_parent, sib_mult, sib_fing = _parse_id(sibling_id)
                if sib_parent == parent_id:
                    sib_idx = sib_fing if sib_fing is not None else (sib_mult if sib_mult is not None else 0)
                    sibling_indices.append(sib_idx)
            
            if sibling_indices:
                total_sibs = len(sibling_indices)
                # If this finger is not the first one, it must abut left (shared diffusion)
                if f_idx > 0:
                    target_al = True
                # If this finger is not the last one, it must abut right (shared diffusion)
                if f_idx < total_sibs - 1:
                    target_ar = True

        # Swap physical abutment flags under left-right mirrored orientations (e.g., rotation is 180)
        deg_deg = round(math.degrees(rot_rad)) % 360
        if deg_deg == 180:
            target_al, target_ar = target_ar, target_al

        base_raw = _get_pcell_prop(ref.cell)
        if not base_raw:
            continue

        base_type, base_params = _parse_pcell_params_from_prop(base_raw)
        if not base_type:
            continue

        ph = _param_hash(base_params)
        cat_key = (base_type, ph)
        variants = catalog.setdefault(cat_key, {})

        if is_manual:
            al_str = "1" if target_al else "0"
            ar_str = "1" if target_ar else "0"
            manual_name = f"{base_type}_manual_l{al_str}r{ar_str}_{ref.cell.name[-4:]}"
            existing_manual = next((c for c in lib.cells if c.name == manual_name), None)
            
            if existing_manual:
                ref.cell = existing_manual
            else:
                template_cell = variants.get((False, False), ref.cell)
                template_prop = _get_pcell_prop(template_cell) or base_raw
                # _make_variant_cell now handles the Clipping Engine automatically
                new_cell = _make_variant_cell(
                    lib, template_cell, base_type, template_prop, target_al, target_ar
                )
                ref.cell = new_cell
        else:
            if (target_al, target_ar) in variants:
                ref.cell = variants[(target_al, target_ar)]
            else:
                template_cell = variants.get((False, False), ref.cell)
                template_prop = _get_pcell_prop(template_cell) or base_raw
                new_cell = _make_variant_cell(
                    lib, template_cell, base_type, template_prop, target_al, target_ar
                )
                variants[(target_al, target_ar)] = new_cell
                ref.cell = new_cell


    # ── Write output ──────────────────────────────────────────────────────
    placed_node_ids = {
        node.get("id")
        for group_nodes in layout_to_nodes.values()
        for node in group_nodes
    }
    for node in nodes:
        if not _node_is_dummy(node) or node.get("id") in placed_node_ids:
            continue
        try:
            layout_idx = int(node.get("layout_index"))
        except (TypeError, ValueError):
            layout_idx = None
        if layout_idx is not None and 0 <= layout_idx < len(layout_devices):
            continue

        if node.get("type") == "tap" or str(node.get("id", "")).upper().startswith("TAP_"):
            subtype = node.get("subtype", "ptap")
            cell_name = "Ntap" if subtype == "ntap" else "Ptap"
            target_cell = next((c for c in lib.cells if c.name == cell_name), None)
            if target_cell is None:
                # Fallback case-insensitively
                target_cell = next((c for c in lib.cells if c.name.lower() == cell_name.lower()), None)
            if target_cell is None:
                logging.warning(f"Could not find cell {cell_name} for tap node {node.get('id')}")
                continue
            
            geom = dict(node.get("geometry", {}))
            rot_rad, x_mirror = _orient_to_gdstk(geom.get("orientation", "R0"))
            dummy_ref = gdstk.Reference(
                target_cell,
                (geom.get("x", 0), geom.get("y", 0)),
                rot_rad,
                1.0,
                x_mirror,
            )
            try:
                dummy_ref.set_property("symbolic_tap", [str(node.get("id", ""))])
            except Exception:
                pass
            top_cell.add(dummy_ref)
            ref_to_node[id(dummy_ref)] = node
            continue

        template_ref = _template_ref_for_dummy_node(node)
        if template_ref is None:
            continue

        geom = dict(node.get("geometry", {}))
        rot_rad, x_mirror = _orient_to_gdstk(geom.get("orientation", "R0"))
        dummy_ref = gdstk.Reference(
            template_ref.cell,
            (geom.get("x", 0), geom.get("y", 0)),
            rot_rad,
            template_ref.magnification,
            x_mirror,
        )
        for prop in getattr(template_ref, "properties", []) or []:
            try:
                dummy_ref.set_property(prop[0], prop[1:])
            except Exception:
                pass
        try:
            dummy_ref.set_property("symbolic_dummy", [str(node.get("id", ""))])
        except Exception:
            pass
        top_cell.add(dummy_ref)
        ref_to_node[id(dummy_ref)] = node

    if output_format is None:
        ext = os.path.splitext(output_path)[1].lower()
        output_format = "gds" if ext == ".gds" else "oas"

    # Create a completely fresh library
    final_lib = gdstk.Library(lib.name)
    
    # Rebuild all used cells as fresh objects
    cell_map = {}
    
    # 1. Map all dependencies to fresh cells
    all_needed = top_cell.dependencies(True)
    for old_c in all_needed:
        fresh_c = gdstk.Cell(old_c.name)
        # Copy properties
        for prop in old_c.properties:
            fresh_c.set_property(prop[0], prop[1:])
        # Copy polygons etc.
        fresh_c.add(*old_c.polygons)
        fresh_c.add(*old_c.paths)
        fresh_c.add(*old_c.labels)
        # Note: references will be handled after all fresh cells exist
        cell_map[old_c.name] = fresh_c
        final_lib.add(fresh_c)
        
    # 2. Rebuild top cell
    fresh_top = gdstk.Cell(top_cell.name)
    for prop in top_cell.properties:
        fresh_top.set_property(prop[0], prop[1:])
    fresh_top.add(*top_cell.polygons)
    fresh_top.add(*top_cell.paths)
    fresh_top.add(*top_cell.labels)
    final_lib.add(fresh_top)
    
    # 3. Rebuild all references using the mapping
    # First for subcells
    for old_c in all_needed:
        fresh_c = cell_map[old_c.name]
        for r in old_c.references:
            target_name = r.cell.name if hasattr(r.cell, 'name') else str(r.cell)
            if target_name in cell_map:
                fresh_ref = gdstk.Reference(
                    cell_map[target_name],
                    r.origin, r.rotation, r.magnification, r.x_reflection
                )
                for prop in r.properties:
                    fresh_ref.set_property(prop[0], prop[1:])
                
                # Check for abutment properties from the nodes
                node = ref_to_node.get(id(r))
                if node:
                    abut = node.get("abutment")
                    this_al = bool(abut.get("abut_left", False)) if abut else False
                    this_ar = bool(abut.get("abut_right", False)) if abut else False
                    
                    # Swap physical properties under left-right mirrored orientations (e.g., rotation is 180)
                    ref_deg = round(math.degrees(r.rotation)) % 360
                    if ref_deg == 180:
                        this_al, this_ar = this_ar, this_al
                    
                    if this_al:
                        fresh_ref.set_property("left_abut", [1])
                    if this_ar:
                        fresh_ref.set_property("right_abut", [1])
                
                fresh_c.add(fresh_ref)
                
    # Then for top cell
    for r in top_cell.references:
        target_name = r.cell.name if hasattr(r.cell, 'name') else str(r.cell)
        target_cell = cell_map.get(target_name)
        
        if not target_cell:
            orig_variant = next((c for c in lib.cells if c.name == target_name), None)
            if orig_variant:
                target_cell = gdstk.Cell(orig_variant.name)
                for prop in orig_variant.properties:
                    target_cell.set_property(prop[0], prop[1:])
                target_cell.add(*orig_variant.polygons)
                cell_map[target_name] = target_cell
                final_lib.add(target_cell)
        
        if target_cell:
            fresh_ref = gdstk.Reference(
                target_cell,
                r.origin, r.rotation, r.magnification, r.x_reflection
            )
            for prop in r.properties:
                fresh_ref.set_property(prop[0], prop[1:])
            
            # Check for abutment properties from the nodes
            node = ref_to_node.get(id(r))
            if node:
                abut = node.get("abutment")
                this_al = bool(abut.get("abut_left", False)) if abut else False
                this_ar = bool(abut.get("abut_right", False)) if abut else False
                
                # Swap physical properties under left-right mirrored orientations (e.g., rotation is 180)
                ref_deg = round(math.degrees(r.rotation)) % 360
                if ref_deg == 180:
                    this_al, this_ar = this_ar, this_al
                
                if this_al:
                    fresh_ref.set_property("left_abut", [1])
                if this_ar:
                    fresh_ref.set_property("right_abut", [1])
            
            fresh_top.add(fresh_ref)

    if output_format == "gds":
        final_lib.write_gds(output_path)
    else:
        final_lib.write_oas(output_path)

    return output_path

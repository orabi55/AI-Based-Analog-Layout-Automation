"""
Tool Registry
=============
Anthropic-compatible tool schema dicts for every layout operation.
Single source of truth for both the chatbot and the MCP server.

Parameter names exactly match the underlying core/ function signatures.
The dispatcher in dispatcher.py is the authoritative routing layer.

Usage:
    from ai_agent.tools.schemas import TOOL_REGISTRY
    # Pass TOOL_REGISTRY directly to the Anthropic client as the `tools` argument.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Shared sub-schema fragments
# ---------------------------------------------------------------------------

_FLOAT = {"type": "number"}
_STR   = {"type": "string"}
_INT   = {"type": "integer"}
_STR_ARRAY = {"type": "array", "items": {"type": "string"}}


def _prop(type_: str, description: str, **extra) -> dict:
    return {"type": type_, "description": description, **extra}


# ---------------------------------------------------------------------------
# Individual schemas
# ---------------------------------------------------------------------------

_READ_LAYOUT = {
    "name": "read_layout",
    "description": (
        "Return the full current layout: every device node with its position, "
        "type, and geometry. Use this to inspect the state before making changes."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_LIST_DEVICES = {
    "name": "list_devices",
    "description": "List all device IDs and types in the current layout.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_GET_DEVICE_INFO = {
    "name": "get_device_info",
    "description": "Return geometry, type, and electrical properties for one device.",
    "input_schema": {
        "type": "object",
        "properties": {
            "device_id": _prop("string", "Device identifier, e.g. 'MM1'"),
        },
        "required": ["device_id"],
    },
}

_MOVE_DEVICE = {
    "name": "move_device",
    "description": "Move a device to a new (x, y) position in micrometers.",
    "input_schema": {
        "type": "object",
        "properties": {
            "device": _prop("string", "Device ID to move"),
            "x":      _prop("number", "Target X position in µm"),
            "y":      _prop("number", "Target Y position in µm"),
        },
        "required": ["device", "x", "y"],
    },
}

_SWAP_DEVICES = {
    "name": "swap_devices",
    "description": "Swap the (x, y) positions of two devices.",
    "input_schema": {
        "type": "object",
        "properties": {
            "device_a": _prop("string", "First device ID"),
            "device_b": _prop("string", "Second device ID"),
        },
        "required": ["device_a", "device_b"],
    },
}

_FLIP_DEVICE = {
    "name": "flip_device",
    "description": "Flip a device horizontally ('h') or vertically ('v').",
    "input_schema": {
        "type": "object",
        "properties": {
            "device": _prop("string", "Device ID to flip"),
            "axis":   _prop("string", "Flip axis: 'h' for horizontal, 'v' for vertical"),
        },
        "required": ["device", "axis"],
    },
}

_ADD_DUMMY = {
    "name": "add_dummy",
    "description": "Add a single dummy fill device at a specified position.",
    "input_schema": {
        "type": "object",
        "properties": {
            "type":   _prop("string", "Device type for the dummy, e.g. 'nmos' or 'pmos'"),
            "x":      _prop("number", "X position in µm"),
            "y":      _prop("number", "Y position in µm"),
            "width":  _prop("number", "Device width in µm (default 0.294)"),
            "height": _prop("number", "Device height in µm (default 0.568)"),
        },
        "required": ["type", "x", "y"],
    },
}

_REMOVE_DUMMIES = {
    "name": "remove_dummies",
    "description": "Remove all dummy and filler cells from the layout.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_CHECK_OVERLAPS = {
    "name": "check_overlaps",
    "description": (
        "Run a DRC check to detect overlap and minimum-spacing violations. "
        "Returns a pass/fail result with a full violation list."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "gap_px": _prop("number", "Minimum spacing in layout pixels (default 0.0)"),
        },
        "required": [],
    },
}

_RUN_LEGALIZER = {
    "name": "run_legalizer",
    "description": (
        "Detect all DRC violations and apply prescriptive mechanical fixes to "
        "resolve them. Modifies device positions to eliminate overlaps and gaps."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "gap_px": _prop("number", "Minimum spacing in layout pixels (default 0.0)"),
        },
        "required": [],
    },
}

_SAVE_LAYOUT = {
    "name": "save_layout",
    "description": (
        "Serialize the current layout to JSON. Optionally write to a file path. "
        "Returns the serialized JSON string in metrics['serialized']."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": _prop("string", "Optional file path to write the layout JSON to"),
        },
        "required": [],
    },
}

_INSERT_TAPS = {
    "name": "insert_taps",
    "description": (
        "Insert substrate / well-tie tap cells (ptap for NMOS rows, ntap for PMOS rows) "
        "at required intervals across every row. Interval driven by PDK tap_max_distance_um."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_INSERT_ENDCAPS = {
    "name": "insert_endcaps",
    "description": (
        "Insert endcap cells at the left and right boundary of every row. "
        "Cell names come from the PDK's endcap_cell_names rule."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_INSERT_FILLERS = {
    "name": "insert_fillers",
    "description": (
        "Fill intra-row gaps with density dummy cells using the existing "
        "finger-grouper legalizer. Ensures equal row widths and pitch-grid alignment."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_INSERT_ALL_PHYSICAL_CELLS = {
    "name": "insert_all_physical_cells",
    "description": (
        "Run the full physical-cell insertion pipeline in order: "
        "endcaps → tap cells → fillers. Aggregates warnings from each step."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_PLACE_COMMON_CENTROID = {
    "name": "place_common_centroid",
    "description": (
        "Place two groups of matched finger nodes in a 1D common-centroid (ABBA) "
        "pattern. Calls generate_placement_grid(technique='CC', rows=1). "
        "Returns repositioned nodes and centroid_error_um."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "group_a_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "IDs of the finger nodes that belong to device A",
            },
            "group_b_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "IDs of the finger nodes that belong to device B",
            },
            "start_x": _prop("number", "Left-edge X coordinate for the pattern in µm"),
            "row_y":   _prop("number", "Row Y coordinate in µm"),
            "pattern": _prop("string", "Pattern label, e.g. 'ABBA' (informational only, default 'ABBA')"),
        },
        "required": ["group_a_ids", "group_b_ids", "start_x", "row_y"],
    },
}

_PLACE_COMMON_CENTROID_2D = {
    "name": "place_common_centroid_2d",
    "description": (
        "Place multiple device groups in a 2D common-centroid matrix. "
        "Calls generate_common_centroid_matrix() and handles arbitrary device ratios. "
        "Returns repositioned nodes and centroid_error_um."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "device_specs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id":      {"type": "string",  "description": "Logical device ID, e.g. 'MM1'"},
                        "fingers": {"type": "integer", "description": "Number of fingers (inferred from nodes if omitted)"},
                    },
                    "required": ["id"],
                },
                "description": "List of {id, fingers} specs — one entry per device",
            },
            "start_x": _prop("number", "Left-edge X coordinate in µm"),
            "row_y":   _prop("number", "Y coordinate of the bottom row in µm"),
        },
        "required": ["device_specs", "start_x", "row_y"],
    },
}

_INSERT_DUMMIES_AROUND_GROUP = {
    "name": "insert_dummies_around_group",
    "description": (
        "Insert structural isolation dummy fingers on both sides of a matched device "
        "group. Dummies are marked structural=True so the filler engine does NOT strip them."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "group_node_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "IDs of the finger nodes that form the group",
            },
            "n_dummies": _prop("integer", "Number of dummy fingers per side per row (default 1)"),
        },
        "required": ["group_node_ids"],
    },
}

_SCORE_LAYOUT = {
    "name": "score_layout",
    "description": (
        "Compute quantitative matching and symmetry quality scores for the current "
        "placement: layout Y symmetry, X mirror symmetry, interdigitation pattern, "
        "2D common-centroid accuracy, and DRC cleanliness. Returns a composite score."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_PLACE_RESISTOR = {
    "name": "place_resistor",
    "description": (
        "Compute resistor geometry from a target area and aspect ratio (L/W). "
        "Series folding stacks segments vertically (increases R per unit width). "
        "Returns node with type='resistor', segments list, and actual_resistance_ratio."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "node_id":       _prop("string",  "ID of the device node to configure as a resistor"),
            "area_um2":      _prop("number",  "Target physical area in µm²"),
            "aspect_ratio":  _prop("number",  "L/W ratio (default 4.0); higher = higher resistance"),
            "allow_series":  _prop("boolean", "Allow folding into stacked series segments (default true)"),
            "allow_parallel":_prop("boolean", "Allow parallel finger configurations (default true)"),
        },
        "required": ["node_id", "area_um2"],
    },
}

_PLACE_MOM_CAP = {
    "name": "place_mom_cap",
    "description": (
        "Place a rectangular interdigitated metal-finger MOM capacitor. "
        "Sets can_overlap=True — the cell may be placed above transistor rows."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "node_id":  _prop("string", "ID of the device node to configure as a MOM cap"),
            "area_um2": _prop("number", "Target physical area in µm²"),
            "layers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Metal layers to use, e.g. ['M2','M3','M4'] (default ['M2','M3','M4'])",
            },
        },
        "required": ["node_id", "area_um2"],
    },
}

_PLACE_MOS_CAP = {
    "name": "place_mos_cap",
    "description": (
        "Place a MOS capacitor (transistor with gate tied to drain). "
        "Reuses standard transistor geometry: nf fingers × 0.294 µm pitch. "
        "Returns node with type='mos_cap'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "node_id":   _prop("string",  "ID of the device node to configure as a MOS cap"),
            "nf":        _prop("integer", "Number of gate fingers (≥ 1)"),
            "width_um":  _prop("number",  "Channel width per finger in µm"),
        },
        "required": ["node_id", "nf", "width_um"],
    },
}

_RESHAPE_PASSIVE = {
    "name": "reshape_passive",
    "description": (
        "Resize any passive device (resistor, mom_cap, mos_cap) to a new area "
        "while preserving its type and electrical ratios. "
        "Reads stored _passive metadata written by the original place_* call."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "node_id":      _prop("string", "ID of the passive device node to resize"),
            "new_area_um2": _prop("number", "New target physical area in µm²"),
        },
        "required": ["node_id", "new_area_um2"],
    },
}


# ---------------------------------------------------------------------------
# GUI-parity layout operation tools
# ---------------------------------------------------------------------------

_DELETE_DEVICE = {
    "name": "delete_device",
    "description": "Remove a device (and all its finger nodes) from the layout.",
    "input_schema": {
        "type": "object",
        "properties": {
            "device_id": _prop("string", "ID of the device to delete"),
        },
        "required": ["device_id"],
    },
}

_ALIGN_DEVICES = {
    "name": "align_devices",
    "description": (
        "Align a group of devices along the x-axis (left edges) or y-axis (row). "
        "mode: 'mean' (average position), 'min', 'max', or 'reference' "
        "(snap to reference_id's coordinate)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "device_ids":   {"type": "array", "items": {"type": "string"},
                             "description": "IDs of devices to align"},
            "axis":         _prop("string", "'x' (left-edge) or 'y' (row)"),
            "mode":         _prop("string",
                                  "'mean' | 'min' | 'max' | 'reference' (default 'mean')"),
            "reference_id": _prop("string", "Reference device ID when mode='reference'"),
        },
        "required": ["device_ids"],
    },
}

_ABUT_DEVICES = {
    "name": "abut_devices",
    "description": (
        "Place device_b immediately to the right of device_a for shared-diffusion "
        "abutment (0.070 µm pitch instead of the standard 0.294 µm slot). "
        "Sets abut_right on A and abut_left on B."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "device_a": _prop("string", "Left device ID"),
            "device_b": _prop("string", "Right device ID"),
        },
        "required": ["device_a", "device_b"],
    },
}

_MERGE_SHARED_SOURCE = {
    "name": "merge_shared_source",
    "description": (
        "SS-merge: place device_b immediately LEFT of device_a and flip it "
        "horizontally so their source diffusions share a contact. "
        "Both devices must be the same type."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "device_a": _prop("string", "Reference (right) device"),
            "device_b": _prop("string", "Device to flip and place on the left"),
        },
        "required": ["device_a", "device_b"],
    },
}

_MERGE_SHARED_DRAIN = {
    "name": "merge_shared_drain",
    "description": (
        "DD-merge: place device_b immediately RIGHT of device_a and flip it "
        "horizontally so their drain diffusions share a contact."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "device_a": _prop("string", "Reference (left) device"),
            "device_b": _prop("string", "Device to flip and place on the right"),
        },
        "required": ["device_a", "device_b"],
    },
}

_LOCK_DEVICE = {
    "name": "lock_device",
    "description": "Freeze a device's position so it cannot be moved by the editor.",
    "input_schema": {
        "type": "object",
        "properties": {
            "device_id": _prop("string", "Device to lock"),
        },
        "required": ["device_id"],
    },
}

_UNLOCK_DEVICE = {
    "name": "unlock_device",
    "description": "Remove the position lock from a device.",
    "input_schema": {
        "type": "object",
        "properties": {
            "device_id": _prop("string", "Device to unlock"),
        },
        "required": ["device_id"],
    },
}

_SET_DEVICE_COLOR = {
    "name": "set_device_color",
    "description": "Assign a custom hex color to a device in the editor (e.g. '#ff6b6b').",
    "input_schema": {
        "type": "object",
        "properties": {
            "device_id": _prop("string", "Device ID"),
            "color":     _prop("string", "CSS hex color, e.g. '#4a90d9'"),
        },
        "required": ["device_id", "color"],
    },
}

_RESET_DEVICE_COLOR = {
    "name": "reset_device_color",
    "description": "Remove the custom color from a device (reverts to default type color).",
    "input_schema": {
        "type": "object",
        "properties": {
            "device_id": _prop("string", "Device ID"),
        },
        "required": ["device_id"],
    },
}

_GET_LAYOUT_BOUNDS = {
    "name": "get_layout_bounds",
    "description": (
        "Return the bounding box (min/max x,y), total area, active device area, "
        "utilization %, and aspect ratio of the current layout."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_CREATE_GROUP = {
    "name": "create_group",
    "description": (
        "Create a named custom group from a list of device IDs. "
        "The group appears in the Hierarchy → Groups tab and lets the user "
        "move all its members together. "
        "Equivalent to right-click → Create Group in the editor."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "device_ids": {
                "type":  "array",
                "items": {"type": "string"},
                "description": "IDs of devices to group (minimum 2)",
            },
            "name": _prop("string",
                          "Group name (default auto-generated, e.g. 'GROUP_1')"),
        },
        "required": ["device_ids"],
    },
}

_MATCH_DEVICES = {
    "name": "match_devices",
    "description": (
        "Apply an interdigitation or common-centroid matching pattern to a set of "
        "devices. Equivalent to Design → Match Devices in the GUI. "
        "technique: 'interdigitated' | 'common_centroid' | 'common_centroid_2d' | 'custom'. "
        "For 'custom', supply a pattern string in custom_pattern."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "device_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "IDs of finger/device nodes to include in the match",
            },
            "technique":      _prop("string",
                                    "Matching technique (default 'interdigitated')"),
            "custom_pattern": _prop("string",
                                    "Pattern string when technique='custom', e.g. 'M0 M1 M0 / M1 M0 M1'"),
        },
        "required": ["device_ids"],
    },
}


# ---------------------------------------------------------------------------
# Mid-level / block tools — circuit-pattern detection
# ---------------------------------------------------------------------------

_DETECT_MATCHED_PAIRS = {
    "name": "detect_matched_pairs",
    "description": (
        "Find devices that share the same electrical signature (type, W, H). "
        "Returns matched_pairs and matched_clusters (full equivalence classes)."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_DETECT_DIFFERENTIAL_PAIRS = {
    "name": "detect_differential_pairs",
    "description": (
        "Detect differential pairs: two same-type devices sharing a non-power "
        "source net. Requires terminal_nets to be loaded with the layout."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_DETECT_CURRENT_MIRRORS = {
    "name": "detect_current_mirrors",
    "description": (
        "Detect current-mirror clusters: same-type devices sharing a non-power gate "
        "net where at least one is diode-connected (gate == drain)."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_DETECT_CROSS_COUPLED = {
    "name": "detect_cross_coupled_pairs",
    "description": (
        "Detect cross-coupled latch pairs: same-type device pair where "
        "drain(A)==gate(B) and drain(B)==gate(A)."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_PLACE_MATCHED_PAIR = {
    "name": "place_matched_pair",
    "description": (
        "Interdigitate two matched parent devices in an ABBA common-centroid "
        "pattern. start_x and row_y default to the existing position of device_a "
        "if omitted."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "device_a": _prop("string", "First parent device ID"),
            "device_b": _prop("string", "Second parent device ID"),
            "start_x":  _prop("number", "Optional left-edge X in µm"),
            "row_y":    _prop("number", "Optional row Y coordinate in µm"),
        },
        "required": ["device_a", "device_b"],
    },
}

_PLACE_DIFFERENTIAL_PAIR = {
    "name": "place_differential_pair",
    "description": "Interdigitate a differential pair (ABAB pattern).",
    "input_schema": {
        "type": "object",
        "properties": {
            "device_a": _prop("string", "VINP-side parent device ID"),
            "device_b": _prop("string", "VINN-side parent device ID"),
            "start_x":  _prop("number", "Optional left-edge X in µm"),
            "row_y":    _prop("number", "Optional row Y coordinate in µm"),
        },
        "required": ["device_a", "device_b"],
    },
}

_PLACE_CURRENT_MIRROR = {
    "name": "place_current_mirror",
    "description": (
        "Place a current-mirror cluster (>= 2 parent devices) common-centroid. "
        "For 2 devices uses ABBA; for larger clusters uses 2D matrix CC."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "device_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Parent device IDs in the mirror cluster (>= 2)",
            },
            "start_x":  _prop("number", "Optional left-edge X in µm"),
            "row_y":    _prop("number", "Optional row Y coordinate in µm"),
        },
        "required": ["device_ids"],
    },
}

_ADD_DUMMY_GROUP = {
    "name": "add_dummy_group",
    "description": (
        "Insert N structural dummy fingers on each side of a matched group. "
        "Dummies are marked structural=True so the filler engine does NOT strip them. "
        "Same engine as insert_dummies_around_group, exposed under a name that matches "
        "the natural 'add dummies around X' phrasing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "group_node_ids": {
                "type":  "array",
                "items": {"type": "string"},
                "description": "IDs of finger nodes that form the group",
            },
            "n_dummies": _prop("integer", "Dummies per side per row (default 1)"),
        },
        "required": ["group_node_ids"],
    },
}

_VALIDATE_SYMMETRY = {
    "name": "validate_symmetry",
    "description": (
        "Score the placement against symmetry & matching benchmarks. "
        "Returns pass/fail (>= 90% on both axes) plus the full score breakdown."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_VALIDATE_DUMMY_PRESENCE = {
    "name": "validate_dummy_presence",
    "description": (
        "Verify that structural dummies sit on both sides of a matched group "
        "on each row. Returns pass/fail per row plus per-side dummy counts."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "group_node_ids": {
                "type":  "array",
                "items": {"type": "string"},
                "description": "IDs of finger nodes that form the group",
            },
            "min_dummies_per_side": _prop("integer",
                "Minimum dummies required on each side per row (default 1)"),
        },
        "required": ["group_node_ids"],
    },
}


# ---------------------------------------------------------------------------
# Advanced / circuit-level tools
# ---------------------------------------------------------------------------

_DETECT_CIRCUIT_TYPE = {
    "name": "detect_circuit_type",
    "description": (
        "Best-effort circuit classification (comparator / latch / "
        "differential_amplifier / differential_pair / current_mirror_array / "
        "matched_array / generic) using detection results from diff/cross/mirror "
        "scans."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_PLACE_COMPARATOR = {
    "name": "place_comparator",
    "description": (
        "Place a comparator: detect + place differential input pair, "
        "cross-coupled latch (ABBA), and load mirror in one go."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_PLACE_TX_DRIVER = {
    "name": "place_tx_driver",
    "description": (
        "Place a TX driver: every detected current-mirror cluster gets "
        "common-centroid placement."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_RUN_FULL_LAYOUT_PIPELINE = {
    "name": "run_full_layout_pipeline",
    "description": (
        "End-to-end layout: detect circuit type → optimize for matching → "
        "insert physical cells (endcaps + taps + fillers) → validate symmetry."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_OPTIMIZE_FOR_MATCHING = {
    "name": "optimize_layout_for_matching",
    "description": (
        "Apply common-centroid placement to every detected matched structure: "
        "diff pairs (ABAB), cross-coupled (ABBA), current mirrors (ABBA / 2D-CC), "
        "and remaining matched pairs (ABBA)."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_OPTIMIZE_FOR_ROUTING = {
    "name": "optimize_layout_for_routing",
    "description": (
        "Routing-friendly cleanup: legalize DRC, then insert structural dummies "
        "around every matched cluster for vertical routing breathing room."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "gap_px": _prop("number", "Minimum spacing in pixels (default 0.0)"),
        },
        "required": [],
    },
}


# ---------------------------------------------------------------------------
# Registry — order matches the logical tool-call workflow
# ---------------------------------------------------------------------------

_PLACE_SEQUENCE = {
    "name": "place_sequence",
    "description": (
        "Place an ordered sequence of devices in a specific row. "
        "Each device occupies a standard 0.294um slot (no overlaps). "
        "The tool automatically detects and sets 'abut_left' and 'abut_right' "
        "flags between adjacent devices if they share a signal potential."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "row_y":      _prop("number", "Row Y coordinate in µm"),
            "device_ids": _prop("array", "Ordered list of device IDs to place in this row", items={"type": "string"}),
            "start_x":    _prop("number", "Optional left-edge X coordinate (default 0.0)"),
        },
        "required": ["row_y", "device_ids"],
    },
}

_SWAP_ROWS = {
    "name": "swap_rows",
    "description": (
        "Swap all devices between two different row y-coordinates in the layout. "
        "Useful for rapidly changing the relative vertical placement of entire rows."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "row_y1": _prop("number", "Y coordinate of the first row in µm"),
            "row_y2": _prop("number", "Y coordinate of the second row in µm"),
        },
        "required": ["row_y1", "row_y2"],
    },
}

_INSERT_GUARD_RING = {
    "name": "insert_guard_ring",
    "description": (
        "Add an automated substrate isolation guard ring around the selected group of devices or around "
        "the entire layout bounding box. Places ptap cells for NMOS / p-substrate and ntap cells for PMOS / n-well."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "group_node_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of device IDs to surround. If empty/omitted, surrounds the entire layout.",
            },
            "ring_type": _prop("string", "Type of tap cell to place: 'ptap', 'ntap', or 'both' (default 'ptap')"),
            "spacing_um": _prop("number", "Distance from device boundary to the guard ring in micrometers (default 0.5)"),
            "tap_width_um": _prop("number", "Width of each tap cell in micrometers (default 0.294)"),
        },
        "required": [],
    },
}

_HIGHLIGHT_DEVICE_NET = {
    "name": "highlight_device_net",
    "description": "Highlight terminal labels connected to a specific net and dim other devices on the canvas.",
    "input_schema": {
        "type": "object",
        "properties": {
            "net_name": _prop("string", "Name of the net to highlight, e.g. 'VDD', 'GND', 'clk'"),
        },
        "required": ["net_name"],
    },
}

_DRAW_SYMMETRY_AXIS = {
    "name": "draw_symmetry_axis",
    "description": "Draw a dashed symmetry line overlay at a specific X or Y coordinate on the canvas.",
    "input_schema": {
        "type": "object",
        "properties": {
            "x_um": _prop("number", "X coordinate in micrometers for a vertical symmetry line"),
            "y_um": _prop("number", "Y coordinate in micrometers for a horizontal symmetry line"),
            "color": _prop("string", "Hex color string for the axis line (default '#00e5ff')"),
        },
        "required": [],
    },
}

_CLEAR_CANVAS_DECORATIONS = {
    "name": "clear_canvas_decorations",
    "description": "Clear all net highlights, color overrides, and drawn symmetry axes from the editor canvas.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_APPLY_RAG_STYLE_MIGRATION = {
    "name": "apply_rag_style_migration",
    "description": (
        "Replicate a high-quality interdigitated or common-centroid analog matching style retrieved "
        "from the ChromaDB vector database, and apply it to target device IDs."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "style_query": _prop("string", "Description of the target style or matching pattern, e.g. 'diff pair common centroid'"),
            "target_device_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of target device IDs to apply the style to.",
            },
        },
        "required": ["style_query", "target_device_ids"],
    },
}

_RECONFIGURE_FLOORPLAN = {
    "name": "reconfigure_floorplan",
    "description": "Reconfigure the layout floorplan grid: adjust row heights, row pitches, or distribute devices across a new number of rows.",
    "input_schema": {
        "type": "object",
        "properties": {
            "aspect_ratio": _prop("number", "Optional target aspect ratio (width/height) or number of rows to pack nodes into"),
            "row_height": _prop("number", "Optional vertical height of each row in micrometers"),
            "row_pitch": _prop("number", "Optional spacing/pitch between rows in micrometers"),
        },
        "required": [],
    },
}

_SHIELD_NET = {
    "name": "shield_net",
    "description": "Shield a critical net (like a clock or sensitive signal path) by inserting dummy isolation cells or empty space channels next to it.",
    "input_schema": {
        "type": "object",
        "properties": {
            "net_name": _prop("string", "Name of the critical net to shield"),
            "shield_type": _prop("string", "Type of shielding: 'dummy' (places dummy cells) or 'empty_space' (creates spacing channels, default 'dummy')"),
            "width_um": _prop("number", "Width of the shield/channel in micrometers (default 0.294)"),
        },
        "required": ["net_name"],
    },
}

_PREVIEW_LAYOUT_GDS = {
    "name": "preview_layout_gds",
    "description": "Render and display a high-fidelity physical KLayout GDS/OAS preview image directly inside the chat panel.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

TOOL_REGISTRY: list = [
    _INSERT_GUARD_RING,
    _HIGHLIGHT_DEVICE_NET,
    _DRAW_SYMMETRY_AXIS,
    _CLEAR_CANVAS_DECORATIONS,
    _APPLY_RAG_STYLE_MIGRATION,
    _RECONFIGURE_FLOORPLAN,
    _SHIELD_NET,
    _PREVIEW_LAYOUT_GDS,
    # Layout inspection
    _READ_LAYOUT,
    _LIST_DEVICES,
    _GET_DEVICE_INFO,
    _SCORE_LAYOUT,
    _GET_LAYOUT_BOUNDS,
    # Device manipulation
    _MOVE_DEVICE,
    _PLACE_SEQUENCE,
    _SWAP_DEVICES,
    _SWAP_ROWS,
    _FLIP_DEVICE,
    _DELETE_DEVICE,
    _ALIGN_DEVICES,
    _ADD_DUMMY,
    _REMOVE_DUMMIES,
    # Diffusion sharing / abutment
    _ABUT_DEVICES,
    _MERGE_SHARED_SOURCE,
    _MERGE_SHARED_DRAIN,
    # Device state
    _LOCK_DEVICE,
    _UNLOCK_DEVICE,
    _SET_DEVICE_COLOR,
    _RESET_DEVICE_COLOR,
    # Grouping
    _CREATE_GROUP,
    # Matching
    _MATCH_DEVICES,
    # DRC & legalisation
    _CHECK_OVERLAPS,
    _RUN_LEGALIZER,
    # Physical cell insertion (order = pipeline execution order)
    _INSERT_ENDCAPS,
    _INSERT_TAPS,
    _INSERT_FILLERS,
    _INSERT_ALL_PHYSICAL_CELLS,
    # Matching placement (low-level)
    _PLACE_COMMON_CENTROID,
    _PLACE_COMMON_CENTROID_2D,
    _INSERT_DUMMIES_AROUND_GROUP,
    # Passive devices
    _PLACE_RESISTOR,
    _PLACE_MOM_CAP,
    _PLACE_MOS_CAP,
    _RESHAPE_PASSIVE,
    # Mid-level: circuit-pattern detection
    _DETECT_MATCHED_PAIRS,
    _DETECT_DIFFERENTIAL_PAIRS,
    _DETECT_CURRENT_MIRRORS,
    _DETECT_CROSS_COUPLED,
    # Mid-level: named placement
    _PLACE_MATCHED_PAIR,
    _PLACE_DIFFERENTIAL_PAIR,
    _PLACE_CURRENT_MIRROR,
    _ADD_DUMMY_GROUP,
    # Mid-level: validation
    _VALIDATE_SYMMETRY,
    _VALIDATE_DUMMY_PRESENCE,
    # Advanced / circuit-level
    _DETECT_CIRCUIT_TYPE,
    _PLACE_COMPARATOR,
    _PLACE_TX_DRIVER,
    _RUN_FULL_LAYOUT_PIPELINE,
    _OPTIMIZE_FOR_MATCHING,
    _OPTIMIZE_FOR_ROUTING,
    # Persistence
    _SAVE_LAYOUT,
]

# Quick lookup: name → schema
TOOL_MAP: dict = {t["name"]: t for t in TOOL_REGISTRY}

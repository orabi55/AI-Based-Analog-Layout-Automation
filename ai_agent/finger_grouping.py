"""
ai_agent/finger_grouping.py
===========================
Utilities for detecting and managing multi-finger transistor structures.

A multi-finger device like MM0 with nf=3 may be represented as:
  - MM0_F1, MM0_F2, MM0_F3  (explicit finger naming)
  - MM0_f1, MM0_f2, MM0_f3  (lowercase)
  - MM0_0,  MM0_1,  MM0_2   (numeric suffixes)
  - MM0 with electrical.nf=3 (single node, nf parameter)

FIXES APPLIED:
  - Bug #BUS: Bus notation MM8<0>, MM8<21> must NOT be treated as fingers.
    Added explicit exclusion of angle-bracket bus notation.
  - Bug #ESCAPE: Removed invalid escape sequences in regex strings.
  - Bug #OVERSPLIT: MM0_1 where MM0 has no other suffixed siblings
    is kept as-is (not treated as finger of a non-existent base).
  - Bug #NUMERIC: Numeric suffix _N only grouped when >= 2 siblings exist.
"""

from __future__ import annotations

import re
from collections import defaultdict


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minimum siblings required before numeric _N suffix is treated as a finger.
# Prevents MM0_1 (a single device with underscore in name) from being
# incorrectly split into base="MM0", finger=1.
MIN_NUMERIC_SIBLINGS = 2


# ---------------------------------------------------------------------------
# Finger Pattern Detection
# ---------------------------------------------------------------------------

# Ordered list of (compiled_pattern, group_index_for_base, group_index_for_num)
# Patterns are tried in order; first match wins.
#
# IMPORTANT: Bus notation like MM8<0>, MM8<21> uses angle brackets and
# must be EXCLUDED. These are separate devices, not fingers of one device.
# We explicitly reject any ID containing '<' or '>'.

_FINGER_PATTERNS: list[re.Pattern] = [
    # MM0_F1, MM0_F2  (uppercase F)
    re.compile(r"^(.+)_F(\d+)$"),
    # MM0_f1, MM0_f2  (lowercase f)
    re.compile(r"^(.+)_f(\d+)$"),
    # MM0_FINGER1, MM0_FINGER2
    re.compile(r"^(.+)_FINGER(\d+)$", re.IGNORECASE),
    # MM0F1, MM0F2  (no underscore, uppercase F)
    re.compile(r"^(.+)F(\d+)$"),
    # MM0_0, MM0_1  (pure numeric — validated separately)
    re.compile(r"^(.+)_(\d+)$"),
]

# Nets that are supply rails — used to skip non-signal nets
_SUPPLY_NETS = {
    "VDD", "VSS", "GND", "AVDD", "AVSS",
    "DVDD", "DVSS", "VCC", "AGND", "DGND"
}


def _is_bus_notation(device_id: str) -> bool:
    """
    Return True if device_id uses bus notation (angle brackets).

    Examples that return True:
        MM8<0>   MM8<21>   NET<3>   BUS<0:7>

    These are SEPARATE devices, NOT fingers of one transistor.
    """
    return "<" in device_id or ">" in device_id


def extract_base_and_finger(device_id: str) -> tuple[str, int]:
    """
    Extract base device name and finger number from a device ID.

    Args:
        device_id: e.g. "MM0_F1", "MM0_f2", "MM0_2", "MM0F3"

    Returns:
        (base_name, finger_number)
        If not a finger device returns (device_id, 0).

    Bus notation (MM8<0>) always returns (device_id, 0).
    """
    # Reject bus notation immediately
    if _is_bus_notation(device_id):
        return device_id, 0

    for pattern in _FINGER_PATTERNS:
        match = pattern.match(device_id)
        if match:
            base_name  = match.group(1)
            finger_num = int(match.group(2))
            # Sanity: base_name must be non-empty and finger_num >= 1
            if base_name and finger_num >= 1:
                return base_name, finger_num

    # No pattern matched — single device
    return device_id, 0


def is_finger_device(device_id: str) -> bool:
    """Return True if device_id indicates a multi-finger component."""
    _, finger = extract_base_and_finger(device_id)
    return finger > 0


def group_fingers(nodes: list[dict]) -> dict[str, list[dict]]:
    """
    Group physical finger devices into logical transistors.

    Rules:
      1. Bus notation (MM8<0>) is NEVER grouped — kept as individual devices.
      2. Explicit finger suffixes (_F1, _f1, F1) always grouped.
      3. Numeric suffixes (_0, _1) only grouped when >= MIN_NUMERIC_SIBLINGS
         share the same base name (prevents false splits).
      4. Dummy devices are never grouped.

    Args:
        nodes: list of device dicts with "id" field

    Returns:
        Dict mapping base_name -> list of finger nodes (sorted by finger number)
        For non-finger devices: base_name == device_id, list has one element.
    """
    # ── Pass 1: Tentative grouping ──────────────────────────────────────────
    tentative: dict[str, list[dict]] = defaultdict(list)

    for node in nodes:
        if node.get("is_dummy"):
            continue

        device_id = node["id"]

        # Bus notation: never group
        if _is_bus_notation(device_id):
            tentative[device_id].append(node)
            continue

        base_name, finger_num = extract_base_and_finger(device_id)
        tentative[base_name].append(node)

    # ── Pass 2: Validate numeric-suffix groups ─────────────────────────────
    # A group that was split via numeric suffix (_0, _1, ...) is only valid
    # if it has >= MIN_NUMERIC_SIBLINGS members.
    # If a group like "MM5" has only one member "MM5_1", it was a false split
    # — keep the original device ID.
    validated: dict[str, list[dict]] = {}

    for base_name, group_nodes in tentative.items():
        if len(group_nodes) == 1:
            # Only one node mapped to this base name.
            # Check if it got here via a numeric split.
            original_id = group_nodes[0]["id"]
            _, finger_num = extract_base_and_finger(original_id)

            if finger_num > 0 and original_id != base_name:
                # Was split but is alone — check if the split used
                # an explicit finger suffix (F1, f1) or numeric (_0)
                # Explicit suffix alone is valid (finger 1 of 1 is unusual
                # but possible). Numeric alone is likely a false split.
                used_numeric = re.match(r"^(.+)_(\d+)$", original_id)
                if used_numeric:
                    # False numeric split — restore original ID
                    validated[original_id] = group_nodes
                else:
                    # Explicit single finger (MM0_F1 with no siblings)
                    # Keep the group as-is (unusual but valid)
                    validated[base_name] = group_nodes
            else:
                # Not a split at all — keep as-is
                validated[base_name] = group_nodes
        else:
            # Multiple nodes — valid group regardless of suffix type
            validated[base_name] = group_nodes

    # ── Pass 3: Sort fingers within each group by finger number ───────────
    for base_name, group_nodes in validated.items():
        group_nodes.sort(
            key=lambda n: extract_base_and_finger(n["id"])[1]
        )

    return validated


# ---------------------------------------------------------------------------
# Logical Device Aggregation
# ---------------------------------------------------------------------------

def aggregate_to_logical_devices(nodes: list[dict]) -> list[dict]:
    """
    Convert physical finger devices to logical transistor representation.

    For multi-finger devices creates ONE logical node with:
      - id          : base name (e.g. "MM0")
      - electrical.nf: total finger count
      - geometry    : position of first finger (leftmost)
      - _fingers    : list of original finger node IDs (for re-expansion)
      - _is_logical : True (marks as aggregated)

    Single-finger devices and bus-notation devices pass through unchanged.
    Dummy devices pass through unchanged.

    Args:
        nodes: list of physical device nodes

    Returns:
        list of logical device nodes
    """
    finger_groups = group_fingers(nodes)
    logical_nodes: list[dict] = []

    for base_name, finger_nodes in finger_groups.items():
        if len(finger_nodes) == 1:
            # Single device — pass through unchanged
            logical_nodes.append(finger_nodes[0])
        else:
            # Multi-finger — aggregate
            logical_node = _create_logical_node(base_name, finger_nodes)
            logical_nodes.append(logical_node)

    # Add dummies separately (never grouped)
    for node in nodes:
        if node.get("is_dummy"):
            logical_nodes.append(node)

    return logical_nodes


def _create_logical_node(base_name: str, finger_nodes: list[dict]) -> dict:
    """
    Create a single logical device node from multiple finger nodes.

    Geometry: uses leftmost finger's X position (not average).
    This makes re-expansion deterministic: start_x + i*pitch.

    Args:
        base_name:    logical device name (e.g. "MM0")
        finger_nodes: list of physical finger nodes sorted by finger number

    Returns:
        Single logical node dict
    """
    # Use first (leftmost) finger as template
    template    = finger_nodes[0]
    first_x     = float(template["geometry"]["x"])
    first_y     = float(template["geometry"]["y"])
    total_width = sum(
        float(n["geometry"].get("width", 0.294)) for n in finger_nodes
    )

    # Aggregate electrical parameters
    electrical     = dict(template.get("electrical", {}))
    electrical["nf"] = len(finger_nodes)

    # Infer per-finger nfin (for effective finger count display)
    nfin = int(electrical.get("nfin", 1))
    electrical["effective_nf"] = len(finger_nodes) * nfin

    finger_ids = [n["id"] for n in finger_nodes]

    return {
        "id":        base_name,
        "type":      template.get("type", "nmos"),
        "is_dummy":  False,
        "geometry": {
            "x":           first_x,
            "y":           first_y,
            "width":       total_width,
            "height":      template["geometry"].get("height", 1.0),
            "orientation": template["geometry"].get("orientation", "R0"),
        },
        "electrical":  electrical,
        "_fingers":    finger_ids,
        "_is_logical": True,
    }


# ---------------------------------------------------------------------------
# Finger Expansion (Logical -> Physical)
# ---------------------------------------------------------------------------

def expand_logical_to_fingers(
    logical_nodes: list[dict],
    original_nodes: list[dict],
    pitch: float = 0.294,
) -> list[dict]:
    """
    Expand logical device positions back to physical finger positions.

    Takes placement decisions made at logical level and applies them
    to individual finger devices.

    Args:
        logical_nodes:  placed logical devices (may have _fingers field)
        original_nodes: original physical finger nodes (for metadata)
        pitch:          x-spacing between consecutive fingers in µm

    Returns:
        list of physical nodes with updated positions
    """
    original_map = {n["id"]: n for n in original_nodes}
    physical_nodes: list[dict] = []

    for logical_node in logical_nodes:
        if not logical_node.get("_is_logical"):
            # Single-finger or bus device — pass through
            physical_nodes.append(logical_node)
            continue

        finger_ids  = logical_node.get("_fingers", [])
        base_x      = float(logical_node["geometry"]["x"])
        base_y      = float(logical_node["geometry"]["y"])
        orientation = logical_node["geometry"].get("orientation", "R0")

        for i, finger_id in enumerate(finger_ids):
            original = original_map.get(finger_id)
            if not original:
                continue

            finger_node = dict(original)
            finger_node["geometry"] = dict(finger_node["geometry"])
            finger_node["geometry"]["x"]           = base_x + (i * pitch)
            finger_node["geometry"]["y"]           = base_y
            finger_node["geometry"]["orientation"] = orientation

            physical_nodes.append(finger_node)

    return physical_nodes


# ---------------------------------------------------------------------------
# Interdigitation Support
# ---------------------------------------------------------------------------

def interdigitate_fingers(
    device_a_logical: dict,
    device_b_logical: dict,
    start_x: float,
    y: float,
    pitch: float = 0.294,
    pattern: str = "ABAB",
) -> list[dict]:
    """
    Create interdigitated finger placement for two matched devices.

    Args:
        device_a_logical: first logical device (e.g. MM0 with nf=3)
        device_b_logical: second logical device (e.g. MM1 with nf=3)
        start_x:          starting x position in µm
        y:                row y coordinate in µm
        pitch:            spacing between fingers in µm
        pattern:          "ABAB" | "ABBA"

    Returns:
        list of physical finger nodes in interdigitated order
    """
    fingers_a = device_a_logical.get("_fingers", [device_a_logical["id"]])
    fingers_b = device_b_logical.get("_fingers", [device_b_logical["id"]])

    sequence = _generate_interdig_sequence(
        len(fingers_a), len(fingers_b), pattern
    )

    interdigitated: list[dict] = []
    counters = {"A": 0, "B": 0}
    fingers = {"A": fingers_a, "B": fingers_b}
    sources = {"A": device_a_logical, "B": device_b_logical}

    for i, label in enumerate(sequence):
        flist = fingers[label]
        ci = counters[label]
        if ci >= len(flist):
            continue
        finger_id = flist[ci]
        counters[label] = ci + 1
        src = sources[label]

        interdigitated.append({
            "id":       finger_id,
            "type":     src.get("type", "nmos"),
            "is_dummy": False,
            "geometry": {
                "x":           start_x + (i * pitch),
                "y":           y,
                "width":       pitch,
                "height":      src["geometry"].get("height", 1.0),
                "orientation": "R0",
            },
            "electrical": dict(src.get("electrical", {})),
        })

    return interdigitated


def _generate_interdig_sequence(
    nf_a: int,
    nf_b: int,
    pattern: str
) -> list[str]:
    """
    Generate interdigitation label sequence.

    Args:
        nf_a:    number of fingers in device A
        nf_b:    number of fingers in device B
        pattern: "ABAB" or "ABBA"

    Returns:
        list of "A" and "B" labels
    """
    if pattern == "ABAB":
        seq: list[str] = []
        for i in range(max(nf_a, nf_b)):
            if i < nf_a:
                seq.append("A")
            if i < nf_b:
                seq.append("B")
        return seq

    elif pattern == "ABBA":
        half_a = nf_a // 2
        return (
            ["A"] * half_a
            + ["B"] * nf_b
            + ["A"] * (nf_a - half_a)
        )

    else:
        return _generate_interdig_sequence(nf_a, nf_b, "ABAB")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_finger_integrity(
    original_nodes: list[dict],
    processed_nodes: list[dict],
) -> dict:
    """
    Verify that all finger devices are preserved after aggregation/expansion.

    Args:
        original_nodes:  physical nodes before processing
        processed_nodes: physical nodes after processing

    Returns:
        dict: {
            "pass":            bool,
            "missing":         List[str],
            "extra":           List[str],
            "original_count":  int,
            "processed_count": int,
            "summary":         str
        }
    """
    original_ids  = {
        n["id"] for n in original_nodes  if not n.get("is_dummy")
    }
    processed_ids = {
        n["id"] for n in processed_nodes if not n.get("is_dummy")
    }

    missing = sorted(original_ids  - processed_ids)
    extra   = sorted(processed_ids - original_ids)
    passed  = (len(missing) == 0 and len(extra) == 0)

    if passed:
        summary = (
            f"Finger integrity OK — "
            f"all {len(original_ids)} device(s) preserved"
        )
    else:
        summary = (
            f"Finger integrity FAILED — "
            f"{len(missing)} missing, {len(extra)} extra"
        )

    return {
        "pass":            passed,
        "missing":         missing,
        "extra":           extra,
        "original_count":  len(original_ids),
        "processed_count": len(processed_ids),
        "summary":         summary,
    }
"""
Device Resolver
===============
Shared resolver for mapping user-facing device references (M1, MM1, MM1_m3)
to physical placement node IDs.

Provides:
- normalize_logical_device_id : M1 → MM1, MM1_m3 → MM1, mm1 → MM1
- logical_base_device_id      : re-export from command_schema
- resolve_layout_device_reference : full resolution with physical IDs
- resolve_many_device_references  : batch resolution
- find_matched_block_for_device   : locate which matched block a device belongs to
- detect_finger_interleaving      : detect ABAB/ABBA from physical node ordering

Functions:
- normalize_logical_device_id:
  - Inputs: ref (str)
  - Outputs: str — canonical logical device ID
- logical_base_device_id:
  - Inputs: device_id (str)
  - Outputs: str — device ID with finger suffixes stripped
- resolve_layout_device_reference:
  - Inputs: ref (str), nodes (list)
  - Outputs: dict with input, logical_id, physical_ids, resolution_type, message
- resolve_many_device_references:
  - Inputs: refs (list), nodes (list)
  - Outputs: list of resolution dicts
- find_matched_block_for_device:
  - Inputs: device_id (str), state (dict)
  - Outputs: dict or None with block_name, partner_devices, technique
- detect_finger_interleaving:
  - Inputs: dev_a (str), dev_b (str), nodes (list)
  - Outputs: str or None — "ABAB", "ABBA", or None
"""

from __future__ import annotations

import re
from typing import Optional

from ai_agent.tools.command_schema import (
    logical_base_device_id,
    _is_filler_or_dummy,
    _FINGER_SUFFIX_RE,
)


# ---------------------------------------------------------------------------
# Alias mapping: M<N> → MM<N>
# ---------------------------------------------------------------------------

#: Regex matching the short alias form (e.g. M1, M10, m2).
_M_ALIAS_RE = re.compile(r"^M(\d+)$", re.IGNORECASE)


def normalize_logical_device_id(ref: str) -> str:
    """Normalize a user-typed device reference to its canonical logical ID.

    Steps:
    1. Strip whitespace and uppercase.
    2. Strip finger suffixes (_m1, _f0, [0], etc.).
    3. Apply M<N> → MM<N> alias (only if not already MM*).

    Examples::

        >>> normalize_logical_device_id("M1")
        'MM1'
        >>> normalize_logical_device_id("MM1")
        'MM1'
        >>> normalize_logical_device_id("mm1")
        'MM1'
        >>> normalize_logical_device_id("MM1_m3")
        'MM1'
        >>> normalize_logical_device_id("MM1_f0")
        'MM1'
        >>> normalize_logical_device_id("MM10_m4")
        'MM10'
        >>> normalize_logical_device_id("M10")
        'MM10'
        >>> normalize_logical_device_id("MM1[0]")
        'MM1'
    """
    token = str(ref or "").strip().upper()
    if not token:
        return ""
    # Strip finger suffix
    token = _FINGER_SUFFIX_RE.sub("", token)
    # Apply M<N> → MM<N> alias
    m = _M_ALIAS_RE.match(token)
    if m:
        token = f"MM{m.group(1)}"
    return token


# ---------------------------------------------------------------------------
# Full resolution
# ---------------------------------------------------------------------------

def _node_id(node: dict) -> str:
    """Extract the primary ID from a placement node dict."""
    return str(node.get("id") or node.get("device_id") or node.get("name") or "")


def resolve_layout_device_reference(
    ref: str,
    nodes: list,
    *,
    include_dummies: bool = False,
) -> dict:
    """Resolve a user device reference against placement nodes.

    Returns::

        {
            "input": "M1",
            "logical_id": "MM1",
            "physical_ids": ["MM1_m1", "MM1_m2", ...],
            "resolution_type": "exact" | "logical_group" | "alias" | "missing",
            "message": optional string
        }

    Resolution rules (in priority order):
    1. Exact node id match.
    2. Logical base id / parent_id match.
    3. Finger-expanded prefix match (MM1_m1, MM1_m2, ... all belong to MM1).
    4. Alias M1 → MM1 then re-resolve.
    5. Filler/edge dummy nodes are excluded unless ``include_dummies=True``.
    6. If no match, ``resolution_type="missing"``.
    """
    ref_str = str(ref or "").strip()
    if not ref_str:
        return {
            "input": ref_str,
            "logical_id": "",
            "physical_ids": [],
            "resolution_type": "missing",
            "message": "Empty reference.",
        }

    # Build lookups
    all_ids: list[str] = []
    id_to_node: dict[str, dict] = {}
    for n in (nodes or []):
        if not isinstance(n, dict):
            continue
        nid = _node_id(n)
        if not nid:
            continue
        all_ids.append(nid)
        id_to_node[nid] = n

    # 1. Exact match
    if ref_str in id_to_node:
        node = id_to_node[ref_str]
        if not include_dummies and _is_filler_or_dummy(ref_str):
            return {
                "input": ref_str,
                "logical_id": ref_str,
                "physical_ids": [ref_str],
                "resolution_type": "exact",
                "message": "Matched a dummy/filler device.",
            }
        return {
            "input": ref_str,
            "logical_id": logical_base_device_id(ref_str),
            "physical_ids": [ref_str],
            "resolution_type": "exact",
            "message": None,
        }

    # Normalize the reference
    logical = normalize_logical_device_id(ref_str)

    # 2. Check exact match after normalization (case-insensitive)
    upper_map = {nid.upper(): nid for nid in all_ids}
    if logical in upper_map:
        nid = upper_map[logical]
        if not include_dummies and _is_filler_or_dummy(nid):
            return {
                "input": ref_str,
                "logical_id": logical,
                "physical_ids": [nid],
                "resolution_type": "exact",
                "message": "Matched a dummy/filler device.",
            }
        return {
            "input": ref_str,
            "logical_id": logical,
            "physical_ids": [nid],
            "resolution_type": "exact",
            "message": None,
        }

    # 3. Find all physical fingers belonging to this logical device
    physical_ids: list[str] = []
    for nid in all_ids:
        if not include_dummies and _is_filler_or_dummy(nid):
            continue
        base = logical_base_device_id(nid).upper()
        if base == logical:
            physical_ids.append(nid)
        else:
            # Also check parent_id
            node = id_to_node.get(nid, {})
            parent = str(node.get("parent_id") or "").upper()
            if parent and parent == logical:
                physical_ids.append(nid)

    if physical_ids:
        res_type = "alias" if ref_str.upper() != logical else "logical_group"
        return {
            "input": ref_str,
            "logical_id": logical,
            "physical_ids": sorted(physical_ids),
            "resolution_type": res_type,
            "message": None,
        }

    # 4. Not found
    return {
        "input": ref_str,
        "logical_id": logical,
        "physical_ids": [],
        "resolution_type": "missing",
        "message": f"No devices found matching '{ref_str}'.",
    }


def resolve_many_device_references(
    refs: list,
    nodes: list,
    *,
    include_dummies: bool = False,
) -> list[dict]:
    """Resolve a batch of device references."""
    return [
        resolve_layout_device_reference(ref, nodes, include_dummies=include_dummies)
        for ref in (refs or [])
    ]


# ---------------------------------------------------------------------------
# Matched-block awareness
# ---------------------------------------------------------------------------

#: Known matched-block patterns for dynamic latch comparator.
_KNOWN_MATCHED_BLOCKS: list[dict] = [
    {
        "block_name": "MM8_MM9_matched",
        "devices": ["MM8", "MM9"],
        "technique": "ABAB_diff_pair",
        "description": "input differential pair",
    },
    {
        "block_name": "MM3_MM0_matched",
        "devices": ["MM3", "MM0"],
        "technique": "ABAB_load_pair",
        "description": "PMOS input/precharge load pair",
    },
    {
        "block_name": "MM2_MM1_matched",
        "devices": ["MM2", "MM1"],
        "technique": "ABAB",
        "description": "output precharge pair",
    },
    {
        "block_name": "MM5_MM4_matched",
        "devices": ["MM5", "MM4"],
        "technique": "symmetric_cross_coupled",
        "description": "PMOS latch pair",
    },
]


def find_matched_block_for_device(
    device_id: str,
    state: dict | None = None,
) -> dict | None:
    """Find which matched block a logical device belongs to.

    Checks both the state's matched-block metadata and the hardcoded
    known blocks for the comparator topology.

    Returns a dict with ``block_name``, ``devices``, ``technique``,
    ``description``, or ``None`` if the device is free (not in a block).
    """
    logical = normalize_logical_device_id(device_id)
    if not logical:
        return None

    # Check state-provided matched blocks
    if isinstance(state, dict):
        trace = state.get("initial_agent_trace") or {}
        strategy = trace.get("strategy") if isinstance(trace, dict) else {}
        if isinstance(strategy, dict):
            matched_blocks = strategy.get("matched_blocks") or {}
            if isinstance(matched_blocks, dict):
                for block_name, block_info in matched_blocks.items():
                    if not isinstance(block_info, dict):
                        continue
                    block_devices = block_info.get("devices") or []
                    if logical in [normalize_logical_device_id(d) for d in block_devices]:
                        return {
                            "block_name": block_name,
                            "devices": list(block_devices),
                            "technique": str(block_info.get("technique") or ""),
                            "description": str(block_info.get("description") or ""),
                        }

            # Also check matching_groups
            groups = strategy.get("matching_groups") or []
            for group in groups:
                if isinstance(group, (list, tuple)):
                    normalized = [normalize_logical_device_id(d) for d in group]
                    if logical in normalized:
                        # Check known blocks for this pair
                        for kb in _KNOWN_MATCHED_BLOCKS:
                            kb_norm = {normalize_logical_device_id(d) for d in kb["devices"]}
                            if set(normalized) == kb_norm:
                                return dict(kb)

    # Fallback: check known blocks
    for kb in _KNOWN_MATCHED_BLOCKS:
        kb_norm = {normalize_logical_device_id(d) for d in kb["devices"]}
        if logical in kb_norm:
            return dict(kb)

    return None


def detect_finger_interleaving(
    dev_a: str,
    dev_b: str,
    nodes: list,
) -> str | None:
    """Detect ABAB or ABBA interleaving from physical finger x-ordering.

    Returns ``"ABAB"`` for strictly alternating, ``"ABBA"`` for symmetric
    cross-coupled, or ``None`` if neither pattern is detected or there are
    fewer than 4 fingers.
    """
    a_norm = normalize_logical_device_id(dev_a)
    b_norm = normalize_logical_device_id(dev_b)

    entries: list[tuple[float, str]] = []
    for n in (nodes or []):
        if not isinstance(n, dict):
            continue
        nid = _node_id(n)
        base = logical_base_device_id(nid).upper()
        if base not in {a_norm, b_norm}:
            continue
        geom = n.get("geometry") if isinstance(n.get("geometry"), dict) else n
        x = geom.get("x")
        if x is None:
            continue
        entries.append((float(x), base))

    if len(entries) < 4:
        return None

    entries.sort(key=lambda e: e[0])
    seq = [owner for _, owner in entries]

    # Check ABAB (strictly alternating)
    if all(seq[i] != seq[i + 1] for i in range(len(seq) - 1)):
        return "ABAB"

    # Check ABBA (symmetric: first half mirrors second half)
    n = len(seq)
    half = n // 2
    if n >= 4 and n % 2 == 0:
        first_half = seq[:half]
        second_half = seq[half:]
        if first_half == list(reversed(second_half)):
            return "ABBA"

    return None

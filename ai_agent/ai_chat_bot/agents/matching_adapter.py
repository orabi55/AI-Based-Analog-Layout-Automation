"""
ai_agent/ai_chat_bot/agents/matching_adapter.py
================================================
Headless (Qt-free) deterministic matching adapter for the AI placement pipeline.

Wraps ``universal_pattern_generator.generate_placement_grid()`` and converts
its integer grid indices to physical micron coordinates using the geometry
engine's constants (STD_PITCH, ROW_PITCH).

The AI flow can call ``apply_matching()`` as a **pure deterministic tool** —
the LLM never computes centroid math or interleaving sequences. It just
decides *which* devices to match and *what technique* to use.

After matching, every member node gets tagged with ``_matched_block`` so
downstream agents (DRC Critic, SA Optimizer) treat the group as rigid.

Supported techniques
--------------------
- INTERDIGITATION      — ratio-based interleaving, 1 row
- COMMON_CENTROID_1D   — quadrant-mirror 1D, 1 row
- COMMON_CENTROID_2D   — quadrant-mirror 2D, 2 rows (point symmetry)
- CUSTOM               — user-defined pattern string (e.g. "ABBA/BAAB")
"""

from __future__ import annotations

import copy
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from ai_agent.matching.universal_pattern_generator import generate_placement_grid

# Physical constants (must match geometry_engine.py)
STD_PITCH = 0.294   # um — standard finger pitch
ROW_PITCH = 0.668   # um — default row-to-row spacing


# ---------------------------------------------------------------------------
# MatchedBlock — immutable record of a matched group
# ---------------------------------------------------------------------------

@dataclass
class MatchedBlock:
    """Record of a matched device group. Serialisable to dict for state."""
    block_id:   str                # unique ID for this matched block
    member_ids: Set[str]           # all finger device IDs in the block
    parent_ids: List[str]          # parent transistor IDs (e.g. ["MM0", "MM1"])
    technique:  str                # e.g. "COMMON_CENTROID_1D"
    anchor_x:   float = 0.0       # leftmost x of the block
    anchor_y:   float = 0.0       # topmost y of the block
    n_rows:     int   = 1         # number of physical rows the block spans

    def to_dict(self) -> dict:
        return {
            "block_id":   self.block_id,
            "member_ids": sorted(self.member_ids),
            "parent_ids": self.parent_ids,
            "technique":  self.technique,
            "anchor_x":   self.anchor_x,
            "anchor_y":   self.anchor_y,
            "n_rows":     self.n_rows,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MatchedBlock":
        return cls(
            block_id   = d["block_id"],
            member_ids = set(d.get("member_ids", [])),
            parent_ids = d.get("parent_ids", []),
            technique  = d.get("technique", "?"),
            anchor_x   = d.get("anchor_x", 0.0),
            anchor_y   = d.get("anchor_y", 0.0),
            n_rows     = d.get("n_rows", 1),
        )


# ---------------------------------------------------------------------------
# Parent / finger helpers
# ---------------------------------------------------------------------------

def _get_parent(device_id: str) -> str:
    """Extract parent transistor name from a finger instance ID."""
    m = re.match(r'^([A-Za-z]+\d+)', device_id)
    return m.group(1) if m else device_id


def _sort_key(device_id: str) -> list:
    """Numeric-aware sort key for finger ordering."""
    nums = re.findall(r'\d+', device_id)
    return [int(x) for x in nums]


# ---------------------------------------------------------------------------
# Public API: apply_matching
# ---------------------------------------------------------------------------

def apply_matching(
    nodes: List[dict],
    device_ids: List[str],
    technique: str,
    pitch: float = STD_PITCH,
    row_pitch: float = ROW_PITCH,
    custom_pattern: Optional[str] = None,
    anchor_x: float = 0.0,
    anchor_y: float = 0.0,
) -> Tuple[List[dict], MatchedBlock]:
    """
    Apply deterministic matching to a subset of devices.

    This is the headless equivalent of ``MatchingEngine.generate_placement``
    from ``matching_engine.py``, but produces *micron coordinates* directly
    instead of pixel positions, and does not require Qt objects.

    Parameters
    ----------
    nodes          : all physical device nodes (used for metadata lookup)
    device_ids     : list of device IDs to include in the matched group
    technique      : "INTERDIGITATION" | "COMMON_CENTROID_1D" | "COMMON_CENTROID_2D" | "CUSTOM"
    pitch          : x-pitch between adjacent fingers (default 0.294 um)
    row_pitch      : y-pitch between rows (default 0.668 um)
    custom_pattern : pattern string for CUSTOM technique (e.g. "ABBA/BAAB")
    anchor_x       : x origin of the matched block (um)
    anchor_y       : y origin of the matched block (um)

    Returns
    -------
    (placed_nodes, matched_block)
        placed_nodes  : list of node dicts with x/y geometry set, tagged with ``_matched_block``
        matched_block : MatchedBlock record for downstream protection

    Raises
    ------
    ValueError
        If no devices are provided or all devices are filtered out.
    ai_agent.matching.universal_pattern_generator.SymmetryError
        If the requested technique cannot produce a symmetric placement
        (e.g. odd finger counts for COMMON_CENTROID_2D).
    """
    if not device_ids:
        raise ValueError("apply_matching: no device IDs provided")

    node_map = {n["id"]: n for n in nodes}

    # 1. Group device IDs by parent transistor
    parent_map: Dict[str, List[str]] = defaultdict(list)
    for did in device_ids:
        parent = _get_parent(did)
        parent_map[parent].append(did)

    sorted_parents = sorted(parent_map.keys())

    # 2. Build token mapping: M0, M1, M2, ... -> parent names
    token_to_parent = {f"M{i}": p for i, p in enumerate(sorted_parents)}
    parent_to_token = {p: f"M{i}" for i, p in enumerate(sorted_parents)}
    token_counts    = {f"M{i}": len(parent_map[p]) for i, p in enumerate(sorted_parents)}

    # 3. Determine row count
    tech_upper = technique.upper()
    n_rows = 1
    if tech_upper == "COMMON_CENTROID_2D":
        n_rows = 2
    elif tech_upper == "CUSTOM" and custom_pattern and "/" in custom_pattern:
        n_rows = custom_pattern.count("/") + 1

    # 4. Call the universal pattern generator (pure math, no GUI)
    grid_coords = generate_placement_grid(
        token_counts, tech_upper, rows=n_rows, custom_str=custom_pattern
    )

    # 5. Map grid indices to physical micron coordinates
    available_ids = {
        p: sorted(parent_map[p], key=_sort_key)
        for p in sorted_parents
    }

    block_id   = f"match_{uuid.uuid4().hex[:8]}"
    member_ids: Set[str] = set()
    placed_nodes: List[dict] = []

    for gc in grid_coords:
        token  = gc["device"]
        x_idx  = gc["x_index"]
        y_idx  = gc["y_index"]

        # Skip DUMMY tokens
        if token == "DUMMY":
            continue

        parent = token_to_parent.get(token)
        if not parent or not available_ids.get(parent):
            continue

        instance_id = available_ids[parent].pop(0)

        # Physical coordinates
        x = round(anchor_x + x_idx * pitch, 6)
        y = round(anchor_y + y_idx * row_pitch, 6)

        # Build placed node (deep copy of original with updated geometry)
        if instance_id in node_map:
            placed = copy.deepcopy(node_map[instance_id])
        else:
            placed = {"id": instance_id, "type": "nmos"}

        geo = placed.setdefault("geometry", {})
        geo["x"] = x
        geo["y"] = y
        geo.setdefault("orientation", "R0")

        # Tag as matched block member
        placed["_matched_block"] = block_id

        placed_nodes.append(placed)
        member_ids.add(instance_id)

    if not placed_nodes:
        raise ValueError(f"apply_matching: no devices placed for {device_ids}")

    matched_block = MatchedBlock(
        block_id   = block_id,
        member_ids = member_ids,
        parent_ids = sorted_parents,
        technique  = tech_upper,
        anchor_x   = anchor_x,
        anchor_y   = anchor_y,
        n_rows     = n_rows,
    )

    print(f"[Matching] Applied {tech_upper} to {sorted_parents} "
          f"({len(member_ids)} fingers, {n_rows} row(s), block={block_id})")

    return placed_nodes, matched_block


# ---------------------------------------------------------------------------
# Utility: detect matching requests from LLM strategy text
# ---------------------------------------------------------------------------

_MATCH_PATTERNS = [
    # "match MM0 and MM1 using common_centroid_1d"
    re.compile(
        r'(?:match|interdigitate|centroid)\s+'
        r'(?:devices?\s+)?'
        r'((?:MM\w+(?:\s*,\s*|\s+and\s+|\s+))+MM\w+)'
        r'.*?(?:using|with|technique)?\s*'
        r'(common_centroid_1d|common_centroid_2d|interdigitation|cc_1d|cc_2d|interdig)',
        re.IGNORECASE,
    ),
    # JSON-style: {"match_groups": [{"devices": ["MM0","MM1"], "technique": "..."}]}
    re.compile(
        r'"match_groups"\s*:\s*\[',
        re.IGNORECASE,
    ),
]

# Technique aliases
_TECHNIQUE_ALIASES = {
    "cc_1d":              "COMMON_CENTROID_1D",
    "cc_2d":              "COMMON_CENTROID_2D",
    "common_centroid_1d": "COMMON_CENTROID_1D",
    "common_centroid_2d": "COMMON_CENTROID_2D",
    "interdig":           "INTERDIGITATION",
    "interdigitation":    "INTERDIGITATION",
    "interdigitate":      "INTERDIGITATION",
}


def parse_matching_requests(strategy_text: str, nodes: List[dict]) -> List[dict]:
    """
    Extract matching requests from the LLM strategy output.

    Looks for structured ``match_groups`` JSON or natural-language phrases.

    Parameters
    ----------
    strategy_text : LLM strategy output
    nodes         : all device nodes (used to expand parent -> finger IDs)

    Returns
    -------
    List of dicts: [{"device_ids": [...], "technique": "..."}]
    """
    import json as _json

    requests: List[dict] = []

    # 1. Try JSON match_groups first
    try:
        # Find JSON block containing match_groups
        for pattern in [
            re.compile(r'```(?:json)?\s*(\{[\s\S]*?"match_groups"[\s\S]*?\})\s*```'),
            re.compile(r'(\{[\s\S]*?"match_groups"[\s\S]*?\})'),
        ]:
            m = pattern.search(strategy_text)
            if m:
                data = _json.loads(m.group(1))
                for mg in data.get("match_groups", []):
                    parent_ids = mg.get("devices", mg.get("parents", []))
                    tech = _TECHNIQUE_ALIASES.get(
                        mg.get("technique", "").lower(),
                        mg.get("technique", "INTERDIGITATION").upper()
                    )
                    # Expand parent IDs to finger IDs
                    finger_ids = _expand_parents_to_fingers(parent_ids, nodes)
                    if finger_ids:
                        requests.append({
                            "device_ids": finger_ids,
                            "parent_ids": parent_ids,
                            "technique":  tech,
                        })
                if requests:
                    return requests
    except Exception:
        pass

    # 2. Try natural-language patterns
    for pat in _MATCH_PATTERNS[:1]:  # only the NL pattern
        for m in pat.finditer(strategy_text):
            device_str = m.group(1)
            tech_str   = m.group(2).lower().strip()
            # Extract device names
            parent_ids = re.findall(r'MM\w+', device_str)
            tech = _TECHNIQUE_ALIASES.get(tech_str, "INTERDIGITATION")
            finger_ids = _expand_parents_to_fingers(parent_ids, nodes)
            if finger_ids:
                requests.append({
                    "device_ids": finger_ids,
                    "parent_ids": parent_ids,
                    "technique":  tech,
                })

    return requests


def _expand_parents_to_fingers(parent_ids: List[str], nodes: List[dict]) -> List[str]:
    """Expand parent transistor IDs to all their finger instance IDs."""
    finger_ids: List[str] = []
    node_ids = {n["id"] for n in nodes}

    for pid in parent_ids:
        # Check if the parent ID itself is a node
        if pid in node_ids:
            finger_ids.append(pid)
        # Check for finger instances: pid_f1, pid_f2, pid_m1, pid_m2, ...
        for n in nodes:
            nid = n["id"]
            if nid == pid:
                continue  # already added
            parent = _get_parent(nid)
            if parent == pid:
                finger_ids.append(nid)

    return sorted(set(finger_ids), key=_sort_key)


# ---------------------------------------------------------------------------
# Block protection utilities
# ---------------------------------------------------------------------------

def get_matched_block_ids(nodes: List[dict]) -> Dict[str, Set[str]]:
    """
    Extract matched block membership from node tags.

    Returns {block_id: {device_id, ...}}
    """
    blocks: Dict[str, Set[str]] = defaultdict(set)
    for n in nodes:
        bid = n.get("_matched_block")
        if bid:
            blocks[bid].add(n["id"])
    return dict(blocks)


def is_in_matched_block(node: dict) -> bool:
    """Check if a node is part of a matched block."""
    return bool(node.get("_matched_block"))


def move_matched_block(nodes: List[dict], block_id: str, dx: float, dy: float) -> None:
    """
    Move all members of a matched block by (dx, dy) as a rigid body.

    Modifies nodes in-place.
    """
    for n in nodes:
        if n.get("_matched_block") == block_id:
            geo = n.get("geometry", {})
            geo["x"] = round(float(geo.get("x", 0)) + dx, 6)
            geo["y"] = round(float(geo.get("y", 0)) + dy, 6)

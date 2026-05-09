"""
Group Placer Core Logic
=======================
Named-pair placement helpers that wrap the existing common-centroid /
interdigitation engines. These are the FC-callable tools the LLM uses to
respond to requests like "place this differential pair" or "make this current
mirror common-centroid".

All placement is delegated to existing modules:
  - place_common_centroid       ← ai_agent.core.common_centroid
  - place_common_centroid_2d    ← ai_agent.core.common_centroid
  - insert_dummies_around_group ← ai_agent.core.common_centroid

No new placement algorithms are introduced — these are name/intent shims.
"""
from __future__ import annotations

from typing import List

from ai_agent.core.interfaces import LayoutToolResult, wrap_tool
from ai_agent.core.common_centroid import (
    place_common_centroid,
    place_common_centroid_2d,
    insert_dummies_around_group,
)


def _resolve_finger_ids(parent_id: str, nodes: list) -> List[str]:
    """Return all node IDs whose ID starts with parent_id (parent + fingers)."""
    pid = str(parent_id)
    out: List[str] = []
    for n in nodes:
        nid = str(n.get("id", ""))
        if nid == pid or nid.startswith(pid + "_") or nid.startswith(pid + "<"):
            out.append(nid)
    return out


def _row_y_for(nodes: list, parent_id: str, default: float = 0.0) -> float:
    """Use the existing Y of the first finger node belonging to parent_id."""
    finger_ids = _resolve_finger_ids(parent_id, nodes)
    for n in nodes:
        if str(n.get("id", "")) in finger_ids:
            try:
                return float(n.get("geometry", {}).get("y", default))
            except (TypeError, ValueError):
                continue
    return default


def _start_x_for(nodes: list, parent_ids: List[str], default: float = 0.0) -> float:
    """Use the leftmost x of the involved finger nodes."""
    target: set = set()
    for pid in parent_ids:
        target.update(_resolve_finger_ids(pid, nodes))
    xs: List[float] = []
    for n in nodes:
        if str(n.get("id", "")) in target:
            try:
                xs.append(float(n.get("geometry", {}).get("x", default)))
            except (TypeError, ValueError):
                continue
    return min(xs) if xs else default


# ---------------------------------------------------------------------------
# Matched pair (ABBA via place_common_centroid)
# ---------------------------------------------------------------------------

@wrap_tool
def place_matched_pair(
    nodes: list,
    device_a: str,
    device_b: str,
    pdk: dict,
    start_x: float = None,
    row_y:   float = None,
) -> LayoutToolResult:
    """Interdigitate two matched devices in an ABBA common-centroid pattern.

    device_a / device_b are *parent* device IDs; their fingers are looked up
    automatically from `nodes`.
    """
    group_a_ids = _resolve_finger_ids(device_a, nodes)
    group_b_ids = _resolve_finger_ids(device_b, nodes)
    if not group_a_ids or not group_b_ids:
        return LayoutToolResult(
            success=False,
            message=f"place_matched_pair: device(s) not found ({device_a!r}, {device_b!r})",
            nodes=list(nodes),
        )

    sx = float(start_x) if start_x is not None else _start_x_for(nodes, [device_a, device_b])
    ry = float(row_y)   if row_y   is not None else _row_y_for(nodes, device_a)

    id_map  = {n["id"]: n for n in nodes}
    group_a = [id_map[did] for did in group_a_ids if did in id_map]
    group_b = [id_map[did] for did in group_b_ids if did in id_map]

    return place_common_centroid(
        group_a, group_b,
        start_x = sx,
        row_y   = ry,
        pdk     = pdk,
        pattern = "ABBA",
    )


# ---------------------------------------------------------------------------
# Differential pair (ABAB interdigitation — same engine, different label)
# ---------------------------------------------------------------------------

@wrap_tool
def place_differential_pair(
    nodes: list,
    device_a: str,
    device_b: str,
    pdk: dict,
    start_x: float = None,
    row_y:   float = None,
) -> LayoutToolResult:
    """Interdigitate a differential pair (ABAB)."""
    group_a_ids = _resolve_finger_ids(device_a, nodes)
    group_b_ids = _resolve_finger_ids(device_b, nodes)
    if not group_a_ids or not group_b_ids:
        return LayoutToolResult(
            success=False,
            message=f"place_differential_pair: device(s) not found ({device_a!r}, {device_b!r})",
            nodes=list(nodes),
        )

    sx = float(start_x) if start_x is not None else _start_x_for(nodes, [device_a, device_b])
    ry = float(row_y)   if row_y   is not None else _row_y_for(nodes, device_a)

    id_map  = {n["id"]: n for n in nodes}
    group_a = [id_map[did] for did in group_a_ids if did in id_map]
    group_b = [id_map[did] for did in group_b_ids if did in id_map]

    return place_common_centroid(
        group_a, group_b,
        start_x = sx,
        row_y   = ry,
        pdk     = pdk,
        pattern = "ABAB",
    )


# ---------------------------------------------------------------------------
# Current mirror — N-device common-centroid via place_common_centroid_2d
# ---------------------------------------------------------------------------

@wrap_tool
def place_current_mirror(
    nodes: list,
    device_ids: list,
    pdk: dict,
    start_x: float = None,
    row_y:   float = None,
) -> LayoutToolResult:
    """Place a current mirror cluster (N devices) common-centroid.

    device_ids: list of parent device IDs sharing a gate net.
    """
    if not device_ids or len(device_ids) < 2:
        return LayoutToolResult(
            success=False,
            message="place_current_mirror: need at least 2 device IDs",
            nodes=list(nodes),
        )

    sx = float(start_x) if start_x is not None else _start_x_for(nodes, device_ids)
    ry = float(row_y)   if row_y   is not None else _row_y_for(nodes, device_ids[0])

    devices: List[dict] = []
    for pid in device_ids:
        finger_ids = _resolve_finger_ids(pid, nodes)
        dev_nodes  = [n for n in nodes if str(n.get("id", "")) in finger_ids]
        devices.append({
            "id":      pid,
            "fingers": len(dev_nodes) or 1,
            "nodes":   dev_nodes,
        })

    return place_common_centroid_2d(
        devices,
        start_x = sx,
        row_y   = ry,
        pdk     = pdk,
    )


# ---------------------------------------------------------------------------
# Dummy group insertion (alias to insert_dummies_around_group)
# ---------------------------------------------------------------------------

@wrap_tool
def add_dummy_group(
    nodes: list,
    group_node_ids: list,
    pdk: dict,
    n_dummies: int = 1,
) -> LayoutToolResult:
    """Insert N structural dummy fingers on each side of a matched group.

    Thin name-alias around insert_dummies_around_group from common_centroid;
    surfaced as a separate tool so the LLM can invoke it via the natural
    "add dummies around X" phrasing.
    """
    if not group_node_ids:
        return LayoutToolResult(
            success=False,
            message="add_dummy_group: group_node_ids must not be empty",
            nodes=list(nodes),
        )

    id_set      = set(group_node_ids)
    group_nodes = [n for n in nodes if str(n.get("id", "")) in id_set]
    if not group_nodes:
        return LayoutToolResult(
            success=False,
            message="add_dummy_group: none of the requested IDs were found",
            nodes=list(nodes),
        )

    return insert_dummies_around_group(
        group_nodes, pdk,
        n_dummies = int(n_dummies),
    )

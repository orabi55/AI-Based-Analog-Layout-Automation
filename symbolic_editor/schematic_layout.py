# -*- coding: utf-8 -*-
"""
schematic_layout.py
Pure-Python schematic layout engine for symbolic_editor.

Contains:
  - DeviceGroup dataclass  (topology detection result)
  - detect_groups()        (diff pair / current mirror / cascode / latch / tail)
  - place_group()          (per-kind placement templates)
  - compute_signal_depths() (BFS signal-flow depth)
  - _IntervalSet           (pure-Python interval tracker, no external deps)
  - ChannelRouter          (greedy left-edge Manhattan channel router)

No LangGraph, no LLM, no layout-engine imports.
"""
from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Literal

from PySide6.QtCore import QLineF, QPointF

# ── Inline power/ground checks (avoids circular import with schematic_view) ───
_SL_POWER_NETS  = {"VDD", "AVDD", "VCC", "PWR", "VDDA", "VDDIO"}
_SL_GROUND_NETS = {"GND", "VSS", "GNDA", "GND_A", "AGND"}

def _sl_is_power(n: str)  -> bool:
    u = n.upper()
    return u in _SL_POWER_NETS or u.startswith("VDD")

def _sl_is_ground(n: str) -> bool:
    u = n.upper()
    return u in _SL_GROUND_NETS or u.startswith("VSS") or u.startswith("GND")

# ── Shared geometry constants (must match schematic_view.py) ─────────────────
CELL_X: float = 160.0   # horizontal cell pitch (px)
CELL_Y: float = 220.0   # vertical cell pitch (px)
CH_H:   float = 26.0    # MosfetItem half-channel height
PIN_EXT: float = 12.0   # MosfetItem pin extension

# ── Type aliases ─────────────────────────────────────────────────────────────
GroupKind = Literal[
    "diff_pair", "current_mirror", "cascode",
    "cross_coupled_latch", "tail", "single", "passive",
]


# ── DeviceGroup ───────────────────────────────────────────────────────────────
@dataclass
class DeviceGroup:
    """Detected functional group of logical devices."""
    kind: GroupKind
    members: list[str]                      # logical device IDs
    symmetry_axis: bool = False             # True → bilateral symmetry inside group
    role_map: dict[str, str] = field(default_factory=dict)   # id → "left"/"right"/"top"/"bottom"/"centre"
    extras: dict = field(default_factory=dict)               # free-form per-kind data


# ── Topology detection ────────────────────────────────────────────────────────
def detect_groups(
    devs: list[dict],
    terminal_nets: dict[str, dict[str, str]],
) -> list[DeviceGroup]:
    """
    Detect functional groups in a flat list of logical devices.

    Parameters
    ----------
    devs:
        List of logical-device dicts, each with at least:
          {"id": str, "type": str, "terminal_nets": {terminal: net}}
    terminal_nets:
        Mapping  device_id -> {terminal -> net_name}  (finger-level keys OK;
        the caller should have already resolved to logical-device IDs, or we
        look up the representative entry from devs[i]["terminal_nets"]).

    Returns
    -------
    list[DeviceGroup] where every device appears in exactly one group.
    """
    # Build per-device net lookup (prefer devs[i]["terminal_nets"] which the
    # caller already resolved to the logical device).
    dev_map: dict[str, dict] = {d["id"]: d for d in devs}

    def nets(did: str) -> dict[str, str]:
        d = dev_map.get(did, {})
        tn = d.get("terminal_nets", {})
        if not tn:
            tn = terminal_nets.get(did, {})
        return tn

    def net_of(did: str, terminal: str) -> str:
        return nets(did).get(terminal, "")

    def dev_type(did: str) -> str:
        return dev_map.get(did, {}).get("type", "nmos").lower()

    def is_passive(did: str) -> bool:
        return dev_type(did) in {"cap", "res", "ind", "capacitor", "resistor"}

    # Build adjacency: net → [(device_id, terminal)]
    net_adj: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for d in devs:
        did = d["id"]
        for terminal, net in nets(did).items():
            if net:
                net_adj[net].append((did, terminal))

    assigned: set[str] = set()
    groups: list[DeviceGroup] = []
    all_ids = sorted([d["id"] for d in devs])

    def is_diode(did: str) -> bool:
        return net_of(did, "G") == net_of(did, "D") and net_of(did, "G") != ""

    def _is_right(did: str) -> bool:
        d = next((x for x in devs if x["id"] == did), None)
        if not d: return False
        for net in d.get("terminal_nets", {}).values():
            if net.upper() in {"VINN", "VOUTP", "VY", "OUTN", "INN"}:
                return True
        return False

    # --- PASS 1: Strong Pairs (Latches, Diff Pairs, Mirrors) ---
    for i, a in enumerate(all_ids):
        if a in assigned or is_passive(a):
            continue
        
        type_a = dev_type(a)
        matched = False

        for b in all_ids[i + 1:]:
            if b in assigned or is_passive(b):
                continue
            type_b = dev_type(b)

            # ── Cross-coupled latch ───────────────────────────────────
            if (net_of(a, "D") and net_of(b, "G") and net_of(a, "D") == net_of(b, "G") and
                    net_of(b, "D") and net_of(a, "G") and net_of(b, "D") == net_of(a, "G") and
                    type_a == type_b):
                left_dev, right_dev = (b, a) if _is_right(a) else (a, b)
                grp = DeviceGroup("cross_coupled_latch", [left_dev, right_dev], True, {left_dev: "left", right_dev: "right"})
                groups.append(grp)
                assigned.update([a, b])
                matched = True
                break

            # ── Differential pair ─────────────────────────────────────
            _is_cross = (net_of(a, "D") == net_of(b, "G") and net_of(b, "D") == net_of(a, "G"))
            if (not _is_cross and type_a == type_b and
                    net_of(a, "S") and net_of(b, "S") and net_of(a, "S") == net_of(b, "S") and
                    not _sl_is_power(net_of(a, "S")) and not _sl_is_ground(net_of(a, "S")) and
                    net_of(a, "G") and net_of(b, "G") and net_of(a, "G") != net_of(b, "G") and
                    net_of(a, "D") and net_of(b, "D") and net_of(a, "D") != net_of(b, "D")):
                left_dev, right_dev = (b, a) if _is_right(a) else (a, b)
                grp = DeviceGroup("diff_pair", [left_dev, right_dev], True, {left_dev: "left", right_dev: "right"}, {"common_source": net_of(a, "S")})
                groups.append(grp)
                assigned.update([a, b])
                matched = True
                break

            # ── Current mirror ────────────────────────────────────────
            if (type_a == type_b and
                    net_of(a, "G") and net_of(b, "G") and net_of(a, "G") == net_of(b, "G") and
                    (is_diode(a) or is_diode(b))):
                ref_side = a if is_diode(a) else b
                copy_side = b if ref_side == a else a
                grp = DeviceGroup("current_mirror", [ref_side, copy_side], True, {ref_side: "left", copy_side: "right"}, {"reference": ref_side, "copy": copy_side})
                groups.append(grp)
                assigned.update([a, b])
                matched = True
                break

    # --- PASS 2: Weak Pairs (Cascode) ---
    for i, a in enumerate(all_ids):
        if a in assigned or is_passive(a):
            continue
        type_a = dev_type(a)
        matched = False

        for b in all_ids[i + 1:]:
            if b in assigned or is_passive(b):
                continue
            type_b = dev_type(b)
            # ── Cascode ───────────────────────────────────────────────
            if (type_a == type_b and net_of(a, "D") and net_of(b, "S") and net_of(a, "D") == net_of(b, "S")):
                grp = DeviceGroup("cascode", [a, b], False, {a: "bottom", b: "top"})
                groups.append(grp)
                assigned.update([a, b])
                matched = True
                break

            if (type_a == type_b and net_of(b, "D") and net_of(a, "S") and net_of(b, "D") == net_of(a, "S")):
                grp = DeviceGroup("cascode", [b, a], False, {b: "bottom", a: "top"})
                groups.append(grp)
                assigned.update([a, b])
                matched = True
                break

        if matched:
            continue

        # ── Tail: single device whose drain feeds a diff_pair source ──
        diff_source_nets = {
            g.extras.get("common_source", "")
            for g in groups
            if g.kind == "diff_pair"
        }
        if net_of(a, "D") in diff_source_nets and net_of(a, "D"):
            grp = DeviceGroup("tail", [a], False, {a: "centre"})
            groups.append(grp)
            assigned.add(a)
            continue

        # ── Single ────────────────────────────────────────────────────
        if a not in assigned:
            groups.append(DeviceGroup(kind="single", members=[a], role_map={a: "centre"}))
            assigned.add(a)

    # ── Passives ──────────────────────────────────────────────────────
    for did in all_ids:
        if did not in assigned and is_passive(did):
            groups.append(DeviceGroup(kind="passive", members=[did], role_map={did: "centre"}))
            assigned.add(did)

    # ── Stragglers (shouldn't happen) ────────────────────────────────
    for did in all_ids:
        if did not in assigned:
            groups.append(DeviceGroup(kind="single", members=[did], role_map={did: "centre"}))
            assigned.add(did)

    return groups


# ── Per-kind placement templates ─────────────────────────────────────────────
def place_group(
    group: DeviceGroup,
    origin: QPointF,
) -> dict[str, dict]:
    """
    Return {device_id: {"cx": float, "cy": float, "mirrored": bool}}
    Coordinates are absolute scene positions.
    """
    ox, oy = origin.x(), origin.y()
    result: dict[str, dict] = {}

    kind = group.kind
    members = group.members

    if kind == "diff_pair":
        # Side-by-side, symmetric about ox
        left_id  = next((k for k, v in group.role_map.items() if v == "left"),  members[0])
        right_id = next((k for k, v in group.role_map.items() if v == "right"), members[-1])
        result[left_id]  = {"cx": ox - CELL_X * 0.5, "cy": oy, "mirrored": False}
        result[right_id] = {"cx": ox + CELL_X * 0.5, "cy": oy, "mirrored": True}

    elif kind == "current_mirror":
        ref_id  = next((k for k, v in group.role_map.items() if v == "left"),  members[0])
        copy_id = next((k for k, v in group.role_map.items() if v == "right"), members[-1])
        result[ref_id]  = {"cx": ox - CELL_X * 0.5, "cy": oy, "mirrored": False}
        result[copy_id] = {"cx": ox + CELL_X * 0.5, "cy": oy, "mirrored": False}

    elif kind == "cross_coupled_latch":
        left_id  = next((k for k, v in group.role_map.items() if v == "left"),  members[0])
        right_id = next((k for k, v in group.role_map.items() if v == "right"), members[-1])
        # Latches face INWARD: left device gate on right (mirrored=True), right device gate on left (mirrored=False)
        result[left_id]  = {"cx": ox - CELL_X * 0.5, "cy": oy, "mirrored": True}
        result[right_id] = {"cx": ox + CELL_X * 0.5, "cy": oy, "mirrored": False}

    elif kind == "cascode":
        # Stacked vertically: bottom device at oy, top (cascode) at oy - CELL_Y*1.4
        bottom_id = next((k for k, v in group.role_map.items() if v == "bottom"), members[0])
        top_id    = next((k for k, v in group.role_map.items() if v == "top"),    members[-1])
        result[bottom_id] = {"cx": ox, "cy": oy,                "mirrored": False}
        result[top_id]    = {"cx": ox, "cy": oy - CELL_Y * 1.4, "mirrored": False}

    elif kind in ("tail", "single", "passive"):
        for did in members:
            result[did] = {"cx": ox, "cy": oy, "mirrored": False}

    else:
        for did in members:
            result[did] = {"cx": ox, "cy": oy, "mirrored": False}

    return result


def group_bounding_width(group: DeviceGroup) -> float:
    """Approximate pixel width of the group's footprint."""
    kind = group.kind
    n = len(group.members)
    if kind in ("diff_pair", "current_mirror"):
        return CELL_X * 1.2
    if kind == "cross_coupled_latch":
        return CELL_X * 1.4
    if kind == "cascode":
        return CELL_X * 0.8
    return CELL_X * 0.8 * max(n, 1)


def group_bounding_height(group: DeviceGroup) -> float:
    """Approximate pixel height of the group's footprint."""
    if group.kind == "cascode":
        return CELL_Y * 1.4 + CELL_Y
    return CELL_Y


# ── BFS signal-flow depth ─────────────────────────────────────────────────────
_PRIMARY_INPUT_RE = re.compile(
    r"^(VIN[PN]?|IN[PN]?|CLK[BN]?|RST[B]?|EN[B]?)$", re.IGNORECASE
)


def compute_signal_depths(
    devs: list[dict],
    terminal_nets: dict[str, dict[str, str]],
    primary_inputs: list[str] | None = None,
) -> dict[str, int]:
    """
    BFS over bipartite graph (devices ↔ nets) to assign a signal-flow depth
    to each logical device.

    primary_inputs: explicit list of primary input net names.
                    Falls back to regex matching if None.

    Returns {device_id: depth_int}.  Depth 0 = closest to inputs.
    """
    def nets_of(did: str) -> dict[str, str]:
        d = next((x for x in devs if x["id"] == did), {})
        tn = d.get("terminal_nets", {})
        if not tn:
            tn = terminal_nets.get(did, {})
        return tn

    all_nets: set[str] = set()
    for d in devs:
        for net in nets_of(d["id"]).values():
            if net:
                all_nets.add(net)

    # Identify primary input nets
    if primary_inputs:
        seed_nets = set(primary_inputs)
    else:
        seed_nets = {n for n in all_nets if _PRIMARY_INPUT_RE.match(n)}

    # Also treat power/ground as depth-0 seeds so rails don't propagate weirdly
    # use inline helpers to avoid circular import
    seed_nets |= {n for n in all_nets if _sl_is_power(n) or _sl_is_ground(n)}

    # BFS: net_depth[net] = int
    net_depth: dict[str, int] = {n: 0 for n in seed_nets}
    # dev_depth[dev] = int
    dev_depth: dict[str, int] = {}

    # Build fast lookups
    # net → list of (device_id, terminal) connected to it
    net_to_devs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for d in devs:
        did = d["id"]
        for terminal, net in nets_of(did).items():
            if net:
                net_to_devs[net].append((did, terminal))

    # BFS from seed nets
    queue: deque[str] = deque(seed_nets)
    visited_nets: set[str] = set(seed_nets)

    while queue:
        net = queue.popleft()
        nd = net_depth[net]
        # Propagate through devices that have this net on a gate/input terminal
        for did, terminal in net_to_devs[net]:
            # Gate drives the device output
            if terminal.upper() in ("G", "1", "+"):
                old = dev_depth.get(did, 10**9)
                new_dd = nd + 1
                if new_dd < old:
                    dev_depth[did] = new_dd
                    # Now propagate to the device's drain net
                    d_net = nets_of(did).get("D") or nets_of(did).get("2") or nets_of(did).get("-")
                    if d_net and d_net not in visited_nets:
                        net_depth[d_net] = new_dd + 1
                        visited_nets.add(d_net)
                        queue.append(d_net)

    # Any device without a depth gets max+1
    max_depth = max(dev_depth.values(), default=0)
    for d in devs:
        did = d["id"]
        if did not in dev_depth:
            dev_depth[did] = max_depth + 1

    return dev_depth


# ── Pure-Python interval set ──────────────────────────────────────────────────
class _IntervalSet:
    """
    Tracks reserved intervals on a 1D axis.
    No external dependencies.
    """
    def __init__(self) -> None:
        self._intervals: list[tuple[float, float]] = []

    def is_free(self, lo: float, hi: float) -> bool:
        for a, b in self._intervals:
            if lo < b and hi > a:   # overlapping
                return False
        return True

    def reserve(self, lo: float, hi: float) -> None:
        self._intervals.append((min(lo, hi), max(lo, hi)))


# ── Channel router ────────────────────────────────────────────────────────────
class ChannelRouter:
    """
    Greedy left-edge Manhattan channel router.

    Horizontal channels are y-stripes between device rows.
    Vertical channels are x-stripes between device columns.

    Usage::
        router = ChannelRouter(h_ys=[...], v_xs=[...])
        lines = router.route(net_name, [(x1,y1), (x2,y2), ...])
        # returns list of (x1, y1, x2, y2) tuples for QGraphicsLineItem

    If no channel is available returns an empty list (caller should
    fall back to label mode).
    """

    def __init__(
        self,
        h_ys: list[float],   # y-coordinates of available horizontal channels
        v_xs: list[float],   # x-coordinates of available vertical channels
    ) -> None:
        # One _IntervalSet per channel
        self._h_slots: dict[float, _IntervalSet] = {y: _IntervalSet() for y in h_ys}
        self._v_slots: dict[float, _IntervalSet] = {x: _IntervalSet() for x in v_xs}

    def route(
        self,
        net: str,  # noqa: ARG002  (for future debug labelling)
        terminals: list[tuple[float, float]],
    ) -> list[tuple[float, float, float, float]]:
        """
        Route a net through Manhattan segments.

        Returns list of (x1, y1, x2, y2) line segments, or [] if routing fails.
        """
        if len(terminals) < 2:
            return []

        xs = [t[0] for t in terminals]
        ys = [t[1] for t in terminals]
        x_lo, x_hi = min(xs), max(xs)
        y_lo, y_hi = min(ys), max(ys)

        # Find a free horizontal channel in the y-range of this net
        trunk_y: float | None = None
        for chy in sorted(self._h_slots.keys()):
            if y_lo <= chy <= y_hi:
                if self._h_slots[chy].is_free(x_lo - 4, x_hi + 4):
                    trunk_y = chy
                    break

        if trunk_y is None:
            return []  # caller falls back to label mode

        # Reserve trunk
        self._h_slots[trunk_y].reserve(x_lo - 4, x_hi + 4)

        segments: list[tuple[float, float, float, float]] = []

        # Horizontal trunk
        segments.append((x_lo, trunk_y, x_hi, trunk_y))

        # Vertical legs from each terminal to trunk
        for tx, ty in terminals:
            # Find a vertical channel near tx
            vch = self._nearest_free_v(tx, ty, trunk_y)
            if vch is None:
                vch = tx   # fall back to exact terminal x
            else:
                self._v_slots[vch].reserve(min(ty, trunk_y) - 4, max(ty, trunk_y) + 4)
            # Leg: terminal → trunk level
            segments.append((tx,  ty,  vch, ty))         # horizontal jog to v-channel
            segments.append((vch, ty,  vch, trunk_y))     # vertical to trunk
            segments.append((vch, trunk_y, tx, trunk_y))  # horizontal back to terminal x (may be 0-length)

        return segments

    def _nearest_free_v(
        self, x: float, y_lo: float, y_hi: float
    ) -> float | None:
        best: tuple[float, float] | None = None  # (distance, x)
        for vx, slot in self._v_slots.items():
            if slot.is_free(min(y_lo, y_hi) - 4, max(y_lo, y_hi) + 4):
                dist = abs(vx - x)
                if best is None or dist < best[0]:
                    best = (dist, vx)
        return best[1] if best else None


# ── Outer band layout ─────────────────────────────────────────────────────────
def build_band_layout(
    devs: list[dict],
    terminal_nets: dict[str, dict[str, str]],
    canvas_width: float = 800.0,
) -> tuple[list[DeviceGroup], dict[str, dict]]:
    """
    Full schematic layout engine.

    1. detect_groups()
    2. Assign groups to bands (0=VDD, 1=PMOS, 2=NMOS, 3=GND)
    3. Order groups within band by signal-flow depth
    4. call place_group() with computed origins
    5. Symmetry post-processing: bilateral pairs mirrored about X_AXIS

    Returns (groups, positions) where positions is
      {device_id: {"cx", "cy", "mirrored", "rank"}}
    """
    # use inline helpers (no circular import needed)

    groups = detect_groups(devs, terminal_nets)
    depths = compute_signal_depths(devs, terminal_nets)

    def dev_nets(did: str) -> dict[str, str]:
        d = next((x for x in devs if x["id"] == did), {})
        tn = d.get("terminal_nets", {})
        if not tn:
            tn = terminal_nets.get(did, {})
        return tn

    def dev_type(did: str) -> str:
        d = next((x for x in devs if x["id"] == did), {})
        return d.get("type", "nmos").lower()

    def band_of(group: DeviceGroup) -> int:
        """Band 0=VDD-touching PMOS, 1=other PMOS, 2=other NMOS, 3=GND-touching NMOS."""
        for did in group.members:
            tn = dev_nets(did)
            typ = dev_type(did)
            s, d = tn.get("S", ""), tn.get("D", "")
            if typ == "pmos":
                if _sl_is_power(s) or _sl_is_power(d):
                    return 0
        for did in group.members:
            if dev_type(did) == "pmos":
                return 1
        for did in group.members:
            tn = dev_nets(did)
            s, d = tn.get("S", ""), tn.get("D", "")
            if _sl_is_ground(s) or _sl_is_ground(d):
                return 3
        return 2

    # Assign bands
    bands: dict[int, list[DeviceGroup]] = defaultdict(list)
    for g in groups:
        bands[band_of(g)].append(g)

    # Sort within each band by mean signal depth of members (ascending)
    for bnd in bands.values():
        bnd.sort(key=lambda g: sum(depths.get(m, 0) for m in g.members) / max(len(g.members), 1))

    # Band y-coordinates — generous gaps so symbols never overlap
    BAND_Y = {0: 0.0, 1: CELL_Y * 1.5, 2: CELL_Y * 3.0, 3: CELL_Y * 4.5}

    # Compute X_AXIS (horizontal centre)
    X_AXIS = canvas_width / 2.0

    positions: dict[str, dict] = {}

    for band_idx in sorted(bands.keys()):
        band_groups = bands[band_idx]
        band_y = BAND_Y[band_idx]

        # Total width needed
        total_w = sum(group_bounding_width(g) for g in band_groups)
        spacing = CELL_X * 0.6
        total_w += spacing * max(len(band_groups) - 1, 0)

        # Start x so the band is centred on X_AXIS
        x_cursor = X_AXIS - total_w / 2.0

        for g in band_groups:
            gw = group_bounding_width(g)
            origin = QPointF(x_cursor + gw / 2.0, band_y)
            gpos = place_group(g, origin)
            for did, pos in gpos.items():
                positions[did] = {**pos, "rank": band_idx}
            x_cursor += gw + spacing

    # ── Symmetry post-processing ─────────────────────────────────────
    for g in groups:
        if not g.symmetry_axis:
            continue
        left_ids  = [k for k, v in g.role_map.items() if v == "left"]
        right_ids = [k for k, v in g.role_map.items() if v == "right"]
        for lid, rid in zip(left_ids, right_ids):
            if lid in positions and rid in positions:
                lx = positions[lid]["cx"]
                positions[rid]["cx"] = 2.0 * X_AXIS - lx
                positions[rid]["mirrored"] = True
        # Centre singletons
        centre_ids = [k for k, v in g.role_map.items() if v == "centre"]
        for cid in centre_ids:
            if cid in positions:
                positions[cid]["cx"] = X_AXIS

    return groups, positions

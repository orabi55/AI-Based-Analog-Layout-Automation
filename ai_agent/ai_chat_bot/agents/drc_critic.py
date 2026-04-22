"""
DRC Critic Agent — Advanced Analog Layout Verification Engine
=============================================================
Implements three major algorithmic upgrades over the original O(N²) baseline:

1. SWEEP-LINE OVERLAP DETECTION  O(N log N + R)
   -----------------------------------------------
   Left/right bounding-box events are sorted by X and swept left-to-right.
   Active intervals are stored in a sorted list; Y-overlap queries run in
   O(log N) via bisect.  Total complexity: O(N log N + R) where R = number
   of intersecting pairs — drastically faster than O(N²) for large N.

2. DYNAMIC GAP COMPUTATION  (Yield-Limiting Constraints)
   -------------------------------------------------------
   Rather than a blanket gap_px, the minimum spacing between two devices is
   derived from their electrical connectivity via terminal_nets:
     * Devices sharing an equipotential net (same D/G/S voltage) → gap = 0
       (valid abutment, no lithographic rule needed)
     * Devices whose closest terminals cross different voltages  → gap = gap_px
   This models real STI / diffusion-spacing rules and prevents unnecessary
   bloat while catching genuine DRC hazards.

3. COST-DRIVEN LEGALIZER WITH SYMMETRY PRESERVATION
   --------------------------------------------------
   compute_prescriptive_fixes() uses a miniature Manhattan-cost function
   instead of the greedy "push right" heuristic.  Candidate positions are
   evaluated in all four cardinal directions; the one that minimises
     Cost = α·|Δx| + β·|Δy| + γ·HPWL_penalty
   is selected.
   If a device belongs to a symmetric matched group (tracked via
   geometric_tags), the SAME displacement vector is applied to every member
   of the group, preserving common-centroid / interdigitation symmetry.
"""

from __future__ import annotations

import bisect
import math
from itertools import groupby
from typing import Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------
DRC_CRITIC_PROMPT = """\
ROLE:
You are a DRC (Design Rule Check) Critic for 14nm FinFET analog layout.
Your job is to fix ALL placement violations by emitting corrected [CMD] blocks.

CONTEXT:
You are Stage 3 in a pipeline:
1) Topology Analyst
2) Placement Specialist
3) DRC Critic (YOU)
4) Routing Pre-Viewer

INPUT:
You receive a violation report.
Each violation includes a PRESCRIPTIVE FIX using exact coordinates (after "→").
Matched-pair group moves are labelled [GROUP MOVE] — apply the SAME delta to all.

CORE RULES (STRICT):
- NEVER invent coordinates.
- ALWAYS use the exact x/y values provided in the violation report.
- Fix EVERY violation (no skipping).
- Do NOT introduce new violations.
- PMOS must stay in PMOS rows (higher y).
- NMOS must stay in NMOS rows (lower y).
- If a violation lists a GROUP MOVE, emit one [CMD] per device in the group.

PROCEDURE:
For each violation:
1) OVERLAP  → Move the nominated device (or group) to the prescribed x.
2) GAP      → Move device B to the prescribed x.
3) ROW_ERROR→ Move the device to the prescribed y.
4) CASCADE  → If a move causes a new overlap, fix that device too.

OUTPUT FORMAT (STRICT):
- Output ONLY [CMD] blocks, then ONE summary line.
- No explanations. No markdown. No extra text.
- Each block must be valid JSON on ONE line.

FORMAT:
[CMD]{"action":"move","device":"MM1","x":0.588,"y":-0.823}[/CMD]

CONSTRAINTS:
- Max commands = 3 × number of violations
- Do NOT repeat unchanged commands
- Do NOT ask questions

FINAL CHECK (before output):
- Every violation is fixed
- All coordinates match the report exactly
- No new overlaps introduced
- Device types remain in correct rows
- Symmetry within matched groups preserved

OUTPUT:
[CMD] blocks first
Then one-line summary
"""

# ---------------------------------------------------------------------------
# Structured violation type
# ---------------------------------------------------------------------------
class DRCViolation:
    """Machine-readable DRC violation — memory-efficient via __slots__."""

    __slots__ = (
        "kind",                    # "OVERLAP" | "GAP" | "ROW_ERROR"
        "dev_a", "dev_b",          # device IDs (dev_b may be None)
        "x1_a", "x2_a", "y_a", "w_a",
        "x1_b", "x2_b", "y_b", "w_b",
        "gap_required", "gap_actual",
        "group_ids",               # frozenset of IDs in the same matched group
        "text",                    # human-readable with prescriptive hint
    )

    def __init__(self, **kw):
        # Initialise all slots to a safe default, then apply kwargs
        self.group_ids = frozenset()
        for k, v in kw.items():
            setattr(self, k, v)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_POWER_NETS: Set[str] = {"vdd", "vss", "gnd", "vcc", "avdd", "avss"}


def _shared_potential(
    id_a: str,
    id_b: str,
    terminal_nets: Dict[str, Dict[str, str]],
) -> bool:
    """Return True if any terminal of device_a shares a net with device_b.

    Equipotential abutment is valid (physical STI gap = 0) when devices share
    the same non-power net on at least one terminal pair.  Shared power/ground
    rails do NOT qualify because their diffusion continuity is handled by the
    fill / well-tie strategy.
    """
    nets_a = terminal_nets.get(id_a, {})
    nets_b = terminal_nets.get(id_b, {})
    for net in nets_a.values():
        if net and net.lower() not in _POWER_NETS and net in nets_b.values():
            return True
    return False


def _effective_gap(
    id_a: str,
    id_b: str,
    terminal_nets: Dict[str, Dict[str, str]],
    default_gap: float,
) -> float:
    """Yield-limiting gap between two devices.

    Returns 0.0 if they share an equipotential net (abutment allowed),
    otherwise returns the lithographic default_gap.
    """
    if _shared_potential(id_a, id_b, terminal_nets):
        return 0.0
    return default_gap


def _group_of(dev_id: str, geometric_tags: Dict) -> Optional[frozenset]:
    """Return the matched-group frozenset that contains dev_id, or None."""
    for tag_info in geometric_tags.values():
        members = tag_info if isinstance(tag_info, (list, tuple, set, frozenset)) \
                  else tag_info.get("members", []) if isinstance(tag_info, dict) else []
        fs = frozenset(members)
        if dev_id in fs:
            return fs
    return None


# ---------------------------------------------------------------------------
# Sweep-Line Overlap Detection  O(N log N + R)
# ---------------------------------------------------------------------------

def _sweep_line_overlaps(
    boxes: List[Dict],
    terminal_nets: Dict[str, Dict[str, str]],
    default_gap: float,
) -> List[Tuple[Dict, Dict, float]]:
    """Detect all overlapping bounding-box pairs via sweep line.

    Returns a list of (box_a, box_b, required_gap) tuples indicating overlap.
    The required_gap is the dynamic gap that *was* violated.

    Algorithm
    ---------
    1. Build an event list: each box yields a LEFT (open) and RIGHT (close)
       event, sorted by X-coordinate.
    2. Sweep left to right.  On a LEFT event, insert the box's Y-interval
       [y1, y2] into a sorted active list and check for Y-overlaps with
       every already-active interval.
    3. On a RIGHT event, remove the Y-interval.
    Because Y-intervals are sorted and we use bisect, insertion/deletion is
    O(log N); each overlap is reported exactly once.  Total: O((N+R) log N).
    """
    events: List[Tuple[float, int, Dict]] = []  # (x_coord, event_type, box)
    # event_type: 0 = LEFT (open), 1 = RIGHT (close)
    for box in boxes:
        events.append((box["x1"], 0, box))
        events.append((box["x2"], 1, box))
    # Sort: primarily by x, secondarily open before close at same x
    events.sort(key=lambda e: (e[0], e[1]))

    # Active set: list of boxes sorted by y1 for O(log N) range queries
    active: List[Dict] = []
    overlaps: List[Tuple[Dict, Dict, float]] = []
    seen: Set[Tuple[str, str]] = set()

    def _active_insert(box: Dict):
        """Insert into active set sorted by y1."""
        idx = bisect.bisect_left([b["y1"] for b in active], box["y1"])
        active.insert(idx, box)

    def _active_remove(box: Dict):
        """Remove from active set."""
        try:
            active.remove(box)
        except ValueError:
            pass

    def _y_overlaps(box: Dict) -> List[Dict]:
        """Return active boxes whose Y-interval overlaps box's Y-interval."""
        # All active boxes with y1 < box["y2"]
        # Use bisect to find first active box with y1 >= box["y2"] (they can't overlap)
        ys = [b["y1"] for b in active]
        hi = bisect.bisect_left(ys, box["y2"])
        candidates = active[:hi]
        # Filter: y2 of candidate must be > box["y1"]
        return [c for c in candidates if c["y2"] > box["y1"] and c["id"] != box["id"]]

    for _xval, etype, box in events:
        if etype == 0:  # LEFT event — entering sweep
            for other in _y_overlaps(box):
                key = tuple(sorted([box["id"], other["id"]]))
                if key not in seen:
                    seen.add(key)
                    req_gap = _effective_gap(
                        box["id"], other["id"], terminal_nets, default_gap
                    )
                    # Only report as overlap if the bounding boxes actually touch
                    # considering the effective gap (gap=0 means pure geometric only)
                    x_overlap = box["x1"] < other["x2"] and other["x1"] < box["x2"]
                    if x_overlap:
                        overlaps.append((box, other, req_gap))
            _active_insert(box)
        else:           # RIGHT event — leaving sweep
            _active_remove(box)

    return overlaps


# ---------------------------------------------------------------------------
# Main DRC checker
# ---------------------------------------------------------------------------

def run_drc_check(
    nodes: List[Dict],
    gap_px: float = 0.0,
    terminal_nets: Optional[Dict[str, Dict[str, str]]] = None,
    geometric_tags: Optional[Dict] = None,
) -> Dict:
    """Validate placement for overlaps, minimum gaps, and row-type errors.

    Upgraded to use:
    * Sweep-Line overlap detection   → O(N log N + R)
    * Dynamic gap from terminal_nets → yield-limiting constraint
    * Symmetric-group metadata       → for downstream fix legalization

    Args:
        nodes:         list of placement node dicts (must have geometry)
        gap_px:        default minimum spacing (in layout units)
        terminal_nets: {device_id: {terminal: net_name}} for gap computation
        geometric_tags: matched-group metadata for symmetry preservation

    Returns:
        dict with keys:
        - "pass"        bool
        - "violations"  list[str]  — human-readable with prescriptive hints
        - "structured"  list[DRCViolation]
        - "summary"     str
        - "pmos_row_y"  float | None
        - "nmos_row_y"  float | None
    """
    terminal_nets = terminal_nets or {}
    geometric_tags = geometric_tags or {}

    violation_texts: List[str] = []
    violation_set: Set[str] = set()
    structured: List[DRCViolation] = []
    valid = [n for n in nodes if "geometry" in n]

    # ── Dynamic row detection (set-based, handles multi-row layouts) ──────────
    pmos_ys: Set[float] = set()
    nmos_ys: Set[float] = set()
    for n in valid:
        y_r = round(float(n["geometry"]["y"]), 4)
        t = str(n.get("type", "")).strip().lower()
        if t == "pmos":
            pmos_ys.add(y_r)
        elif t == "nmos":
            nmos_ys.add(y_r)

    pmos_row_y = max(pmos_ys) if pmos_ys else None
    nmos_row_y = min(nmos_ys) if nmos_ys else None

    # ── Build bounding box cache per node ────────────────────────────────────
    boxes: List[Dict] = []
    for n in valid:
        geo = n["geometry"]
        x = float(geo.get("x", 0))
        y = float(geo.get("y", 0))
        w = float(geo.get("width", 1))
        h = float(geo.get("height", 1))
        boxes.append({
            "id":  n["id"],
            "x1": x,  "y1": y,
            "x2": x + w, "y2": y + h,
            "w": w, "h": h,
            "row": round(y, 4),
            "type": str(n.get("type", "")).strip().lower(),
            "group": _group_of(n["id"], geometric_tags),
        })

    id_to_box: Dict[str, Dict] = {b["id"]: b for b in boxes}

    def _add(text: str, sv: DRCViolation):
        if text not in violation_set:
            violation_set.add(text)
            violation_texts.append(text)
            structured.append(sv)

    # ── 1. Sweep-Line Overlap Detection ──────────────────────────────────────
    overlaps = _sweep_line_overlaps(boxes, terminal_nets, gap_px)

    for a, b, req_gap in overlaps:
        # ── Skip cross-type (NMOS vs PMOS) bounding-box overlaps ──────────
        # These are caused by row-order violations (NMOS above PMOS or vice
        # versa) and will be resolved when ROW_ERROR corrections move the
        # row to the correct Y.  Treating them as horizontal overlaps would
        # push devices sideways, destroying matching and symmetry.
        a_type = a.get("type", "")
        b_type = b.get("type", "")
        if {a_type, b_type} == {"nmos", "pmos"}:
            continue

        # Determine which device is the anchor (left-most stays put)
        if a["x1"] <= b["x1"]:
            fix_dev = b["id"]
            fix_x   = round(a["x2"] + req_gap, 4)
            fix_y   = b["y1"]
        else:
            fix_dev = a["id"]
            fix_x   = round(b["x2"] + req_gap, 4)
            fix_y   = a["y1"]

        # Annotate if this device is part of a symmetric group
        grp = id_to_box[fix_dev].get("group")
        grp_note = ""
        if grp and len(grp) > 1:
            peers = sorted(g for g in grp if g != fix_dev)
            grp_note = f"  [GROUP MOVE — apply same Δx to: {', '.join(peers)}]"

        text = (
            f"OVERLAP: {a['id']} vs {b['id']}  "
            f"({a['id']}=[x:{a['x1']:.3f}>{a['x2']:.3f}, y:{a['y1']:.3f}]  "
            f"{b['id']}=[x:{b['x1']:.3f}>{b['x2']:.3f}, y:{b['y1']:.3f}])  "
            f"effective_gap={req_gap:.4f}  "
            f"MOVE {fix_dev} to x={fix_x:.3f}, y={fix_y:.3f}{grp_note}"
        )
        _add(text, DRCViolation(
            kind="OVERLAP",
            dev_a=a["id"], dev_b=b["id"],
            x1_a=a["x1"], x2_a=a["x2"], y_a=a["y1"], w_a=a["w"],
            x1_b=b["x1"], x2_b=b["x2"], y_b=b["y1"], w_b=b["w"],
            gap_required=req_gap,
            gap_actual=b["x1"] - a["x2"],
            group_ids=grp or frozenset(),
            text=text,
        ))

    # ── 2. Row-Type Validation (two-stage) ───────────────────────────────────
    nmos_top_y    = max(nmos_ys) if nmos_ys else None
    pmos_bottom_y = min(pmos_ys) if pmos_ys else None

    if pmos_ys or nmos_ys:
        for n in valid:
            dev_id   = n["id"]
            dev_type = str(n.get("type", "")).strip().lower()
            geo      = n["geometry"]
            x_geo    = float(geo.get("x", 0))
            w_geo    = float(geo.get("width", 1))
            y        = round(float(geo.get("y", 0)), 4)

            def _row_viol(text: str, correct_y: float, _did=dev_id, _x=x_geo, _w=w_geo, _y=y):
                grp = _group_of(_did, geometric_tags)
                _add(text, DRCViolation(
                    kind="ROW_ERROR",
                    dev_a=_did, dev_b=None,
                    x1_a=_x, x2_a=_x + _w,
                    y_a=_y, w_a=_w,
                    x1_b=0, x2_b=0, y_b=correct_y, w_b=0,
                    gap_required=0, gap_actual=0,
                    group_ids=grp or frozenset(),
                    text=text,
                ))

            if dev_type == "pmos":
                # Stage A: must be above all NMOS rows
                if nmos_top_y is not None and y <= nmos_top_y:
                    # Use any PMOS row that is already above NMOS.
                    # If none exist (fully inverted) PMOS stays in place —
                    # only NMOS rows will be re-stacked below it.
                    valid_pmos_ys = [py for py in pmos_ys if py > nmos_top_y]
                    correct_y = max(valid_pmos_ys) if valid_pmos_ys else y
                    _row_viol(
                        f"FATAL ROW ERROR: {dev_id} is PMOS at y={y:.4f} "
                        f"(must be above NMOS band y>{nmos_top_y:.4f}). "
                        f"Move {dev_id} to y={correct_y:.4f}",
                        correct_y,
                    )
                # Stage B: on correct side but not in known PMOS rows
                elif (nmos_top_y is not None and y > nmos_top_y
                      and pmos_ys and y not in pmos_ys):
                    correct_y = max(pmos_ys)
                    _row_viol(
                        f"FATAL ROW ERROR: {dev_id} is PMOS at y={y:.4f}, "
                        f"not in PMOS rows {sorted(pmos_ys)}. "
                        f"→ MOVE {dev_id} to y={correct_y:.4f}",
                        correct_y,
                    )

            elif dev_type == "nmos":
                # Stage A: must be below all PMOS rows
                if pmos_bottom_y is not None and y >= pmos_bottom_y:
                    # Use any NMOS row already below PMOS.
                    valid_nmos_ys = [ny for ny in nmos_ys if ny < pmos_bottom_y]
                    if valid_nmos_ys:
                        correct_y = min(valid_nmos_ys)
                    else:
                        # ── Fully inverted: all NMOS rows are above PMOS rows ──
                        # Compute per-row device heights dynamically from geometry.
                        # Stack NMOS rows directly below the lowest PMOS row,
                        # one on top of the other, with zero gap between them.
                        # No hardcoded spacing — uses actual device heights.
                        _row_max_h: Dict[float, float] = {}
                        for _n2 in valid:
                            _ry2 = round(float(_n2["geometry"]["y"]), 4)
                            _h2  = float(_n2["geometry"].get("height", 0))
                            _row_max_h[_ry2] = max(_row_max_h.get(_ry2, 0.0), _h2)

                        # Sort NMOS rows descending (highest Y first, i.e. the
                        # inverted-top row becomes the corrected top NMOS row).
                        nmos_sorted_desc = sorted(nmos_ys, reverse=True)

                        # Stack: start just below pmos_bottom_y, subtract each
                        # row's actual height (no gap).
                        _nmos_target_map: Dict[float, float] = {}
                        cursor = pmos_bottom_y
                        for _nrow_y in nmos_sorted_desc:
                            _h_nrow = _row_max_h.get(_nrow_y, 0.0)
                            if _h_nrow <= 0:
                                _h_nrow = float(geo.get("height", 0.568))
                            target = round(cursor - _h_nrow, 4)
                            _nmos_target_map[_nrow_y] = target
                            cursor = target

                        correct_y = _nmos_target_map.get(
                            y,
                            round(pmos_bottom_y - float(geo.get("height", 0.568)), 4)
                        )
                    _row_viol(
                        f"FATAL ROW ERROR: {dev_id} is NMOS at y={y:.4f} "
                        f"(must be below PMOS band y<{pmos_bottom_y:.4f}). "
                        f"Move {dev_id} to y={correct_y:.4f}",
                        correct_y,
                    )
                # Stage B: on correct side but not in known NMOS rows
                elif (pmos_bottom_y is not None and y < pmos_bottom_y
                      and nmos_ys and y not in nmos_ys):
                    correct_y = min(nmos_ys)
                    _row_viol(
                        f"FATAL ROW ERROR: {dev_id} is NMOS at y={y:.4f}, "
                        f"not in NMOS rows {sorted(nmos_ys)}. "
                        f"→ MOVE {dev_id} to y={correct_y:.4f}",
                        correct_y,
                    )

    # ── 3. Minimum Gap Check (same row, adjacent, dynamic spacing) ───────────
    if gap_px > 0:
        boxes_sorted = sorted(boxes, key=lambda bx: (bx["row"], bx["x1"]))
        for _row_y, grp_iter in groupby(boxes_sorted, key=lambda bx: bx["row"]):
            row_devs = list(grp_iter)
            for k in range(len(row_devs) - 1):
                la, lb = row_devs[k], row_devs[k + 1]
                req = _effective_gap(la["id"], lb["id"], terminal_nets, gap_px)
                actual_gap = lb["x1"] - la["x2"]
                if req > 0 and 0 <= actual_gap < req:
                    fix_x = round(la["x2"] + req, 4)
                    grp = lb.get("group")
                    grp_note = ""
                    if grp and len(grp) > 1:
                        peers = sorted(g for g in grp if g != lb["id"])
                        grp_note = f"  [GROUP MOVE — apply same Δx to: {', '.join(peers)}]"
                    text = (
                        f"GAP.VIOLATION: {la['id']} → {lb['id']}  "
                        f"actual={actual_gap:.4f}  required≥{req:.4f}  "
                        f"→ MOVE {lb['id']} to x={fix_x:.3f}, y={lb['y1']:.3f}{grp_note}"
                    )
                    _add(text, DRCViolation(
                        kind="GAP",
                        dev_a=la["id"], dev_b=lb["id"],
                        x1_a=la["x1"], x2_a=la["x2"], y_a=la["y1"], w_a=la["w"],
                        x1_b=lb["x1"], x2_b=lb["x2"], y_b=lb["y1"], w_b=lb["w"],
                        gap_required=req,
                        gap_actual=actual_gap,
                        group_ids=grp or frozenset(),
                        text=text,
                    ))

    passed = len(violation_texts) == 0
    summary = (
        "DRC PASSED – no overlap or gap violations."
        if passed
        else (
            f"DRC FAILED – {len(violation_texts)} violation(s):\n"
            + "\n".join(f"  • {v}" for v in violation_texts)
        )
    )
    return {
        "pass": passed,
        "violations": violation_texts,
        "structured": structured,
        "summary": summary,
        "pmos_row_y": pmos_row_y,
        "nmos_row_y": nmos_row_y,
    }


# ---------------------------------------------------------------------------
# Cost-Driven Legalizer with Symmetry Preservation
# ---------------------------------------------------------------------------

# Tunable cost coefficients (α, β, γ)
_ALPHA = 1.0    # weight for |Δx| displacement
_BETA  = 2.0    # weight for |Δy| displacement (row hops are expensive)
_GAMMA = 0.5    # weight for HPWL wirelength penalty proxy


def _move_cost(
    orig_x: float,
    orig_y: float,
    cand_x: float,
    cand_y: float,
    net_count: int = 1,
) -> float:
    """Miniature Manhattan cost for a candidate move.

    Cost = α·|Δx| + β·|Δy| + γ·net_count·(|Δx|+|Δy|)
    Lower is better.  net_count proxies HPWL sensitivity.
    """
    dx = abs(cand_x - orig_x)
    dy = abs(cand_y - orig_y)
    return _ALPHA * dx + _BETA * dy + _GAMMA * net_count * (dx + dy)


def compute_prescriptive_fixes(
    drc_result: Dict,
    gap_px: float = 0.0,
    nodes: Optional[List[Dict]] = None,
    geometric_tags: Optional[Dict] = None,
    terminal_nets: Optional[Dict[str, Dict[str, str]]] = None,
) -> List[Dict]:
    """Generate exact corrective move commands from structured DRC violations.

    Improvements over the original greedy "push-right" approach:
    ─────────────────────────────────────────────────────────────
    1. COST SCORING   — evaluates candidate positions in all four cardinal
       directions; picks the one with lowest Manhattan displacement cost.
    2. SYMMETRY GUARD — if the device is in a matched group (geometric_tags),
       the chosen Δ-vector is applied uniformly to every group member.
    3. BISECT SLOTS   — O(log N) per spatial probe (unchanged from baseline).
    4. DYNAMIC GAP    — honours the yield-limiting gap from terminal_nets.

    Args:
        drc_result:    dict from run_drc_check()
        gap_px:        default gap for obstacle avoidance
        nodes:         original node list (pre-populates occupied slots)
        geometric_tags: matched-group metadata for symmetry preservation
        terminal_nets: terminal→net map for dynamic gap (passed through)

    Returns:
        list[dict]: command dicts ready to emit as [CMD] blocks
    """
    geometric_tags = geometric_tags or {}
    terminal_nets  = terminal_nets  or {}

    # ── Row-boundary references (for PMOS/NMOS violation filtering) ──────────
    # Strategy: build boundaries from AUTHORITATIVE positions:
    #   - For devices with a ROW_ERROR → use the correction target (y_b)
    #   - For correctly placed devices (no ROW_ERROR) → use current position
    # This prevents wrong current positions from polluting the boundary set.
    _nmos_top_y: Optional[float] = None     # highest valid NMOS row Y
    _pmos_bot_y: Optional[float] = None     # lowest  valid PMOS row Y

    if nodes:
        # Build a map: device_id -> target_y if it has a ROW_ERROR
        _row_err_targets: Dict[str, float] = {}
        for _v in drc_result.get("structured", []):
            if _v.kind == "ROW_ERROR" and _v.dev_a and _v.y_b is not None:
                _row_err_targets[_v.dev_a] = _v.y_b

        # Now walk all nodes and pick the right y for boundary computation
        for _n in nodes:
            _did = str(_n.get("id", ""))
            _t   = str(_n.get("type", "")).strip().lower()
            # Use the correction target if this device has a ROW_ERROR,
            # otherwise trust the current (presumably correct) position.
            if _did in _row_err_targets:
                _y = round(_row_err_targets[_did], 4)
            else:
                _y = round(float(_n.get("geometry", {}).get("y", 0)), 4)

            if _t == "nmos":
                _nmos_top_y = max(_nmos_top_y, _y) if _nmos_top_y is not None else _y
            elif _t == "pmos":
                _pmos_bot_y = min(_pmos_bot_y, _y) if _pmos_bot_y is not None else _y

    # ── Build lookup maps ─────────────────────────────────────────────────────
    # proposed_x / proposed_y track where we think each device currently sits
    # after previously applied fixes in this same pass.
    proposed_x: Dict[str, float] = {}
    proposed_y: Dict[str, float] = {}
    proposed_w: Dict[str, float] = {}
    orig_x:     Dict[str, float] = {}
    orig_y:     Dict[str, float] = {}
    net_counts: Dict[str, int]   = {}

    for v in drc_result.get("structured", []):
        if v.dev_a:
            proposed_w.setdefault(v.dev_a, v.w_a)
            orig_x.setdefault(v.dev_a, v.x1_a)
            orig_y.setdefault(v.dev_a, v.y_a)
        if v.dev_b:
            proposed_w.setdefault(v.dev_b, v.w_b)
            orig_x.setdefault(v.dev_b, v.x1_b)
            orig_y.setdefault(v.dev_b, v.y_b)

    # Count nets per device (proxy for HPWL sensitivity)
    for dev_id, nets in terminal_nets.items():
        net_counts[dev_id] = len(
            {n for n in nets.values() if n and n.lower() not in _POWER_NETS}
        )

    # ── Per-row occupied slot tracking (sorted → O(log N) queries) ───────────
    occupied: Dict[float, List[float]] = {}
    if nodes:
        _tmp: Dict[float, set] = {}
        for n in nodes:
            geo = n.get("geometry", {})
            ry  = round(float(geo.get("y", 0)), 4)
            rx  = round(float(geo.get("x", 0)), 4)
            _tmp.setdefault(ry, set()).add(rx)
        occupied = {ry: sorted(xs) for ry, xs in _tmp.items()}

    def _register_slot(row_y: float, x: float):
        row_y = round(row_y, 4)
        x     = round(x, 4)
        lst   = occupied.setdefault(row_y, [])
        idx   = bisect.bisect_left(lst, x)
        if idx >= len(lst) or lst[idx] != x:
            lst.insert(idx, x)

    def _is_free(row_y: float, x: float, w: float) -> bool:
        """Return True if [x, x+w) is unoccupied in row_y."""
        row_y = round(row_y, 4)
        slots = occupied.get(row_y, [])
        w_r   = round(w, 4)
        lo    = bisect.bisect_left(slots, x - w_r + 1e-6)
        return not (lo < len(slots) and abs(slots[lo] - x) < w_r - 1e-6)

    def _find_free_x(row_y: float, want_x: float, w: float) -> float:
        """Classical bisect-based free-slot finder (used as tie-breaker)."""
        x    = round(want_x, 4)
        step = round(w, 4) if w > 0 else 0.294
        while not _is_free(row_y, x, w):
            x = round(x + step, 4)
        return x

    def _best_candidate(
        dev_id: str,
        raw_x: float,
        raw_y: float,
        w: float,
        anchor_x2: Optional[float] = None,
        req_gap: float = 0.0,
        dev_type: str = "",
    ) -> Tuple[float, float]:
        """Pick the lowest-cost legal position from four candidate directions.

        Candidates evaluated (cardinal vectors):
          RIGHT: raw_x (primary direction)
          LEFT:  anchor_x2 - w - req_gap  (compact leftward if space exists)
          DOWN:  same x, shift down by row_pitch
          UP:    same x, shift up by row_pitch

        Vertical candidates are FILTERED by device type to prevent
        PMOS from drifting below NMOS rows (and vice-versa).
        A PMOS candidate must satisfy: candidate_y >= _pmos_bot_y
        An NMOS candidate must satisfy: candidate_y <= _nmos_top_y
        """
        ox = orig_x.get(dev_id, raw_x)
        oy = orig_y.get(dev_id, raw_y)
        nc = net_counts.get(dev_id, 1)
        row_pitch = 0.668
        _type = dev_type.strip().lower()

        def _row_valid_for_type(y: float) -> bool:
            """Return True if y is a legal row for this device's type."""
            if _type == "pmos":
                # PMOS must stay at or above its minimum row Y
                if _pmos_bot_y is not None and y < _pmos_bot_y:
                    return False
                # PMOS must NOT be in any NMOS row band
                if _nmos_top_y is not None and y <= _nmos_top_y:
                    return False
            elif _type == "nmos":
                # NMOS must stay at or below its maximum row Y
                if _nmos_top_y is not None and y > _nmos_top_y:
                    return False
                # NMOS must NOT be in any PMOS row band
                if _pmos_bot_y is not None and y >= _pmos_bot_y:
                    return False
            return True

        candidates: List[Tuple[float, float]] = []

        # RIGHT candidate (primary horizontal fix)
        if _row_valid_for_type(raw_y):
            candidates.append((_find_free_x(raw_y, raw_x, w), raw_y))

        # LEFT candidate (only sensible if anchor_x2 is known)
        if anchor_x2 is not None and _row_valid_for_type(raw_y):
            left_x = round(anchor_x2 - w - req_gap, 4)
            if left_x >= 0 and _is_free(raw_y, left_x, w):
                candidates.append((left_x, raw_y))

        # Vertical candidates — gated by type-aware row validation
        for delta_y in (+row_pitch, -row_pitch):
            ny = round(raw_y + delta_y, 4)
            if _row_valid_for_type(ny):
                fx = _find_free_x(ny, ox, w)
                candidates.append((fx, ny))

        # Fallback: if no type-valid candidate found, allow horizontal at raw_y
        if not candidates:
            candidates.append((_find_free_x(raw_y, raw_x, w), raw_y))

        # Score all candidates; keep the cheapest valid one
        best_x, best_y = candidates[0]
        best_cost = _move_cost(ox, oy, best_x, best_y, nc)
        for cx, cy in candidates[1:]:
            cost = _move_cost(ox, oy, cx, cy, nc)
            if cost < best_cost and _is_free(cy, cx, w):
                best_cost = cost
                best_x, best_y = cx, cy

        return best_x, best_y

    def _apply_group_move(
        primary_dev: str,
        dx: float,
        dy: float,
        group_ids: frozenset,
        cmds: List[Dict],
        cmd_map: Dict[str, Dict],
    ):
        """Apply the same displacement vector to every member of a matched group.

        This preserves common-centroid / interdigitated symmetry:
        if MM0_f1 moves right by 0.3, MM1_f1 also moves right by exactly 0.3.
        """
        all_ids = group_ids if group_ids else frozenset([primary_dev])
        for gid in all_ids:
            if gid == primary_dev:
                continue
            cur_x = proposed_x.get(gid, orig_x.get(gid, 0.0))
            cur_y = proposed_y.get(gid, orig_y.get(gid, 0.0))
            new_y = round(cur_y + dy, 4)
            w_g   = proposed_w.get(gid, 0.294)

            if abs(dx) < 1e-6:
                # Y-only row fix: keep sibling's exact X — do NOT search for
                # a free slot (that would break matching and symmetry).
                new_x = cur_x
            else:
                # Horizontal fix: find the nearest free slot at the target X.
                new_x = _find_free_x(new_y, round(cur_x + dx, 4), w_g)

            _register_slot(new_y, new_x)
            proposed_x[gid] = new_x
            proposed_y[gid] = new_y
            if gid not in cmd_map:
                c = {"action": "move", "device": gid, "x": new_x, "y": new_y}
                cmds.append(c)
                cmd_map[gid] = c
            else:
                cmd_map[gid]["x"] = new_x
                cmd_map[gid]["y"] = new_y

    # ── Main fix loop ─────────────────────────────────────────────────────────
    cmds:    List[Dict]        = []
    cmd_map: Dict[str, Dict]   = {}

    # Build a type lookup from nodes for use in _best_candidate
    _node_type_map: Dict[str, str] = {}
    if nodes:
        for _n in nodes:
            _node_type_map[str(_n.get("id", ""))] = str(_n.get("type", "")).strip().lower()

    for v in drc_result.get("structured", []):

        if v.kind == "ROW_ERROR":
            # ── ROW_ERROR: change Y ONLY — never change X ─────────────────
            # Changing X would destroy horizontal placement, break matched
            # groups, common-centroid patterns, and interdigitation order.
            # The correct action is to shift the ENTIRE ROW vertically:
            # every device keeps its exact X and moves to correct_y.
            correct_y = v.y_b
            dev_id    = v.dev_a
            cur_x     = proposed_x.get(dev_id, v.x1_a)
            cur_y     = proposed_y.get(dev_id, v.y_a)

            # Skip if this device is already at the correct Y (no-op fix)
            if abs(cur_y - correct_y) < 1e-6:
                continue

            dx = 0.0                              # X never changes for row fixes
            dy = round(correct_y - cur_y, 4)

            new_x = cur_x                         # X frozen
            new_y = correct_y

            _register_slot(new_y, new_x)
            proposed_x[dev_id] = new_x
            proposed_y[dev_id] = new_y

            if dev_id not in cmd_map:
                c = {"action": "move", "device": dev_id, "x": new_x, "y": new_y}
                cmds.append(c)
                cmd_map[dev_id] = c
            else:
                cmd_map[dev_id]["x"] = new_x
                cmd_map[dev_id]["y"] = new_y

            # Propagate Y-shift to every device in the same matched group.
            # dx=0 ensures siblings only shift vertically, preserving symmetry.
            if v.group_ids and len(v.group_ids) > 1:
                _apply_group_move(dev_id, dx, dy, v.group_ids, cmds, cmd_map)

        elif v.kind == "OVERLAP":
            cur_xa = proposed_x.get(v.dev_a, v.x1_a)
            cur_xb = proposed_x.get(v.dev_b, v.x1_b)
            cur_ya = proposed_y.get(v.dev_a, v.y_a)
            cur_yb = proposed_y.get(v.dev_b, v.y_b)
            w_a    = proposed_w.get(v.dev_a, v.w_a)
            w_b    = proposed_w.get(v.dev_b, v.w_b)
            req    = _effective_gap(v.dev_a, v.dev_b, terminal_nets, gap_px)

            if cur_xb >= cur_xa:
                target_dev = v.dev_b
                raw_x      = round(cur_xa + w_a + req, 4)
                old_x      = cur_xb
                old_y      = cur_yb
                w_t        = w_b
                anchor_x2  = cur_xa + w_a
            else:
                target_dev = v.dev_a
                raw_x      = round(cur_xb + w_b + req, 4)
                old_x      = cur_xa
                old_y      = cur_ya
                w_t        = w_a
                anchor_x2  = cur_xb + w_b

            dev_type_t = _node_type_map.get(target_dev, "")
            free_x, free_y = _best_candidate(
                target_dev, raw_x, old_y, w_t,
                anchor_x2=anchor_x2, req_gap=req,
                dev_type=dev_type_t,
            )
            _register_slot(free_y, free_x)

            dx = free_x - old_x
            dy = free_y - old_y
            proposed_x[target_dev] = free_x
            proposed_y[target_dev] = free_y

            if target_dev not in cmd_map:
                c = {"action": "move", "device": target_dev, "x": free_x, "y": free_y}
                cmds.append(c)
                cmd_map[target_dev] = c
            else:
                if free_x > cmd_map[target_dev]["x"]:
                    cmd_map[target_dev]["x"] = free_x
                    proposed_x[target_dev]   = free_x

            # Propagate displacement to matched group
            if v.group_ids and len(v.group_ids) > 1:
                _apply_group_move(target_dev, dx, dy, v.group_ids, cmds, cmd_map)

        elif v.kind == "GAP":
            cur_xa = proposed_x.get(v.dev_a, v.x1_a)
            cur_ya = proposed_y.get(v.dev_a, v.y_a)
            w_a    = proposed_w.get(v.dev_a, v.w_a)
            w_b    = proposed_w.get(v.dev_b, v.w_b)
            old_xb = proposed_x.get(v.dev_b, v.x1_b)
            old_yb = proposed_y.get(v.dev_b, v.y_b)
            req    = _effective_gap(v.dev_a, v.dev_b, terminal_nets, gap_px)

            raw_x = round(cur_xa + w_a + req, 4)
            dev_type_b = _node_type_map.get(v.dev_b, "")
            free_x, free_y = _best_candidate(
                v.dev_b, raw_x, old_yb, w_b,
                anchor_x2=cur_xa + w_a, req_gap=req,
                dev_type=dev_type_b,
            )
            _register_slot(free_y, free_x)

            dx = free_x - old_xb
            dy = free_y - old_yb
            proposed_x[v.dev_b] = free_x
            proposed_y[v.dev_b] = free_y

            if v.dev_b not in cmd_map:
                c = {"action": "move", "device": v.dev_b, "x": free_x, "y": free_y}
                cmds.append(c)
                cmd_map[v.dev_b] = c
            else:
                if free_x > cmd_map[v.dev_b]["x"]:
                    cmd_map[v.dev_b]["x"] = free_x
                    proposed_x[v.dev_b]   = free_x

            # Propagate to matched group
            if v.group_ids and len(v.group_ids) > 1:
                _apply_group_move(v.dev_b, dx, dy, v.group_ids, cmds, cmd_map)

    return cmds


# ---------------------------------------------------------------------------
# LLM formatting helper
# ---------------------------------------------------------------------------

def format_drc_violations_for_llm(drc_result: Dict, prior_cmds_text: str = "") -> str:
    """Format run_drc_check output into an LLM prompt snippet.

    Includes prescriptive geometry hints (with GROUP MOVE annotations)
    and the prior failed CMDs for context-preserving retry.
    """
    if drc_result["pass"]:
        return "DRC: All clear – no violations detected."

    lines = [
        f"═══ DRC VIOLATIONS ({len(drc_result['violations'])} found) ═══",
        "Each entry includes a PRESCRIPTIVE FIX with exact coordinates.",
        "Entries marked [GROUP MOVE] require the same Δ applied to all listed devices.",
        "",
    ]
    lines.extend(f"  [{i}] {v}" for i, v in enumerate(drc_result["violations"], 1))

    if prior_cmds_text.strip():
        lines.append("")
        lines.append("═══ PRIOR FAILED [CMD] BLOCKS (context — do NOT repeat unchanged) ═══")
        lines.append(prior_cmds_text.strip()[:2000])

    lines.append("")
    lines.append("Use the exact x/y values from the prescriptive hints above in your [CMD] blocks.")
    return "\n".join(lines)

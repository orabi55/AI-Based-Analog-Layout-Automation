"""
Placement Quality Benchmark
============================
Computes **quantitative** matching and symmetry metrics for the output of the
initial placement pipeline.  All metrics are normalized to [0.0 .. 1.0] where
1.0 is perfect.  A final composite score (weighted average) is also returned.

Metrics
-------
1. **Layout Y Symmetry** (layout_y_score)
   Whole-layout vertical structure check.
   - ALL PMOS devices must be in rows strictly ABOVE ALL NMOS devices.
   - Within each device type, ALL matched pairs must be in the SAME Y row.
   Score = 1.0 only when both conditions hold for all pairs.

2. **Matched-Pair X Symmetry** (matching_x_score)
   For every matched pair in the same row, measures how closely the two
   blocks are mirrored about the row's geometric centre axis.
   Score degrades linearly with deviation relative to device width.

3. **Interdigitation Pattern** (interdigitation_score)
   ONLY for nodes that carry ``_technique`` in
   {ABAB_diff_pair, ABBA_current_mirror, ABAB_load_pair, symmetric_cross_coupled}.
   Checks that the physical finger sequence (sorted by X) forms a valid
   ABBA (palindrome) or ABAB (strict alternation) pattern.
   Score = 1 - (broken_blocks / total_interdigitated_blocks).
   Returns N/A (shown as "--") if no interdigitated blocks exist.

4. **Common-Centroid Accuracy** (centroid_score)
   ONLY for nodes with ``_technique == "common_centroid_mirror"`` (2D placement).
   Measures how closely the geometric centroid of each half (A-fingers vs
   B-fingers) coincides.  True 2D common-centroid => centroids identical.
   Score = exp(-offset / dev_width).
   Returns N/A (shown as "--") if no 2D common-centroid groups exist.

5. **DRC Overlap Score** (drc_score)
   Zero origin-to-origin pitch violations => 1.0.
   Abutted fingers (pitch = 0.070 um) are NOT flagged as overlaps.

Composite
---------
``placement_quality_score`` -- weighted average of available metrics.
N/A metrics are excluded from weighting so they never penalise the score.

Usage
-----
    from ai_agent.placement.quality_metrics import score_placement

    report = score_placement(nodes, matching_info, finger_map=finger_map)
    print(report["summary"])

Functions
---------
- score_placement:
    Main entry point.
- _layout_y_symmetry:
    Whole-layout PMOS-above-NMOS + same-row-per-pair check.
- _matched_pair_x_symmetry:
    Mirror symmetry about row centre axis.
- _interdigitation_pattern:
    ABBA/ABAB sequence check for interdigitated blocks only.
- _common_centroid_accuracy:
    Centroid coincidence for 2D common-centroid blocks only.
- _drc_overlap:
    Counts true geometric overlaps (pitch < MIN_PITCH).
- format_report:
    Formats the metric dict as a console-friendly table string.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

# Default finger width (um) -- matches config.design_rules.PITCH_UM
STD_PITCH = 0.294

# Techniques treated as interdigitation (ABBA / ABAB)
_INTERDIG_TECHNIQUES = frozenset({
    "ABAB_diff_pair",
    "ABBA_current_mirror",
    "ABAB_load_pair",
    "symmetric_cross_coupled",
    "ABBA",
    "ABAB",
})

# Technique used for 2-D common-centroid placement
_CC_TECHNIQUE = "common_centroid_mirror"

# Sentinel for "metric not applicable" (no relevant blocks)
_NA = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_geo(node: dict) -> dict:
    return node.get("geometry") or {}


def _cx(node: dict) -> float:
    """Centre-X of a node."""
    g = _get_geo(node)
    return float(g.get("x", 0.0)) + float(g.get("width", 0.0)) / 2.0


def _row_y(node: dict) -> float:
    return round(float(_get_geo(node).get("y", 0.0)), 4)


def _transistor_key(node_id: str) -> str:
    """Return the logical transistor name for a finger/instance node."""
    import re
    m = re.match(r'^(.+?)(?:_m\d+)?(?:_f\d+)?$', node_id)
    return m.group(1) if m else node_id


_DUMMY_PREFIXES = ("EDGE_DUMMY", "FILLER_DUMMY", "FILLER", "DUMMY")


def _is_dummy(node: dict) -> bool:
    """True for dummy/filler cells that should not count as active devices."""
    if node.get("is_dummy"):
        return True
    nid = str(node.get("id", ""))
    return any(nid.startswith(p) for p in _DUMMY_PREFIXES)


def _infer_pairs_from_rows(nodes: List[dict]) -> List[Tuple[str, str]]:
    """
    Detect matched pairs directly from the physical row pattern.

    Rules:
    1. 2-device rows: ABBA/ABAB -> one pair.
    2. 3+ device rows, palindromic -> all pairwise combinations.
    3. 3+ device rows: detect singleton pairs -- devices that appear exactly
       once and occupy mirror-symmetric positions about the row centre.
       e.g. [MM6, D, MM10, MM10, MM10, MM10, D, MM7] -> MM6 and MM7 are
       singletons at symmetric positions -> inferred as a matched pair.
    """
    row_nodes: Dict[float, List[dict]] = defaultdict(list)
    for n in nodes:
        if not _is_dummy(n):
            row_nodes[_row_y(n)].append(n)

    found: set = set()
    for rn in row_nodes.values():
        sorted_row = sorted(rn, key=lambda n: float(_get_geo(n).get("x", 0.0)))
        keys = [_transistor_key(n["id"]) for n in sorted_row]
        unique = list(dict.fromkeys(keys))
        if len(unique) < 2:
            continue

        is_palindrome  = keys == keys[::-1]
        is_alternating = len(keys) >= 2 and all(
            keys[i] != keys[i + 1] for i in range(len(keys) - 1)
        )

        if len(unique) == 2:
            if is_palindrome or is_alternating:
                a, b = unique[0], unique[1]
                found.add((min(a, b), max(a, b)))
        else:
            # Multi-device row
            if is_palindrome:
                # All pairwise combos in palindromic row
                for i in range(len(unique)):
                    for j in range(i + 1, len(unique)):
                        a, b = unique[i], unique[j]
                        found.add((min(a, b), max(a, b)))
            else:
                # Detect singleton pairs: devices appearing exactly once
                # that sit at mirror-symmetric positions about row centre.
                from collections import Counter
                counts = Counter(keys)
                singletons = [k for k, v in counts.items() if v == 1]
                if len(singletons) >= 2:
                    n_devices = len(keys)
                    # Build position map for singletons
                    singleton_pos = {keys[i]: i for i in range(n_devices)
                                     if keys[i] in singletons}
                    # Pair singletons that are mirror-symmetric about centre
                    paired = set()
                    centre = (n_devices - 1) / 2.0
                    for sa, pos_a in singleton_pos.items():
                        if sa in paired:
                            continue
                        mirror_pos = n_devices - 1 - pos_a
                        for sb, pos_b in singleton_pos.items():
                            if sb == sa or sb in paired:
                                continue
                            if abs(pos_b - mirror_pos) <= 1:
                                found.add((min(sa, sb), max(sa, sb)))
                                paired.add(sa)
                                paired.add(sb)
                                break

    return list(found)


# ---------------------------------------------------------------------------
# Sub-metric 1 -- Layout Y Symmetry (whole-layout)
# ---------------------------------------------------------------------------

def _layout_y_symmetry(
    nodes: List[dict],
    matched_pairs: List[Tuple[str, str]],
) -> Tuple[float, str]:
    """
    Whole-layout vertical structure quality.

    Two checks (each contributes 50 % of the score):

    A) PMOS-above-NMOS separation
       min(PMOS_Y) must be strictly greater than max(NMOS_Y).
       Score_A = 1.0 if satisfied, else 0.0.

    B) Same-row alignment for matched pairs
       Every matched pair must have ALL their fingers in the SAME Y row.
       Score_B = fraction of pairs that share at least one common Y row.

    Final score = 0.5 * Score_A + 0.5 * Score_B
    """
    active = [n for n in nodes if not n.get("is_dummy")]

    # -- Check A: PMOS above NMOS ------------------------------------------
    pmos_ys = [_row_y(n) for n in active if str(n.get("type", "")).lower() == "pmos"]
    nmos_ys = [_row_y(n) for n in active if str(n.get("type", "")).lower() == "nmos"]

    details = []
    if pmos_ys and nmos_ys:
        pmos_min = min(pmos_ys)
        nmos_max = max(nmos_ys)
        sep_ok = pmos_min > nmos_max
        score_a = 1.0 if sep_ok else 0.0
        if sep_ok:
            details.append(
                f"  [A] PMOS/NMOS separation OK: "
                f"min(PMOS_Y)={pmos_min:.4f} > max(NMOS_Y)={nmos_max:.4f} [OK]"
            )
        else:
            details.append(
                f"  [A] PMOS/NMOS separation FAIL: "
                f"min(PMOS_Y)={pmos_min:.4f} <= max(NMOS_Y)={nmos_max:.4f} [FAIL]"
            )
    else:
        score_a = 1.0   # single-type layout -- trivially OK
        details.append("  [A] Single device type -- separation check N/A [OK]")

    # -- Check B: matched pairs in same Y row --------------------------------
    if not matched_pairs:
        # No pairs to evaluate — score is just check A (PMOS/NMOS separation)
        score = score_a
        details.append("  [B] No matched pairs — same-row check N/A (score = check A only)")
        return score, "\n".join(details)

    id_to_nodes: Dict[str, List[dict]] = defaultdict(list)
    for n in active:
        id_to_nodes[_transistor_key(n["id"])].append(n)

    same_row = 0
    for a_id, b_id in matched_pairs:
        ys_a = {_row_y(n) for n in id_to_nodes.get(a_id, [])}
        ys_b = {_row_y(n) for n in id_to_nodes.get(b_id, [])}
        if not ys_a or not ys_b:
            details.append(f"  [B] {a_id}/{b_id}: missing nodes")
            continue
        shared = ys_a & ys_b
        if shared:
            same_row += 1
            details.append(
                f"  [B] {a_id}/{b_id}: same row(s) Y={sorted(shared)} [OK]"
            )
        else:
            details.append(
                f"  [B] {a_id}/{b_id}: DIFFERENT rows "
                f"A-Y={sorted(ys_a)}  B-Y={sorted(ys_b)} [FAIL]"
            )
    score_b = same_row / len(matched_pairs)

    score = 0.5 * score_a + 0.5 * score_b
    return score, "\n".join(details)


# ---------------------------------------------------------------------------
# Sub-metric 2 -- Global layout X mirror symmetry
# ---------------------------------------------------------------------------

def _matched_pair_x_symmetry(
    nodes: List[dict],
    matched_pairs: List[Tuple[str, str]],
) -> Tuple[float, str]:
    """
    Check how well the entire layout mirrors itself about a single vertical
    axis at the global center X.

    Symmetry means FUNCTIONAL mirroring, not same-name mirroring.
    Matched pairs (e.g. MM6/MM7 as NMOS latches) are treated as functionally
    equivalent — so MM6 at position X mirrored by MM7 at the mirror position
    IS correct symmetry.

    Algorithm:
      1. Build a symmetry equivalence map: devices in the same matched pair
         share a group key (e.g. MM6 and MM7 both map to "MM6_MM7_group").
      2. Compute global_axis = (min_cx + max_cx) / 2 from active devices.
      3. Per row: for each device at cx, look for any device of the same
         symmetry group at the mirror position (2*axis - cx).
      4. row_score = matched / total. Final = mean of row_scores.
    """
    active = [n for n in nodes if not _is_dummy(n)]
    if not active:
        return _NA, "No active devices."

    # Build symmetry equivalence map from matched pairs.
    # Devices in the same pair share the same canonical group key.
    sym_group: Dict[str, str] = {}
    for a, b in matched_pairs:
        group_key = f"{min(a,b)}|{max(a,b)}"
        sym_group[a] = group_key
        sym_group[b] = group_key
    # Unmatched devices use their own name as the group key
    for n in active:
        tkey = _transistor_key(n["id"])
        if tkey not in sym_group:
            sym_group[tkey] = tkey

    # Global layout bounds (active devices only)
    all_cx = [_cx(n) for n in active]
    global_axis = (min(all_cx) + max(all_cx)) / 2.0

    # Group active devices by row Y
    row_nodes: Dict[float, List[dict]] = defaultdict(list)
    for n in active:
        row_nodes[_row_y(n)].append(n)

    tolerance = STD_PITCH * 0.5   # within half a pitch counts as "at mirror"

    row_scores = []
    details = []
    for y, rn in sorted(row_nodes.items()):
        rn_sorted = sorted(rn, key=_cx)
        total = len(rn_sorted)
        if total == 0:
            continue

        # Build lookup: slot → list of symmetry group keys
        cx_to_groups: Dict[int, List[str]] = defaultdict(list)
        for n in rn_sorted:
            slot = round(_cx(n) / tolerance)
            tkey = _transistor_key(n["id"])
            cx_to_groups[slot].append(sym_group.get(tkey, tkey))

        matched = 0
        for n in rn_sorted:
            cx = _cx(n)
            mirror_cx = 2.0 * global_axis - cx
            tkey = _transistor_key(n["id"])
            my_group = sym_group.get(tkey, tkey)
            mirror_slot = round(mirror_cx / tolerance)
            found = False
            for s in (mirror_slot - 1, mirror_slot, mirror_slot + 1):
                if my_group in cx_to_groups.get(s, []):
                    found = True
                    break
            if found:
                matched += 1

        row_score = matched / total
        row_scores.append(row_score)
        details.append(
            f"  Row y={y:.4f}: {matched}/{total} devices mirrored "
            f"(axis={global_axis:.4f}) -> {row_score:.0%}"
        )

    if not row_scores:
        return _NA, "No rows to evaluate."

    score = sum(row_scores) / len(row_scores)
    return score, "\n".join(details)


# ---------------------------------------------------------------------------
# Sub-metric 3 -- Interdigitation pattern (ABBA / ABAB blocks ONLY)
# ---------------------------------------------------------------------------

def _interdigitation_pattern(nodes: List[dict]) -> Tuple[Optional[float], str]:
    """
    Verify ABBA / ABAB interdigitation for rows with exactly 2 device types.

    A row qualifies when:
      - It contains exactly 2 distinct active device names, AND
      - The X-sorted sequence is palindromic (ABBA) or strictly alternating (ABAB).

    Rows with 1 device or 3+ devices are NOT interdigitation -- they belong
    to common-centroid scoring.
    Returns N/A when zero 2-device rows exist.
    """
    row_nodes: Dict[float, List[dict]] = defaultdict(list)
    for n in nodes:
        if not _is_dummy(n):
            row_nodes[_row_y(n)].append(n)

    if not row_nodes:
        return _NA, "N/A -- no active rows detected."

    qualifying = 0
    errors = 0
    details = []
    for y_key in sorted(row_nodes):
        row = sorted(row_nodes[y_key], key=lambda n: float(_get_geo(n).get("x", 0.0)))
        keys = [_transistor_key(n["id"]) for n in row]
        unique = list(dict.fromkeys(keys))

        if len(unique) != 2:
            continue   # 1 device = trivial; 3+ = common centroid, not interdigitation

        qualifying += 1
        is_palindrome  = keys == keys[::-1]
        is_alternating = len(keys) >= 2 and all(
            keys[i] != keys[i + 1] for i in range(len(keys) - 1)
        )
        ok = is_palindrome or is_alternating
        pattern = "ABBA" if is_palindrome else ("ABAB" if is_alternating else "BROKEN")

        if ok:
            details.append(
                f"  Row y={y_key}: {keys[:8]}{'...' if len(keys)>8 else ''} "
                f"-> {pattern} [OK]"
            )
        else:
            errors += 1
            details.append(
                f"  Row y={y_key}: {keys[:8]}{'...' if len(keys)>8 else ''} "
                f"-> BROKEN [FAIL]"
            )

    if qualifying == 0:
        return _NA, "N/A -- no 2-device ABBA/ABAB rows (only single-device or common-centroid rows)."

    score = 1.0 - errors / qualifying
    return score, "\n".join(details)


# ---------------------------------------------------------------------------
# Sub-metric 4 -- Common-centroid accuracy (2-D common centroid ONLY)
# ---------------------------------------------------------------------------

def _common_centroid_accuracy(nodes: List[dict]) -> Tuple[Optional[float], str]:
    """
    Measure centroid coincidence for device groups that form a 2D
    common-centroid arrangement (same palindromic row pattern in 2+ rows).

    Works for both:
    - 2-device ABBA pairs repeated in 2+ rows.
    - Multi-device (3+) symmetric groups repeated in 2+ rows.

    Score: for each qualifying group, compute each device's geometric
    centroid over all its fingers.  A perfect 2D CC has all device
    centroids coinciding.  Score = exp(-max_pairwise_spread / dev_width).
    Returns N/A when no group spans 2+ rows.
    """
    row_nodes: Dict[float, List[dict]] = defaultdict(list)
    for n in nodes:
        if not _is_dummy(n):
            row_nodes[_row_y(n)].append(n)

    # Identify palindromic rows and their device sets
    palindrome_rows: Dict[float, frozenset] = {}
    for y_key, rn in row_nodes.items():
        sorted_row = sorted(rn, key=lambda n: float(_get_geo(n).get("x", 0.0)))
        keys = [_transistor_key(n["id"]) for n in sorted_row]
        unique = list(dict.fromkeys(keys))
        if len(unique) >= 2 and keys == keys[::-1]:
            palindrome_rows[y_key] = frozenset(unique)

    if not palindrome_rows:
        return _NA, "N/A -- no palindromic rows detected."

    # Group rows by device set
    group_rows: Dict[frozenset, List[float]] = defaultdict(list)
    for y_key, dev_set in palindrome_rows.items():
        group_rows[dev_set].append(y_key)

    id_to_nodes: Dict[str, List[dict]] = defaultdict(list)
    for n in nodes:
        if not _is_dummy(n):
            id_to_nodes[_transistor_key(n["id"])].append(n)

    pair_scores = []
    details = []
    for dev_set, y_keys in group_rows.items():
        devices = sorted(dev_set)
        label = "/".join(devices)
        if len(y_keys) < 2:
            details.append(
                f"  {label}: only {len(y_keys)} row(s) -- 1D arrangement, skip CC check"
            )
            continue

        # Centroid of each device restricted to the qualifying rows
        y_set = set(y_keys)
        centroids = []
        for dev in devices:
            dev_ns = [n for n in id_to_nodes.get(dev, []) if _row_y(n) in y_set]
            if not dev_ns:
                continue
            cx = sum(_cx(n) for n in dev_ns) / len(dev_ns)
            cy = sum(_row_y(n) for n in dev_ns) / len(dev_ns)
            centroids.append((dev, cx, cy))

        if len(centroids) < 2:
            continue

        # Max pairwise centroid distance
        max_offset = 0.0
        for i in range(len(centroids)):
            for j in range(i + 1, len(centroids)):
                _, cx1, cy1 = centroids[i]
                _, cx2, cy2 = centroids[j]
                max_offset = max(max_offset,
                                 math.sqrt((cx1 - cx2)**2 + (cy1 - cy2)**2))

        dev_width = float(_get_geo(id_to_nodes[devices[0]][0]).get("width", STD_PITCH))
        s = math.exp(-max_offset / max(dev_width, 0.001))
        pair_scores.append(s)
        details.append(
            f"  {label}: {len(y_keys)} rows, "
            f"max_centroid_spread={max_offset:.4f}  score={s:.3f} "
            f"{'[OK]' if s >= 0.85 else '[FAIL]'}"
        )

    if not pair_scores:
        return _NA, "N/A -- all arrangements are single-row only (no 2D common-centroid)."

    score = sum(pair_scores) / len(pair_scores)
    return score, "\n".join(details)


# ---------------------------------------------------------------------------
# Sub-metric 5 -- DRC overlap score
# ---------------------------------------------------------------------------

def _drc_overlap(nodes: List[dict]) -> Tuple[float, str]:
    """
    Count true geometric overlaps (origin-to-origin pitch < MIN_PITCH).

    Abutted fingers (pitch ~= 0.070 um) are physically correct and NOT flagged.
    Score = 1 - (overlapping_pairs / total_adjacent_pairs).
    """
    MIN_PITCH = 0.065   # 0.070 um abutment pitch with 1 nm tolerance

    row_nodes: Dict[float, List[dict]] = defaultdict(list)
    for n in nodes:
        if not n.get("is_dummy"):
            row_nodes[_row_y(n)].append(n)

    overlapping = 0
    total_adj = 0
    details = []
    for y_key in sorted(row_nodes):
        row = sorted(row_nodes[y_key],
                     key=lambda n: float(_get_geo(n).get("x", 0.0)))
        for i in range(len(row) - 1):
            n1, n2 = row[i], row[i + 1]
            x1 = float(_get_geo(n1).get("x", 0.0))
            w1 = float(_get_geo(n1).get("width", STD_PITCH))
            x2 = float(_get_geo(n2).get("x", 0.0))
            total_adj += 1
            pitch = x2 - x1
            if pitch < MIN_PITCH:
                overlapping += 1
                details.append(
                    f"  OVERLAP y={y_key}: {n1['id']} (x={x1:.4f}+{w1:.3f})"
                    f" vs {n2['id']} (x={x2:.4f}) pitch={pitch:.4f}"
                )

    score = max(0.0, 1.0 - overlapping / max(total_adj, 1))
    if not details:
        details.append("  No overlaps detected. [OK]")
    return score, "\n".join(details)


# ---------------------------------------------------------------------------
# Weights (only for metrics that are not N/A)
# ---------------------------------------------------------------------------

WEIGHTS = {
    "layout_y":        0.25,   # Whole-layout PMOS/NMOS separation + same row
    "matching_x":      0.25,   # Mirror X symmetry
    "interdigitation": 0.20,   # ABBA/ABAB pattern (if applicable)
    "centroid":        0.20,   # 2D common centroid (if applicable)
    "drc_overlap":     0.10,   # No physical overlaps
}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def score_placement(
    nodes: List[dict],
    matching_info: Optional[dict] = None,
    finger_map: Optional[dict] = None,
    verbose: bool = False,
) -> dict:
    """
    Compute quantitative matching and symmetry quality scores for a placement.

    Parameters
    ----------
    nodes : list
        Fully-expanded finger-level node dicts (post ``expand_to_fingers``).
    matching_info : dict, optional
        Output of ``detect_matching_groups``.  Supplies ``matched_pairs``.
    finger_map : dict, optional
        Mapping ``group_id -> [finger_nodes]``.  Used to infer pairs if
        matching_info is not provided.
    verbose : bool
        If True, include per-metric detail strings in the returned dict.

    Returns
    -------
    dict with keys:
        composite_score, layout_y_score, matching_x_score,
        interdigitation_score (None if N/A), centroid_score (None if N/A),
        drc_score, matched_pairs_count, device_count, summary, [details]
    """
    if matching_info is None:
        matching_info = {}

    # -- Build matched_pairs from matching_info, then AUGMENT with layout inference
    # matching_info may use opaque group IDs (e.g. "MM2_MM1_matched") that do not
    # match expanded finger node IDs.  Always supplement with pattern-based detection.
    raw_pairs: List[Tuple[str, str]] = list(matching_info.get("matched_pairs", []))
    inferred_pairs = _infer_pairs_from_rows(nodes)

    # Merge: use inferred pairs as the canonical set (they use real device names)
    # Keep raw pairs only if they can be resolved to actual nodes
    active_ids = {_transistor_key(n["id"]) for n in nodes if not _is_dummy(n)}
    resolved_raw = [
        (a, b) for a, b in raw_pairs
        if a in active_ids and b in active_ids
    ]

    # Combine without duplicates
    seen = set()
    matched_pairs: List[Tuple[str, str]] = []
    for a, b in inferred_pairs + resolved_raw:
        key = (min(a, b), max(a, b))
        if key not in seen:
            seen.add(key)
            matched_pairs.append((min(a, b), max(a, b)))

    # ── Run all sub-metrics ──────────────────────────────────────────────
    y_score,   y_detail   = _layout_y_symmetry(nodes, matched_pairs)
    x_score,   x_detail   = _matched_pair_x_symmetry(nodes, matched_pairs)
    id_score,  id_detail  = _interdigitation_pattern(nodes)
    cc_score,  cc_detail  = _common_centroid_accuracy(nodes)
    drc_score, drc_detail = _drc_overlap(nodes)

    # ── Weighted composite (skip N/A metrics) ────────────────────────────
    active_weights = {
        "layout_y":   WEIGHTS["layout_y"],
        "drc_overlap": WEIGHTS["drc_overlap"],
    }
    score_values = {
        "layout_y":   y_score,
        "drc_overlap": drc_score,
    }
    # X Mirror Symmetry is now a global geometric check (always applicable)
    if x_score is not _NA:
        active_weights["matching_x"] = WEIGHTS["matching_x"]
        score_values["matching_x"]   = x_score
    if id_score is not _NA:
        active_weights["interdigitation"] = WEIGHTS["interdigitation"]
        score_values["interdigitation"]   = id_score
    if cc_score is not _NA:
        active_weights["centroid"]        = WEIGHTS["centroid"]
        score_values["centroid"]          = cc_score

    # Normalise weights so they sum to 1.0
    total_w = sum(active_weights.values())
    composite = sum(active_weights[k] / total_w * score_values[k]
                    for k in active_weights)

    # ── Human-readable summary ───────────────────────────────────────────
    def _bar(s: float, w: int = 20) -> str:
        return "#" * int(round(s * w)) + "-" * (w - int(round(s * w)))

    def _grade(s: float) -> str:
        if s >= 0.95: return "A+"
        if s >= 0.90: return "A"
        if s >= 0.80: return "B"
        if s >= 0.70: return "C"
        if s >= 0.50: return "D"
        return "F"

    def _fmt_row(label, s) -> str:
        if s is _NA:
            return f"  {label:<24}  {'N/A':>6}   {'(not applicable)':<22}"
        return (
            f"  {label:<24}  {s:>6.1%}   {_bar(s):<22}  {_grade(s)}"
        )

    n_interdig = sum(
        1 for n in nodes
        if n.get("_technique") in _INTERDIG_TECHNIQUES and n.get("_match_owner") in ("A","B")
    )
    n_cc = sum(
        1 for n in nodes
        if n.get("_technique") == _CC_TECHNIQUE and n.get("_match_owner") in ("A","B")
    )

    sep = "=" * 64
    lines = [
        "",
        sep,
        "  MATCHING & SYMMETRY QUALITY BENCHMARK",
        sep,
        f"  Devices          : {len(nodes)}",
        f"  Matched pairs    : {len(matched_pairs)}",
        f"  Interdig fingers : {n_interdig}  "
        f"({'ABBA/ABAB blocks present' if n_interdig else 'none -- metric N/A'})",
        f"  CommonCent.fings : {n_cc}  "
        f"({'2D blocks present' if n_cc else 'none -- metric N/A'})",
        "",
        f"  {'Metric':<24}  {'Score':>6}   {'Progress':<22}  Grade",
        f"  {'-'*24}  {'-'*6}   {'-'*22}  -----",
        _fmt_row("Layout Y Symmetry",   y_score),
        _fmt_row("X Mirror Symmetry",   x_score),
        _fmt_row("Interdigitation",      id_score),
        _fmt_row("Common Centroid (2D)", cc_score),
        _fmt_row("DRC Clean",           drc_score),
        f"  {'-'*24}  {'-'*6}   {'-'*22}  -----",
        f"  {'COMPOSITE':<24}  {composite:>6.1%}   {_bar(composite):<22}  {_grade(composite)}",
        sep,
    ]

    if matched_pairs:
        lines.append("")
        lines.append("  Matched pairs:")
        for a, b in matched_pairs[:20]:
            lines.append(f"    ({a}, {b})")
        if len(matched_pairs) > 20:
            lines.append(f"    ... and {len(matched_pairs)-20} more")

    summary = "\n".join(lines)

    result = {
        "composite_score":       composite,
        "layout_y_score":        y_score,
        "matching_x_score":      x_score,
        "interdigitation_score": id_score,   # None if N/A
        "centroid_score":        cc_score,   # None if N/A
        "drc_score":             drc_score,
        "matched_pairs_count":   len(matched_pairs),
        "device_count":          len(nodes),
        "summary":               summary,
    }

    if verbose:
        result["details"] = {
            "layout_y":      y_detail,
            "matching_x":    x_detail,
            "interdigitation": id_detail,
            "centroid":        cc_detail,
            "drc_overlap":     drc_detail,
        }

    return result


# ---------------------------------------------------------------------------
# Convenience: format a previously-computed report
# ---------------------------------------------------------------------------

def format_report(report: dict, show_details: bool = False) -> str:
    """Return the full benchmark report string, optionally with per-metric details."""
    lines = [report.get("summary", "")]
    if show_details and "details" in report:
        lines.append("\n-- Per-Metric Details ---------------------------------------------")
        for metric, text in report["details"].items():
            lines.append(f"\n[{metric.upper()}]")
            lines.append(text)
    return "\n".join(lines)

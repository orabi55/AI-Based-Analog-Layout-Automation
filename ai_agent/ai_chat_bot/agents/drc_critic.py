"""
DRC Critic Agent
================
Validates proposed placements for:
  - Axis-aligned bounding-box overlaps between devices
  - Minimum gap violations between adjacent devices in the same row

Enhanced with prescriptive geometry hints and a pure-Python fix generator
(compute_prescriptive_fixes) for closed-loop self-correction without LLM.
"""

from itertools import groupby

from ai_agent.ai_chat_bot.analog_kb import ANALOG_LAYOUT_RULES


# ---------------------------------------------------------------------------
# System prompt (7-section LayoutCopilot structure)
# ---------------------------------------------------------------------------
DRC_CRITIC_PROMPT = """\
ROLE:
You are a DRC (Design Rule Check) Critic.
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

CORE RULES (STRICT):
- NEVER invent coordinates.
- ALWAYS use the exact x/y values provided in the violation report.
- Fix EVERY violation (no skipping).
- Do NOT introduce new violations.
- PMOS must stay in PMOS rows (higher y).
- NMOS must stay in NMOS rows (lower y).

PROCEDURE:
For each violation:

1) OVERLAP: Move the right-side device to the prescribed x.

2) GAP: Move device B to the prescribed x.

3) ROW_ERROR: Move the device to the prescribed y.

4) CASCADE CHECK: If a move causes a new overlap with another device, fix that device as well.

OUTPUT FORMAT (STRICT):
- Output ONLY [CMD] blocks, then ONE summary line.
- No explanations. No markdown. No extra text.
- Each block must be valid JSON on ONE line.

FORMAT:
[CMD]{"action":"move","device":"MM1","x":0.588}[/CMD]

CONSTRAINTS:
- Max commands = 2 × number of violations
- Do NOT repeat unchanged commands
- Do NOT ask questions

FINAL CHECK (before output):
- Every violation is fixed
- All coordinates match the report exactly
- No new overlaps introduced
- Device types remain in correct rows

OUTPUT:
[CMD] blocks first
Then one-line summary
""" + "\n\n" + ANALOG_LAYOUT_RULES


# ---------------------------------------------------------------------------
# Structured violation type for machine-readable feedback
# ---------------------------------------------------------------------------
class DRCViolation:
    """Carries geometric details about one DRC violation."""
    __slots__ = (
        "kind",      # "OVERLAP" | "GAP"
        "dev_a", "dev_b",
        "x1_a", "x2_a", "y_a", "w_a",
        "x1_b", "x2_b", "y_b", "w_b",
        "gap_required", "gap_actual",
        "text",       # human-readable with prescriptive hint
    )

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


# ---------------------------------------------------------------------------
# Pure-Python DRC checker (no Qt, no LLM)
# ---------------------------------------------------------------------------
def run_drc_check(nodes, gap_px=0.0):
    """Check nodes for overlap and gap violations.

    Args:
        nodes: list of placement node dicts (must have geometry.x/y/width/height)
        gap_px: minimum required gap between adjacent devices (default 0).

    Returns:
        dict: {
            "pass": bool,
            "violations": [str, ...],           # human-readable with prescriptive hints
            "structured": [DRCViolation, ...],  # machine-readable objects
            "summary": str,
            "pmos_row_y": float | None,         # detected PMOS row y
            "nmos_row_y": float | None,         # detected NMOS row y
        }
    """
    violation_texts = []
    violation_set = set()   # O(1) dedup guard replacing list-membership checks
    structured = []
    valid = [n for n in nodes if "geometry" in n]

    # ---- Dynamic row detection ----
    # Build a SET of valid y-values per device type.
    # Using a set (not majority-vote) correctly handles multi-row layouts:
    #   e.g. common-centroid NMOS on y=0.000, 0.668, 1.336  — all three are valid.
    pmos_ys: set = set()
    nmos_ys: set = set()
    for n in valid:
        y_rounded = round(float(n["geometry"]["y"]), 4)
        t = str(n.get("type", "")).strip().lower()
        if t == "pmos":
            pmos_ys.add(y_rounded)
        elif t == "nmos":
            nmos_ys.add(y_rounded)

    # For legacy callers that read pmos_row_y / nmos_row_y as a single float,
    # expose representative sentinels under the convention PMOS y > NMOS y.
    pmos_row_y = max(pmos_ys) if pmos_ys else None
    nmos_row_y = min(nmos_ys) if nmos_ys else None

    # Build bounding box list — cache geo dict once per node
    boxes = []
    for n in valid:
        geo = n["geometry"]
        x = float(geo.get("x", 0))
        y = float(geo.get("y", 0))
        w = float(geo.get("width", 1))
        h = float(geo.get("height", 1))
        boxes.append({
            "id": n["id"],
            "x1": x, "y1": y,
            "x2": x + w, "y2": y + h,
            "w": w, "h": h,
            "row": round(y, 4),
        })

    def _add_violation(text, sv):
        """Append a violation only if not already recorded (O(1) dedup)."""
        if text not in violation_set:
            violation_set.add(text)
            violation_texts.append(text)
            structured.append(sv)

    # ---- Overlap check (O(n²)) ----
    for i in range(len(boxes)):
        a = boxes[i]
        a_x1, a_x2, a_y1, a_y2, a_h = a["x1"], a["x2"], a["y1"], a["y2"], a["h"]
        for j in range(i + 1, len(boxes)):
            b = boxes[j]
            # Skip clearly different rows
            if abs(a["row"] - b["row"]) > max(a_h, b["h"]) * 0.5:
                continue
            if not (a_x1 < b["x2"] and b["x1"] < a_x2):
                continue
            if not (a_y1 < b["y2"] and b["y1"] < a_y2):
                continue
            # Prescriptive fix: shift the right-side device clear of the left one
            if a_x1 <= b["x1"]:
                fix_dev, fix_x, fix_y = b["id"], round(a_x2 + gap_px, 4), b["y1"]
            else:
                fix_dev, fix_x, fix_y = a["id"], round(b["x2"] + gap_px, 4), a_y1
            text = (
                f"OVERLAP: {a['id']} ∩ {b['id']}  "
                f"({a['id']}=[x:{a_x1:.3f}→{a_x2:.3f}, y:{a_y1:.3f}]  "
                f"{b['id']}=[x:{b['x1']:.3f}→{b['x2']:.3f}, y:{b['y1']:.3f}])  "
                f"→ MOVE {fix_dev} to x={fix_x:.3f}, y={fix_y:.3f}"
            )
            _add_violation(text, DRCViolation(
                kind="OVERLAP",
                dev_a=a["id"], dev_b=b["id"],
                x1_a=a_x1, x2_a=a_x2, y_a=a_y1, w_a=a["w"],
                x1_b=b["x1"], x2_b=b["x2"], y_b=b["y1"], w_b=b["w"],
                gap_required=gap_px, gap_actual=b["x1"] - a_x2,
                text=text,
            ))

    # ---- Row-Type check ----
    # Two-stage approach:
    #  Stage A — Cross-type ordering: PMOS rows must be above NMOS rows,
    #            i.e. PMOS y > NMOS y in this editor.
    #  Stage B — Set membership: within a valid side of the ordering, flag any
    #            device y that does not match known rows of that device type.
    nmos_top_y    = max(nmos_ys) if nmos_ys else None
    pmos_bottom_y = min(pmos_ys) if pmos_ys else None

    if pmos_ys or nmos_ys:
        for n in valid:
            dev_id   = n["id"]
            dev_type = str(n.get("type", "")).strip().lower()
            geo      = n["geometry"]           # already confirmed present
            x_geo    = float(geo.get("x", 0))
            w_geo    = float(geo.get("width", 1))
            y        = round(float(geo.get("y", 0)), 4)

            def _row_violation(text, correct_y):
                _add_violation(text, DRCViolation(
                    kind="ROW_ERROR",
                    dev_a=dev_id, dev_b=None,
                    x1_a=x_geo, x2_a=x_geo + w_geo,
                    y_a=y, w_a=w_geo,
                    x1_b=0, x2_b=0, y_b=correct_y, w_b=0,
                    gap_required=0, gap_actual=0,
                    text=text,
                ))

            if dev_type == "pmos":
                # Stage A: PMOS must be above the NMOS band (larger y)
                if nmos_top_y is not None and y <= nmos_top_y:
                    correct_y = max(pmos_ys) if pmos_ys else round(nmos_top_y + 0.668, 4)
                    _row_violation(
                        f"FATAL ROW ERROR: Device {dev_id} is a PMOS but is at y={y:.4f} "
                        f"(must be above NMOS rows, y>{nmos_top_y:.4f}). "
                        f"Move it to y={correct_y:.4f}.",
                        correct_y,
                    )
                # Stage B: PMOS on valid side but not in known PMOS rows
                elif nmos_top_y is not None and y > nmos_top_y and pmos_ys and y not in pmos_ys:
                    correct_y = max(pmos_ys)
                    _row_violation(
                        f"FATAL ROW ERROR: Device {dev_id} is a PMOS at y={y:.4f}. "
                        f"Valid PMOS row(s): {sorted(pmos_ys)}. "
                        f"Move it to y={correct_y:.4f}.",
                        correct_y,
                    )

            elif dev_type == "nmos":
                # Stage A: NMOS must be below the PMOS band (smaller y)
                if pmos_bottom_y is not None and y >= pmos_bottom_y:
                    correct_y = min(nmos_ys) if nmos_ys else round(pmos_bottom_y - 0.668, 4)
                    _row_violation(
                        f"FATAL ROW ERROR: Device {dev_id} is an NMOS but is at y={y:.4f} "
                        f"(must be below PMOS rows, y<{pmos_bottom_y:.4f}). "
                        f"Move it to y={correct_y:.4f}.",
                        correct_y,
                    )
                # Stage B: NMOS on valid side but not in known NMOS rows
                elif pmos_bottom_y is not None and y < pmos_bottom_y and nmos_ys and y not in nmos_ys:
                    correct_y = min(nmos_ys)
                    _row_violation(
                        f"FATAL ROW ERROR: Device {dev_id} is an NMOS at y={y:.4f}. "
                        f"Valid NMOS row(s): {sorted(nmos_ys)}. "
                        f"Move it to y={correct_y:.4f}.",
                        correct_y,
                    )

    # ---- Gap check (same row, adjacent) ----
    if gap_px > 0:
        boxes_sorted = sorted(boxes, key=lambda bx: (bx["row"], bx["x1"]))
        for _row, grp in groupby(boxes_sorted, key=lambda bx: bx["row"]):
            row_devs = list(grp)
            for k in range(len(row_devs) - 1):
                a, bx = row_devs[k], row_devs[k + 1]
                gap = bx["x1"] - a["x2"]
                if 0 <= gap < gap_px:
                    fix_x = round(a["x2"] + gap_px, 4)
                    text = (
                        f"GAP.VIOLATION: {a['id']} → {bx['id']}  "
                        f"actual_gap={gap:.3f}px  required≥{gap_px:.1f}px  "
                        f"→ MOVE {bx['id']} to x={fix_x:.3f}, y={bx['y1']:.3f}"
                    )
                    _add_violation(text, DRCViolation(
                        kind="GAP",
                        dev_a=a["id"], dev_b=bx["id"],
                        x1_a=a["x1"], x2_a=a["x2"], y_a=a["y1"], w_a=a["w"],
                        x1_b=bx["x1"], x2_b=bx["x2"], y_b=bx["y1"], w_b=bx["w"],
                        gap_required=gap_px, gap_actual=gap,
                        text=text,
                    ))

    passed = len(violation_texts) == 0
    summary = (
        "DRC PASSED – no overlap or gap violations."
        if passed
        else f"DRC FAILED – {len(violation_texts)} violation(s):\n"
        + "\n".join(f"  • {v}" for v in violation_texts)
    )
    return {
        "pass": passed,
        "violations": violation_texts,
        "structured": structured,
        "summary": summary,
        "pmos_row_y": pmos_row_y,
        "nmos_row_y": nmos_row_y,
    }


def compute_prescriptive_fixes(drc_result, gap_px=0.0, nodes=None):
    """Generate exact corrective move commands from structured DRC violations.

    Uses per-device *proposed_x* tracking so cascaded collisions (multiple
    devices at the same x) each receive a DISTINCT non-overlapping position.
    Uses per-row slot tracking ({row_y: sorted list of occupied x-slots}) to
    prevent two devices from being placed at the same position. Slot conflict
    detection is O(log n) via bisect rather than O(n) linear scan.

    This is a PURE-PYTHON fallback — no LLM needed.

    Args:
        drc_result:  dict from run_drc_check()
        gap_px:      minimum device gap
        nodes:       original node list (used to pre-populate occupied slots)

    Returns:
        list[dict]: command dicts ready to emit as [CMD] blocks
    """
    import bisect

    # Map dev_id → (proposed_x, dev_width) so chained overlaps get distinct slots
    proposed_x: dict = {}
    proposed_w: dict = {}
    for v in drc_result.get("structured", []):
        if v.dev_a:
            proposed_w.setdefault(v.dev_a, v.w_a)
        if v.dev_b:
            proposed_w.setdefault(v.dev_b, v.w_b)

    # Build per-row occupied x lists (sorted) from existing node positions.
    # Sorted lists enable O(log n) bisect-based conflict checks in _find_free_x.
    # key = round(row_y, 4); value = sorted list of round(x, 4)
    occupied: dict = {}   # row_y → sorted list[float]
    if nodes:
        _tmp: dict = {}
        for n in nodes:
            geo = n.get("geometry", {})
            ry = round(float(geo.get("y", 0)), 4)
            rx = round(float(geo.get("x", 0)), 4)
            _tmp.setdefault(ry, set()).add(rx)
        occupied = {ry: sorted(xs) for ry, xs in _tmp.items()}

    def _find_free_x(row_y: float, want_x: float, w: float) -> float:
        """Return smallest x >= want_x with no slot overlap in row_y.

        Conflict condition: an existing slot s overlaps [x, x+w) when
        abs(s - x) < w  (both devices assumed same width w).
        Uses bisect to jump to the relevant portion of the sorted slot list,
        giving O(log n) per probe instead of O(n).
        """
        row_y = round(row_y, 4)
        slots: list = occupied.get(row_y, [])
        x = round(want_x, 4)
        w_r = round(w, 4)
        step = w_r if w_r > 0 else 0.294

        while True:
            # Find first slot that could overlap: s >= x - w_r + ε
            lo = bisect.bisect_left(slots, x - w_r + 1e-6)
            conflict = lo < len(slots) and abs(slots[lo] - x) < w_r - 1e-6
            if not conflict:
                return round(x, 4)
            x = round(x + step, 4)

    def _register_slot(row_y: float, x: float):
        """Insert x into the sorted occupied list for row_y."""
        row_y = round(row_y, 4)
        x = round(x, 4)
        lst = occupied.setdefault(row_y, [])
        idx = bisect.bisect_left(lst, x)
        if idx >= len(lst) or lst[idx] != x:
            lst.insert(idx, x)

    cmds = []
    cmd_map: dict = {}   # dev_id → cmd dict (for in-place updates)

    for v in drc_result.get("structured", []):
        if v.kind == "ROW_ERROR":
            correct_y = v.y_b
            dev_id    = v.dev_a
            x_cur     = proposed_x.get(dev_id, v.x1_a)
            w         = proposed_w.get(dev_id, v.w_a)
            free_x    = _find_free_x(correct_y, x_cur, w)
            _register_slot(correct_y, free_x)
            proposed_x[dev_id] = free_x
            if dev_id not in cmd_map:
                c = {"action": "move", "device": dev_id, "x": free_x, "y": correct_y}
                cmds.append(c)
                cmd_map[dev_id] = c
            else:
                cmd_map[dev_id]["y"] = correct_y
                cmd_map[dev_id]["x"] = free_x

        elif v.kind == "OVERLAP":
            x_a = proposed_x.get(v.dev_a, v.x1_a)
            x_b = proposed_x.get(v.dev_b, v.x1_b)
            w_a = proposed_w.get(v.dev_a, v.w_a)
            w_b = proposed_w.get(v.dev_b, v.w_b)

            if x_b >= x_a:
                target_dev = v.dev_b
                raw_clear  = round(x_a + w_a + gap_px, 4)
                move_y     = v.y_b
                w_t        = w_b
            else:
                target_dev = v.dev_a
                raw_clear  = round(x_b + w_b + gap_px, 4)
                move_y     = v.y_a
                w_t        = w_a

            clear_x = _find_free_x(move_y, raw_clear, w_t)
            _register_slot(move_y, clear_x)
            proposed_x[target_dev] = clear_x

            if target_dev not in cmd_map:
                c = {"action": "move", "device": target_dev, "x": clear_x, "y": move_y}
                cmds.append(c)
                cmd_map[target_dev] = c
            else:
                if clear_x > cmd_map[target_dev]["x"]:
                    cmd_map[target_dev]["x"] = clear_x
                    proposed_x[target_dev]   = clear_x

        elif v.kind == "GAP":
            x_a       = proposed_x.get(v.dev_a, v.x1_a)
            w_a       = proposed_w.get(v.dev_a, v.w_a)
            raw_new_x = round(x_a + w_a + gap_px, 4)
            w_b       = proposed_w.get(v.dev_b, v.w_b)
            new_x     = _find_free_x(v.y_b, raw_new_x, w_b)
            _register_slot(v.y_b, new_x)
            proposed_x[v.dev_b] = new_x

            if v.dev_b not in cmd_map:
                c = {"action": "move", "device": v.dev_b, "x": new_x, "y": v.y_b}
                cmds.append(c)
                cmd_map[v.dev_b] = c
            else:
                if new_x > cmd_map[v.dev_b]["x"]:
                    cmd_map[v.dev_b]["x"] = new_x
                    proposed_x[v.dev_b]   = new_x

    return cmds


def format_drc_violations_for_llm(drc_result, prior_cmds_text=""):
    """Format run_drc_check output into an LLM prompt snippet.

    Includes prescriptive geometry hints and the prior failed CMDs for
    context-preserving retry (so the LLM doesn't repeat the same mistake).
    """
    if drc_result["pass"]:
        return "DRC: All clear – no violations detected."

    lines = [
        f"═══ DRC VIOLATIONS ({len(drc_result['violations'])} found) ═══",
        "Each entry includes a PRESCRIPTIVE FIX with exact coordinates:",
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

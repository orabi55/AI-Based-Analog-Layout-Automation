"""
DRC Critic Agent
================
Validates proposed placements for:
  - Axis-aligned bounding-box overlaps between devices
  - Minimum gap violations between adjacent devices in the same row

Enhanced with prescriptive geometry hints and a pure-Python fix generator
(compute_prescriptive_fixes) for closed-loop self-correction without LLM.
"""

from ai_agent.analog_kb import ANALOG_LAYOUT_RULES


# ---------------------------------------------------------------------------
# System prompt (7-section LayoutCopilot structure)
# ---------------------------------------------------------------------------
DRC_CRITIC_PROMPT = """\
### I. ROLE PLAY
You are a DRC (Design Rule Check) Critic. You review placement commands
for geometric violations and output corrected [CMD] blocks.
You are precise and methodical — you fix EVERY violation, never skip one.

### II. WORKFLOW OVERVIEW
You are Stage 3 of a 4-stage pipeline:
  Stage 1: Topology Analyst — constraint extraction.
  Stage 2: Placement Specialist — generated the [CMD] blocks you are reviewing.
  Stage 3 (YOU): DRC Critic — fix violations, output corrected [CMD] blocks.
  Stage 4: Routing Pre-Viewer — will optimise crossings after your fixes.

### III. TASK DESCRIPTION
Fix OVERLAP and GAP violations using move [CMD] blocks.
The violation report includes EXACT prescriptive x values after the '→' symbol.
Use those x values DIRECTLY — never invent coordinates.

### IV. PIPELINE (follow these steps internally)
Step 1: Read each violation — note device A, device B, prescribed x and y.
Step 2: For OVERLAP: move the right-side device to prescribed x.
Step 3: For GAP: shift device B right by the deficit (use prescribed x).
Step 4: Check cascade: if moving B causes a new overlap with C, fix C too.
Step 5: For ROW_ERROR: move the device back to its correct row y.
Step 6: Output all fix [CMD] blocks first, then a one-line summary.

### V. INFORMATION VERIFICATION
Before responding, verify:
  [ ] Every violation has a matching [CMD] block.
  [ ] I used the prescriptive x value from the report (not an invented one).
  [ ] I did NOT introduce new overlaps by shifting devices.
  [ ] PMOS devices kept their PMOS row y; NMOS kept NMOS row y.
If a check fails, revise the [CMD] blocks before outputting.

### VI. INTERACTION GUIDELINE
Output [CMD] blocks ONLY. One-line summary after. No questions.
- Output ONLY raw [CMD]...[/CMD] blocks. 
- Do NOT wrap output in markdown code fences (``` or ```json).
- Do NOT use unicode or full-width brackets.
- Each [CMD] block must contain valid JSON on a single line.
- Example of correct format:
[CMD]{"action": "move", "device": "MM1", "x": 0.588}[/CMD]
[CMD]{"action": "move", "device": "MM2", "x": 0.882}[/CMD]
Max [CMD] blocks = 2 × number of violations.
Do NOT ask the user for confirmation.

### VII. EXTERNAL KNOWLEDGE
PMOS row y < NMOS row y in this editor.
Never assign a PMOS device to NMOS row y, or vice versa.
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
    structured = []
    valid = [n for n in nodes if "geometry" in n]

    # ---- Dynamic row detection ----
    # Identify the canonical y for each type by majority vote (most common y per type)
    from collections import Counter
    pmos_ys = Counter(round(n["geometry"]["y"], 4) for n in valid if str(n.get("type","")).strip().lower() == "pmos")
    nmos_ys = Counter(round(n["geometry"]["y"], 4) for n in valid if str(n.get("type","")).strip().lower() == "nmos")
    pmos_row_y = pmos_ys.most_common(1)[0][0] if pmos_ys else None
    nmos_row_y = nmos_ys.most_common(1)[0][0] if nmos_ys else None

    # Build bounding box list
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

    # ---- Overlap check (O(n²)) ----
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            # Skip clearly different rows
            if abs(a["row"] - b["row"]) > max(a["h"], b["h"]) * 0.5:
                continue
            overlap_x = a["x1"] < b["x2"] and b["x1"] < a["x2"]
            overlap_y = a["y1"] < b["y2"] and b["y1"] < a["y2"]
            if overlap_x and overlap_y:
                # Prescriptive fix: shift the right-side device clear of the left one
                if a["x1"] <= b["x1"]:
                    fix_dev, fix_x, fix_y = b["id"], round(a["x2"] + gap_px, 4), b["y1"]
                else:
                    fix_dev, fix_x, fix_y = a["id"], round(b["x2"] + gap_px, 4), a["y1"]
                text = (
                    f"OVERLAP: {a['id']} ∩ {b['id']}  "
                    f"({a['id']}=[x:{a['x1']:.3f}→{a['x2']:.3f}, y:{a['y1']:.3f}]  "
                    f"{b['id']}=[x:{b['x1']:.3f}→{b['x2']:.3f}, y:{b['y1']:.3f}])  "
                    f"→ MOVE {fix_dev} to x={fix_x:.3f}, y={fix_y:.3f}"
                )
                violation_texts.append(text)
                structured.append(DRCViolation(
                    kind="OVERLAP",
                    dev_a=a["id"], dev_b=b["id"],
                    x1_a=a["x1"], x2_a=a["x2"], y_a=a["y1"], w_a=a["w"],
                    x1_b=b["x1"], x2_b=b["x2"], y_b=b["y1"], w_b=b["w"],
                    gap_required=gap_px, gap_actual=b["x1"] - a["x2"],
                    text=text,
                ))

    # ---- Row-Type check ----
    # Dynamically detect expected row y per device type and flag outliers.
    if pmos_row_y is not None and nmos_row_y is not None:
        for n in valid:
            dev_id = n["id"]
            dev_type = str(n.get("type", "")).strip().lower()
            geo = n.get("geometry", {})
            y = float(geo.get("y", 0))

            if dev_type == "pmos" and abs(y - pmos_row_y) > 0.01:
                correct_y = pmos_row_y
                text = (
                    f"FATAL ROW ERROR: Device {dev_id} is a PMOS but is at y={y:.4f}. "
                    f"PMOS row y={correct_y:.4f}. Move it back to y={correct_y:.4f}."
                )
                if text not in violation_texts:
                    violation_texts.append(text)
                    structured.append(DRCViolation(
                        kind="ROW_ERROR",
                        dev_a=dev_id, dev_b=None,
                        x1_a=geo.get("x",0), x2_a=geo.get("x",0)+geo.get("width",1),
                        y_a=y, w_a=geo.get("width",1),
                        x1_b=0, x2_b=0, y_b=correct_y, w_b=0,
                        gap_required=0, gap_actual=0,
                        text=text,
                    ))
            elif dev_type == "nmos" and abs(y - nmos_row_y) > 0.01:
                correct_y = nmos_row_y
                text = (
                    f"FATAL ROW ERROR: Device {dev_id} is an NMOS but is at y={y:.4f}. "
                    f"NMOS row y={correct_y:.4f}. Move it back to y={correct_y:.4f}."
                )
                if text not in violation_texts:
                    violation_texts.append(text)
                    structured.append(DRCViolation(
                        kind="ROW_ERROR",
                        dev_a=dev_id, dev_b=None,
                        x1_a=geo.get("x",0), x2_a=geo.get("x",0)+geo.get("width",1),
                        y_a=y, w_a=geo.get("width",1),
                        x1_b=0, x2_b=0, y_b=correct_y, w_b=0,
                        gap_required=0, gap_actual=0,
                        text=text,
                    ))

    # ---- Gap check (same row, adjacent) ----
    if gap_px > 0:
        from itertools import groupby
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
                    violation_texts.append(text)
                    structured.append(DRCViolation(
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
    Uses per-row slot tracking ({row_y: set of occupied x-slots}) to prevent
    two devices from being placed at the same position.

    This is a PURE-PYTHON fallback — no LLM needed.

    Args:
        drc_result:  dict from run_drc_check()
        gap_px:      minimum device gap
        nodes:       original node list (used to pre-populate occupied slots)

    Returns:
        list[dict]: command dicts ready to emit as [CMD] blocks
    """
    # Map dev_id → (proposed_x, dev_width) so chained overlaps get distinct slots
    proposed_x = {}  # updated as we plan each move
    proposed_w = {}  # device width
    for v in drc_result.get("structured", []):
        if v.dev_a:
            proposed_w.setdefault(v.dev_a, v.w_a)
        if v.dev_b:
            proposed_w.setdefault(v.dev_b, v.w_b)

    # Build per-row occupied x sets from existing node positions
    # key = round(row_y, 4); value = set of round(x, 4)
    occupied: dict = {}
    if nodes:
        for n in nodes:
            geo = n.get("geometry", {})
            ry = round(float(geo.get("y", 0)), 4)
            rx = round(float(geo.get("x", 0)), 4)
            occupied.setdefault(ry, set()).add(rx)

    def _find_free_x(row_y, want_x, w):
        """Return the smallest x >= want_x that doesn't overlap any occupied slot in row_y."""
        row_y = round(row_y, 4)
        slots = occupied.get(row_y, set())
        x = round(want_x, 4)
        w_r = round(w, 4)
        # Round x to nearest multiple of w (slot grid)
        step = w_r if w_r > 0 else 0.294
        while True:
            # Check if any occupied slot overlaps [x, x+w)
            conflict = any(abs(s - x) < w_r - 1e-6 for s in slots)
            if not conflict:
                return round(x, 4)
            x = round(x + step, 4)

    cmds = []
    cmd_map = {}   # dev_id → cmd dict (for updating in place)

    for v in drc_result.get("structured", []):
        if v.kind == "ROW_ERROR":
            # y_b holds the correct row y that was detected dynamically
            correct_y = v.y_b
            dev_id = v.dev_a
            x_cur = proposed_x.get(dev_id, v.x1_a)
            w = proposed_w.get(dev_id, v.w_a)
            # Find a free slot in the correct row
            free_x = _find_free_x(correct_y, x_cur, w)
            row_key = round(correct_y, 4)
            occupied.setdefault(row_key, set()).add(round(free_x, 4))
            proposed_x[dev_id] = free_x
            if dev_id not in cmd_map:
                c = {"action": "move", "device": dev_id, "x": free_x, "y": correct_y}
                cmds.append(c)
                cmd_map[dev_id] = c
            else:
                cmd_map[dev_id]["y"] = correct_y
                cmd_map[dev_id]["x"] = free_x

        elif v.kind == "OVERLAP":
            # Use already-proposed x if the device was scheduled earlier
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

            # Use slot-aware search to avoid placing on an occupied position
            clear_x = _find_free_x(move_y, raw_clear, w_t)
            row_key = round(move_y, 4)
            occupied.setdefault(row_key, set()).add(round(clear_x, 4))
            proposed_x[target_dev] = clear_x

            if target_dev not in cmd_map:
                c = {"action": "move", "device": target_dev, "x": clear_x, "y": move_y}
                cmds.append(c)
                cmd_map[target_dev] = c
            else:
                # Device already scheduled — bump x further right if needed
                if clear_x > cmd_map[target_dev]["x"]:
                    cmd_map[target_dev]["x"] = clear_x
                    proposed_x[target_dev]   = clear_x

        elif v.kind == "GAP":
            # Use proposed right edge of dev_a to compute the gap fix
            x_a  = proposed_x.get(v.dev_a, v.x1_a)
            w_a  = proposed_w.get(v.dev_a, v.w_a)
            raw_new_x = round(x_a + w_a + gap_px, 4)
            new_x = _find_free_x(v.y_b, raw_new_x, proposed_w.get(v.dev_b, v.w_b))
            row_key = round(v.y_b, 4)
            occupied.setdefault(row_key, set()).add(round(new_x, 4))
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
    for i, v in enumerate(drc_result["violations"], 1):
        lines.append(f"  [{i}] {v}")

    if prior_cmds_text.strip():
        lines.append("")
        lines.append("═══ PRIOR FAILED [CMD] BLOCKS (context — do NOT repeat unchanged) ═══")
        lines.append(prior_cmds_text.strip()[:2000])

    lines.append("")
    lines.append("Use the exact x/y values from the prescriptive hints above in your [CMD] blocks.")
    return "\n".join(lines)

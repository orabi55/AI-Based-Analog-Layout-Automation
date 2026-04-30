"""
File Description:
This module provides console logging helpers and stage progress tracking for the initial-placement LangGraph pipeline. It ensures structured formatting, timestamps, and dual output to both stdout and a log file.

Functions:
- _safe_print:
    - Role: Prints text to both stdout and the log file, handling potential encoding errors.
    - Inputs: 
        - *args: Variable length argument list for print.
        - **kwargs: Arbitrary keyword arguments for print.
    - Outputs: None
- steps_only:
    - Role: Determines if the logging should only show high-level placement steps based on environment variables.
    - Inputs: None
    - Outputs: (bool) True if only steps should be shown.
- vprint:
    - Role: Prints verbose debug messages when not in step-only mode, and always writes to the log file.
    - Inputs: 
        - *args: Variable length argument list for print.
        - **kwargs: Arbitrary keyword arguments for print.
    - Outputs: None
- ip_step:
    - Role: Logs a specific pipeline milestone with consistent formatting and status icons.
    - Inputs: 
        - stage (str): The stage identifier (e.g., "1/5").
        - message (str): The status message to log.
    - Outputs: None
- pipeline_start:
    - Role: Prints a pipeline banner, initializes timing, and clears the log file.
    - Inputs: 
        - name (str): The name of the pipeline.
        - total_stages (int): Total number of stages in the pipeline.
        - config (dict | None): Optional configuration parameters for display.
    - Outputs: None
- pipeline_end:
    - Role: Prints a final placement summary block with metrics like layout size, DRC status, and total time.
    - Inputs: 
        - summary (dict | None): Dictionary containing summary metrics.
    - Outputs: None
- stage_start:
    - Role: Marks the beginning of a pipeline stage and logs its header.
    - Inputs: 
        - stage_num (int): The current stage number.
        - name (str): The name of the stage.
    - Outputs: (float) The start time of the stage.
- stage_end:
    - Role: Logs the completion of a pipeline stage with its elapsed time and status.
    - Inputs: 
        - stage_num (int): The current stage number.
        - name (str): The name of the stage.
        - status (str): The completion status.
        - details (str): Additional details to log.
    - Outputs: None
- log_section:
    - Role: Logs a section header to the placement log.
    - Inputs: 
        - title (str): The title of the section.
    - Outputs: None
- log_detail:
    - Role: Logs a detail line to the placement log.
    - Inputs: 
        - msg (str): The message to log.
    - Outputs: None
- log_table:
    - Role: Logs a formatted table to the placement log.
    - Inputs: 
        - headers (list): List of table header strings.
        - rows (list): List of row data (each row is a list).
        - col_widths (list | None): Optional list of column widths.
    - Outputs: None
- log_device_positions:
    - Role: Logs a summary table of device positions grouped by type and row.
    - Inputs: 
        - nodes (list): List of device nodes with geometry info.
        - label (str): Label for the position summary.
    - Outputs: None
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime


# -- Global pipeline state --------------------------------------------------
_pipeline_start: float | None = None
_stage_times: dict[str, float] = {}
_pipeline_name: str = "LangGraph"
_total_stages: int = 5
_log_file_path: str = "placement_live_output.log"


def _safe_print(*args, **kwargs) -> None:
    """Print to BOTH stdout and log file. Never raises on encoding errors."""
    text = " ".join(str(a) for a in args)

    # Always print to stdout so the user sees it in the terminal
    kwargs.setdefault("flush", True)
    try:
        print(text, **kwargs)
    except UnicodeEncodeError:
        safe = text.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
            sys.stdout.encoding or "utf-8", errors="replace"
        )
        print(safe, **kwargs)

    # Also write to log file
    try:
        with open(_log_file_path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception:
        pass


def steps_only() -> bool:
    return os.environ.get("PLACEMENT_STEPS_ONLY", "0").lower() in (
        "1",
        "true",
        "yes",
    )


def vprint(*args, **kwargs) -> None:
    """Print when not in step-only mode (full debug / legacy behavior).
    
    Always writes to the log file regardless of step-only mode.
    """
    text = " ".join(str(a) for a in args)
    # Always write to log file
    try:
        with open(_log_file_path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception:
        pass
    # Only print to stdout if not step-only
    if not steps_only():
        kwargs.setdefault("flush", True)
        try:
            print(text, **kwargs)
        except UnicodeEncodeError:
            pass


def ip_step(stage: str, message: str) -> None:
    """Always printed: one line per pipeline milestone."""
    ts = datetime.now().strftime("%H:%M:%S")
    # Parse stage number for visual formatting
    parts = stage.split("/")
    if len(parts) == 2:
        try:
            current = int(parts[0])
            total = int(parts[1].split()[0])
            stage_name = " ".join(parts[1].split()[1:])
            # Determine status icon from message
            if "pass" in message.lower() or "ok" in message.lower():
                icon = "[OK]"
            elif "fail" in message.lower():
                icon = "[!!]"
            else:
                icon = "[..]"
            # Format with alignment
            label = f"[{current}/{total}] {stage_name}"
            _safe_print(f"  {ts}  {label:<30s} {icon}  {message}")
            return
        except (ValueError, IndexError):
            pass
    _safe_print(f"  {ts}  [IP] {stage} -- {message}")


# -- Pipeline lifecycle -----------------------------------------------------

def pipeline_start(name: str, total_stages: int, config: dict | None = None) -> None:
    """Print a clear pipeline banner and start timing."""
    global _pipeline_start, _stage_times, _pipeline_name, _total_stages
    _pipeline_start = time.time()
    _stage_times = {}
    _pipeline_name = name
    _total_stages = total_stages

    try:
        with open(_log_file_path, "w", encoding="utf-8") as f:
            pass  # clear the log file
    except Exception:
        pass

    cfg = config or {}
    model = cfg.get("model", "?")
    devices = cfg.get("devices", "?")
    n_pmos = cfg.get("n_pmos", "?")
    n_nmos = cfg.get("n_nmos", "?")
    abutment = "On" if cfg.get("abutment") else "Off"

    bar = "=" * 62
    _safe_print()
    _safe_print(f"+{bar}+")
    _safe_print(f"|  AI INITIAL PLACEMENT -- {name:<36s}|")
    _safe_print(f"+{bar}+")
    _safe_print(f"|  Model      : {model:<47s}|")
    dev_str = f"{devices}   ({n_pmos} PMOS + {n_nmos} NMOS)"
    _safe_print(f"|  Devices    : {dev_str:<47s}|")
    _safe_print(f"|  Abutment   : {abutment:<47s}|")
    _safe_print(f"+{bar}+")
    _safe_print()


def pipeline_end(summary: dict | None = None) -> str:
    """Print a placement summary block with key metrics and return the formatted string."""
    global _pipeline_start
    elapsed = time.time() - _pipeline_start if _pipeline_start else 0
    _pipeline_start = None

    s = summary or {}
    out_lines = []
    def _print(line=""):
        out_lines.append(str(line))
        _safe_print(line)
    width       = s.get("width",        "?")
    height      = s.get("height",       "?")
    aspect      = s.get("aspect",       "?")
    area        = s.get("area",         "?")
    utilization = s.get("utilization",  "?")
    hpwl        = s.get("hpwl",         "--")
    drc_status  = s.get("drc_status",   "?")
    pmos_nmos_sep = s.get("pmos_nmos_sep", "?")
    n_placed    = s.get("n_placed",     "?")
    quality     = s.get("quality",      {})   # placement quality report dict

    mins = int(elapsed // 60)
    secs = int(elapsed % 60)

    bar = "=" * 62
    _print()
    _print(bar)
    _print("  PLACEMENT SUMMARY")
    _print(bar)
    if width != "?" and height != "?":
        _print(f"  Layout Size  : {width}um x {height}um  (aspect {aspect})")
        _print(f"  Total Area   : {area}")
        _print(f"  Utilization  : {utilization}")
    if hpwl != "--":
        _print(f"  HPWL         : {hpwl}um")
    _print(f"  DRC Status   : {drc_status}")
    _print(f"  PMOS/NMOS    : {pmos_nmos_sep}")
    _print(f"  Devices      : {n_placed} placed")
    _print(f"  Time Total   : {mins}m {secs}s")
    _print(bar)

    # -- Placement Quality Benchmark (printed after utilization) -------------
    if quality and isinstance(quality, dict):
        composite   = quality.get("composite_score",       0.0)
        y_score     = quality.get("layout_y_score",        0.0)
        x_score     = quality.get("matching_x_score")          # may be None (N/A)

        id_score    = quality.get("interdigitation_score")   # may be None
        cc_score    = quality.get("centroid_score")          # may be None
        drc_q_score = quality.get("drc_score",             0.0)
        n_pairs     = quality.get("matched_pairs_count",   0)

        def _bar(score: float, width: int = 20) -> str:
            filled = int(round(score * width))
            return "#" * filled + "-" * (width - filled)

        def _grade(score: float) -> str:
            if score >= 0.95: return "A+"
            if score >= 0.90: return "A"
            if score >= 0.80: return "B"
            if score >= 0.70: return "C"
            if score >= 0.50: return "D"
            return "F"

        def _row(label, val):
            if val is None:
                return f"  {label:<24}  {'N/A':>6}   {'(not applicable)':<22}"
            return (
                f"  {label:<24}  {val:>6.1%}   {_bar(val):<22}  {_grade(val)}"
            )

        qbar = "=" * 64
        _print()
        _print(qbar)
        _print("  MATCHING & SYMMETRY QUALITY BENCHMARK")
        _print(qbar)

        # -- Print placement goals that were active for this run ----------------
        goals = s.get("placement_goals") or {}
        if goals:
            mp = goals.get("matching_priority",  "Medium")
            sp = goals.get("symmetry_priority",  "Medium")
            ap = goals.get("area_priority",      "Medium")
            ma = goals.get("max_area_um2")
            _print(f"  Goals applied : Matching={mp}  Symmetry={sp}  Area={ap}"
                        + (f"  MaxArea={ma}um2" if ma else ""))

        _print(f"  Matched pairs : {n_pairs}")
        _print(f"  {'Metric':<24}  {'Score':>6}   {'Progress':<22}  Grade")
        _print(f"  {'-'*24}  {'-'*6}   {'-'*22}  -----")
        _print(_row("Layout Y Symmetry",   y_score))
        _print(_row("X Mirror Symmetry",   x_score))
        _print(_row("Interdigitation",      id_score))
        _print(_row("Common Centroid (2D)", cc_score))
        _print(_row("DRC Clean",           drc_q_score))
        _print(f"  {'-'*24}  {'-'*6}   {'-'*22}  -----")
        _print(
            f"  {'COMPOSITE':<24}  {composite:>6.1%}   {_bar(composite):<22}  {_grade(composite)}"
        )
        _print(qbar)

        # -- Explanatory notes based on active goals ----------------------------
        notes = []
        if goals:
            mp = goals.get("matching_priority", "Medium")
            sp = goals.get("symmetry_priority", "Medium")

            if sp == "Low" and (x_score or 0.0) >= 0.9:
                notes.append(
                    "NOTE: Symmetry=Low skipped the global mirror enforcer, but "
                    "ABBA interdigitation is inherently palindromic -- X Mirror "
                    "Symmetry will still score high. This is expected and correct."
                )
            if sp == "Low":
                notes.append(
                    "NOTE: Symmetry enforcer was disabled per user goal. "
                    "Global two-half axis mirroring was NOT applied."
                )
            if mp == "Low":
                notes.append(
                    "NOTE: Matching=Low skipped interdigitation entirely. "
                    "X Mirror / Interdigitation scores reflect natural placement only."
                )
            if mp == "Medium":
                notes.append(
                    "NOTE: Matching=Medium applied ABBA only for diff pairs and "
                    "current mirrors. Cross-coupled and load pairs were placed "
                    "individually without interdigitation."
                )
            if mp == "High":
                notes.append(
                    "NOTE: Matching=High applied ABBA/common-centroid for ALL "
                    "detected matched pairs (diff pairs, mirrors, cross-coupled, loads)."
                )

        for note in notes:
            # Word-wrap at 62 chars
            words = note.split()
            line = "  "
            for w in words:
                if len(line) + len(w) + 1 > 64:
                    _print(line)
                    line = "  " + w + " "
                else:
                    line += w + " "
            if line.strip():
                _print(line)
        if notes:
            _print()

    _print()
    return "\n".join(out_lines)



def stage_start(stage_num: int, name: str) -> float:
    """Mark the start of a pipeline stage and return the start time."""
    _stage_times[name] = time.time()
    _safe_print(f"\n{'─' * 62}")
    _safe_print(f"  STAGE {stage_num}/{_total_stages}: {name.upper()}")
    _safe_print(f"{'─' * 62}")
    return _stage_times[name]


def stage_end(stage_num: int, name: str, status: str, details: str = "") -> None:
    """Log stage completion with timing."""
    t0 = _stage_times.get(name, time.time())
    elapsed = time.time() - t0
    ip_step(f"{stage_num}/{_total_stages} {name}", f"{status} ({elapsed:.1f}s){' -- ' + details if details else ''}")


# -- Detail logging helpers -------------------------------------------------

def log_section(title: str) -> None:
    """Log a section header to the placement log."""
    _safe_print(f"\n  {'─' * 50}")
    _safe_print(f"  {title}")
    _safe_print(f"  {'─' * 50}")


def log_detail(msg: str) -> None:
    """Log a detail line to the placement log (always visible)."""
    _safe_print(f"    {msg}")


def log_table(headers: list, rows: list, col_widths: list | None = None) -> None:
    """Log a formatted table to the placement log."""
    if not rows:
        return
    if col_widths is None:
        col_widths = [max(len(str(h)), max(len(str(r[i])) for r in rows))
                      for i, h in enumerate(headers)]
    
    header_line = "  " + " | ".join(
        str(h).ljust(w) for h, w in zip(headers, col_widths)
    )
    _safe_print(header_line)
    _safe_print("  " + "-+-".join("-" * w for w in col_widths))
    for row in rows:
        _safe_print("  " + " | ".join(
            str(c).ljust(w) for c, w in zip(row, col_widths)
        ))


def log_device_positions(nodes: list, label: str = "Device Positions") -> None:
    """Log a summary table of device positions."""
    if not nodes:
        return
    log_section(label)
    
    # Group by type and row
    by_type_row = {}
    for n in nodes:
        if "geometry" not in n:
            continue
        dev_type = n.get("type", "?")
        y = round(float(n["geometry"].get("y", 0)), 4)
        key = (dev_type, y)
        by_type_row.setdefault(key, []).append(n)
    
    for (dev_type, y), row_nodes in sorted(by_type_row.items()):
        row_nodes_sorted = sorted(row_nodes, key=lambda n: float(n["geometry"].get("x", 0)))
        ids = ", ".join(n["id"] for n in row_nodes_sorted)
        xs = [float(n["geometry"].get("x", 0)) for n in row_nodes_sorted]
        x_range = f"x=[{min(xs):.3f} .. {max(xs):.3f}]" if xs else ""
        _safe_print(f"    {dev_type.upper():>5s} row y={y:<8.4f} ({len(row_nodes_sorted):>2d} devices) {x_range}")
        # Show each device on its own line for debugging
        for n in row_nodes_sorted:
            geo = n["geometry"]
            _safe_print(f"      {n['id']:<20s} x={float(geo.get('x',0)):>8.4f}  w={float(geo.get('width',0)):>6.4f}")

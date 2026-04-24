"""
Console logging helpers for the initial-placement LangGraph.

The UI initial-placement worker sets PLACEMENT_STEPS_ONLY=1 (unless
PLACEMENT_DEBUG_FULL_LOG=1) so the console shows only [IP] step lines, not
full prompts or [LLM_FACTORY] / hierarchy diagnostics.

Enhanced with structured formatting, timestamps, and stage progress tracking.
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


def _safe_print(*args, **kwargs) -> None:
    """Print that never raises on encoding errors and writes to a live log file."""
    text = " ".join(str(a) for a in args)
    
    # Optional: if you still want it in stdout too, uncomment the print
    # kwargs.setdefault("flush", True)
    # try:
    #     print(text, **kwargs)
    # except UnicodeEncodeError:
    #     safe = text.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
    #         sys.stdout.encoding or "utf-8", errors="replace"
    #     )
    #     print(safe, **kwargs)
        
    try:
        # Write to log file in the current working directory
        with open("placement_live_output.log", "a", encoding="utf-8") as f:
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
    """Print when not in step-only mode (full debug / legacy behavior)."""
    if not steps_only():
        _safe_print(*args, **kwargs)


def ip_step(stage: str, message: str) -> None:
    """Always printed: one line per pipeline milestone in step-only mode."""
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
        with open("placement_live_output.log", "w", encoding="utf-8") as f:
            pass # clear the log file
    except Exception:
        pass

    cfg = config or {}
    model = cfg.get("model", "?")
    devices = cfg.get("devices", "?")
    n_pmos = cfg.get("n_pmos", "?")
    n_nmos = cfg.get("n_nmos", "?")
    abutment = "On" if cfg.get("abutment") else "Off"
    sa = "On" if cfg.get("sa") else "Off"

    bar = "=" * 62
    _safe_print()
    _safe_print(f"+{bar}+")
    _safe_print(f"|  AI INITIAL PLACEMENT -- {name:<36s}|")
    _safe_print(f"+{bar}+")
    _safe_print(f"|  Model      : {model:<47s}|")
    dev_str = f"{devices}   ({n_pmos} PMOS + {n_nmos} NMOS)"
    _safe_print(f"|  Devices    : {dev_str:<47s}|")
    _safe_print(f"|  Abutment   : {abutment:<47s}|")
    _safe_print(f"|  SA Post-Opt: {sa:<47s}|")
    _safe_print(f"+{bar}+")
    _safe_print()


def pipeline_end(summary: dict | None = None) -> None:
    """Print a placement summary block with key metrics."""
    global _pipeline_start
    elapsed = time.time() - _pipeline_start if _pipeline_start else 0
    _pipeline_start = None

    s = summary or {}
    width = s.get("width", "?")
    height = s.get("height", "?")
    aspect = s.get("aspect", "?")
    area = s.get("area", "?")
    hpwl = s.get("hpwl", "--")
    drc_status = s.get("drc_status", "?")
    pmos_nmos = s.get("pmos_nmos_sep", "?")
    n_placed = s.get("n_placed", "?")

    mins = int(elapsed // 60)
    secs = int(elapsed % 60)

    bar = "=" * 62
    _safe_print()
    _safe_print(bar)
    _safe_print("  PLACEMENT SUMMARY")
    _safe_print(bar)
    if width != "?" and height != "?":
        _safe_print(f"  Layout Size  : {width}um x {height}um  (aspect {aspect})")
        _safe_print(f"  Total Area   : {area}")
    if hpwl != "--":
        _safe_print(f"  HPWL         : {hpwl}um")
    _safe_print(f"  DRC Status   : {drc_status}")
    _safe_print(f"  PMOS/NMOS    : {pmos_nmos}")
    _safe_print(f"  Devices      : {n_placed} placed")
    _safe_print(f"  Time Total   : {mins}m {secs}s")
    _safe_print(bar)
    _safe_print()


def stage_start(stage_num: int, name: str) -> float:
    """Mark the start of a pipeline stage and return the start time."""
    _stage_times[name] = time.time()
    return _stage_times[name]


def stage_end(stage_num: int, name: str, status: str, details: str = "") -> None:
    """Log stage completion with timing."""
    t0 = _stage_times.get(name, time.time())
    elapsed = time.time() - t0
    ip_step(f"{stage_num}/{_total_stages} {name}", f"{status} ({elapsed:.1f}s){' -- ' + details if details else ''}")

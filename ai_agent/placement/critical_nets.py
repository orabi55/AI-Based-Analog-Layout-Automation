"""
critical_nets.py
================
Helper utilities for the "Parasitic-Critical Nets" placement goal.

All functions are pure Python with full type hints and no Qt / LangGraph
dependencies so they can be imported safely by every layer of the pipeline.

Public API
----------
SUPPLY_NETS : frozenset[str]
    Canonical supply net names that are dropped from user selections.

PRIORITY_WEIGHTS : dict[str, int]
    Numeric weight per priority level.
    Low=0 (off), Medium=5, High=10.

get_user_critical_nets(state) -> tuple[list[str], int]
    Extract the user's critical-net selection from the pipeline state dict.
    Returns ([], 0) when the feature is off (Low priority or no nets),
    ensuring byte-identical output on the off-path.

devices_for_critical_nets(terminal_nets, nets) -> dict[str, list[str]]
    Map each requested net name to the list of device IDs connected to it.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Supply / power rail net names that are silently dropped from user selections.
#: Comparison is always case-insensitive.
SUPPLY_NETS: frozenset[str] = frozenset({
    "vdd", "vss", "gnd", "vcc", "vee",
    "avdd", "avss", "vdda", "vddh", "vddl", "vddq",
    "vbn", "vbp", "vpp", "vpwr", "vgnd",
    "gnd1", "vss_ana", "vssa", "vdde",
})

#: Numeric weight per priority level.
#: Low = 0 (feature is effectively OFF).
PRIORITY_WEIGHTS: Dict[str, int] = {
    "Low":    0,
    "Medium": 5,
    "High":   10,
}

#: Maximum number of nets the user can select via the UI.
_MAX_NETS: int = 10


# ---------------------------------------------------------------------------
# get_user_critical_nets
# ---------------------------------------------------------------------------

def get_user_critical_nets(state: dict) -> Tuple[List[str], int]:
    """Extract the validated critical-net list and weight from pipeline state.

    Reads ``state["placement_goals"]["critical_nets"]``.
    Returns ``([], 0)`` whenever the feature is logically OFF so that all
    callers can use a simple truthiness check:

    .. code-block:: python

        nets, weight = get_user_critical_nets(state)
        if not nets:
            return  # feature is off — skip entirely

    Rules applied (in order):
    1. Missing / None placement_goals → off.
    2. Missing / None critical_nets sub-key → off.
    3. priority == "Low" or unknown → weight 0 → off.
    4. Empty net list → off.
    5. Drop any supply net (case-insensitive).
    6. Deduplicate (preserve first occurrence order).
    7. Cap at _MAX_NETS (10).

    Args:
        state: LangGraph state dict (or any dict with the same schema).

    Returns:
        A 2-tuple ``(nets, weight)`` where *nets* is a list of validated net
        names and *weight* is the numeric priority weight (0 when off).
    """
    goals: dict = state.get("placement_goals") or {}
    crit_cfg: dict = goals.get("critical_nets") or {}

    priority: str = crit_cfg.get("priority", "Low")
    weight: int = PRIORITY_WEIGHTS.get(priority, 0)

    # Low priority = feature is OFF
    if weight == 0:
        return [], 0

    raw_nets: list = crit_cfg.get("nets") or []
    if not raw_nets:
        return [], 0

    # Drop supply nets, deduplicate, cap
    seen: set[str] = set()
    cleaned: List[str] = []
    for net in raw_nets:
        if not isinstance(net, str):
            continue
        net_stripped = net.strip()
        if not net_stripped:
            continue
        if net_stripped.lower() in SUPPLY_NETS:
            continue
        key = net_stripped.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(net_stripped)
        if len(cleaned) >= _MAX_NETS:
            break

    if not cleaned:
        return [], 0

    return cleaned, weight


# ---------------------------------------------------------------------------
# devices_for_critical_nets
# ---------------------------------------------------------------------------

def devices_for_critical_nets(
    terminal_nets: Dict[str, Dict[str, str]],
    nets: List[str],
) -> Dict[str, List[str]]:
    """Map each requested net to the device IDs connected to it.

    Searches the ``terminal_nets`` mapping (``{device_id: {pin: net_name}}``)
    for every pin that carries one of the requested net names.

    Comparison is case-insensitive so "VOUTP" and "voutp" match the same net.

    Args:
        terminal_nets: Mapping from device ID to pin→net dict
                       (e.g. ``{"MM1": {"D": "VOUTP", "G": "VIN", "S": "VSS"}}``).
        nets: List of validated critical net names (from get_user_critical_nets).

    Returns:
        ``{net_name: [device_id, ...]}`` for every requested net that has at
        least one connected device.  Device lists are sorted for determinism.
        Nets with no connected devices are omitted.
    """
    if not nets or not terminal_nets:
        return {}

    # Build lower-case lookup → canonical net name
    lower_to_canonical: Dict[str, str] = {n.lower(): n for n in nets}

    result: Dict[str, List[str]] = {n: [] for n in nets}

    for dev_id, pins in terminal_nets.items():
        if not isinstance(pins, dict):
            continue
        for pin_net in pins.values():
            if not isinstance(pin_net, str):
                continue
            canonical = lower_to_canonical.get(pin_net.strip().lower())
            if canonical is not None and dev_id not in result[canonical]:
                result[canonical].append(dev_id)

    # Sort device lists for deterministic context output
    return {
        net: sorted(devs)
        for net, devs in result.items()
        if devs  # omit nets with no connected devices
    }

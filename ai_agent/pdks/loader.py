"""
PDK Loader
==========
Rule lookup from a PDK configuration dict with SAED14nm heuristic fallbacks.

Usage:
    from ai_agent.pdks.loader import get_rule

    fin_pitch = get_rule(pdk, "fin_pitch_um")        # silent fallback
    tap_dist  = get_rule(pdk, "tap_max_distance_um")  # logs WARNING if heuristic
"""

import logging
from typing import Any

logger = logging.getLogger("ai_agent")

# ---------------------------------------------------------------------------
# SAED14nm heuristic defaults — used when the key is absent from the pdk dict
# ---------------------------------------------------------------------------
_SAED14_DEFAULTS: dict = {
    "fin_pitch_um":        0.014,   # FinFET fin-to-fin pitch
    "tap_max_distance_um": 2.5,     # Max substrate-tap spacing (yield-critical)
    "endcap_cell_names":   ["ENDCAP"],
    "endcap_width_um":     0.294,   # matches PITCH_UM (non-abutted device pitch)
    "tap_cell_nmos":       "Ptap",  # p-substrate tap used in NMOS rows
    "tap_cell_pmos":       "Ntap",  # n-well tap used in PMOS rows
    "tap_width_um":        0.294,
    "tap_height_um":       0.200,
}

# These keys are yield-limiting constraints — always warn when falling back
_YIELD_CRITICAL: frozenset = frozenset({"tap_max_distance_um"})


def get_rule(pdk: dict, rule_name: str) -> Any:
    """Return a design rule value from the PDK dict.

    Search order:
      1. pdk[rule_name]            — top-level key
      2. pdk[sub][rule_name]       — any nested dict (e.g. pdk["drc_rules"])
      3. SAED14 heuristic fallback — logged at WARNING for yield-critical keys

    Args:
        pdk:       PDK configuration dict (may be None or empty).
        rule_name: Rule name to look up.

    Returns:
        The rule value (any type) or None if not found anywhere.
    """
    pdk = pdk or {}

    # 1. Direct top-level lookup
    if rule_name in pdk:
        return pdk[rule_name]

    # 2. Nested sub-dict lookup (handles {"drc_rules": {...}, "cell_lib": {...}})
    for subval in pdk.values():
        if isinstance(subval, dict) and rule_name in subval:
            return subval[rule_name]

    # 3. Heuristic fallback
    if rule_name in _SAED14_DEFAULTS:
        value = _SAED14_DEFAULTS[rule_name]
        if rule_name in _YIELD_CRITICAL:
            logger.warning(
                "[PDK] '%s' not found in PDK dict — using SAED14 heuristic %r. "
                "Verify against your PDK's actual design rules.",
                rule_name,
                value,
            )
        else:
            logger.debug("[PDK] '%s' not in PDK dict — using SAED14 default %r.", rule_name, value)
        return value

    return None


def load_pdk(name: str = "saed14nm") -> dict:
    """Return a PDK configuration dict for the named process.

    Currently only 'saed14nm' (and aliases) is supported.
    For all other names a warning is logged and an empty dict is returned —
    which causes every subsequent get_rule() call to use SAED14 heuristics.
    """
    normalised = name.lower().replace("-", "").replace("_", "")
    if normalised == "saed14nm":
        return dict(_SAED14_DEFAULTS)
    logger.warning(
        "[PDK] Unknown PDK name %r — returning empty dict. "
        "get_rule() will fall back to SAED14 heuristics.", name
    )
    return {}

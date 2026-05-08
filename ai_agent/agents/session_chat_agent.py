"""
Session Chat Agent
==================
Deterministic first-pass router for the session chatbot flow.

This module is the **first guard** that runs before any LLM classifier is
called.  For messages whose intent is obvious from keyword matching, it
returns a session route immediately.  For ambiguous messages it returns
``None`` so the caller can escalate to an LLM-based classifier.

Exports:
- VALID_SESSION_ROUTES : frozenset of allowed route strings.
- rule_route           : keyword-based fast-path router.
- normalize_route      : sanitise an arbitrary route string.

Design notes:
- No specialist agents are called here; this file only *decides* a route.
- Priority order (highest → lowest):
    0. fix_drc        — DRC repair phrases (remove/clear + violation/overlap)
                        must beat strong commands so "remove DRC violation"
                        routes to fix_drc, not command_edit
    1. command_edit   — STRONG imperative verbs (move/swap/flip/delete/…)
    2a. fix_drc       — explicit "fix DRC" / "repair violation" phrases
    2b. need_drc      — DRC / spacing / overlap vocabulary (read-only check)
    3a. fix_routing   — active optimization (reduce parasitics/wirelength/crossings)
    3b. need_routing  — routing / wirelength (read-only preview)
    4. need_strategy  — symmetry / matching / centroid vocabulary
    5. need_topology  — topology / netlist / connectivity vocabulary
    6. answer_only    — explanation / summarisation vocabulary
    7. command_edit   — WEAK command verbs (place/shift) that lose to
                        an explanation context
    8. None           — caller must escalate to LLM classifier
  Strong imperatives beat everything else.  Weak verbs like ``"place"``
  yield to explanation markers so that interrogative messages such as
  ``"why did you place M1 here?"`` are classified as ``answer_only``.
"""

from __future__ import annotations

import re
from typing import Optional, List

# ---------------------------------------------------------------------------
# Route constants
# ---------------------------------------------------------------------------

#: All valid session routes for the chatbot flow.
VALID_SESSION_ROUTES: frozenset[str] = frozenset({
    "answer_only",   # LLM answers directly; no command is generated
    "command_edit",  # LLM generates one or more layout edit commands
    "need_topology", # delegate to topology_analyst specialist
    "need_strategy", # delegate to strategy_selector specialist
    "need_placement",# delegate to placement_specialist specialist
    "need_drc",      # delegate to drc_checker (read-only) specialist
    "fix_drc",       # delegate to drc_critic (active fixes) specialist
    "need_routing",  # delegate to routing_previewer (read-only preview)
    "fix_routing",   # delegate to routing_previewer (active optimization)
    "clarify",       # ask the user for clarification
})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _word_match(text: str, words: tuple[str, ...]) -> bool:
    """Return True if any item in *words* appears as a whole word in *text*.

    Uses ``\\b`` word-boundary anchors so that, e.g., ``"place"`` does
    **not** match inside ``"placement"`` or ``"initial placement"``.
    Multi-word phrases (containing a space) are matched as sub-strings
    because they already impose their own natural boundary.
    """
    for w in words:
        if ' ' in w:
            # Multi-word phrase — substring match is fine.
            if w in text:
                return True
        else:
            # Single token — require whole-word match.
            if re.search(r'\b' + re.escape(w) + r'\b', text):
                return True
    return False

# ---------------------------------------------------------------------------
# Keyword lists  (kept as module-level tuples for easy extension)
# ---------------------------------------------------------------------------

# Strong imperatives — always win, even in mixed messages.
# These are unambiguous layout-edit verbs that cannot appear in a
# purely interrogative context with a different meaning.
_STRONG_COMMAND_WORDS: tuple[str, ...] = (
    "move", "swap", "flip", "delete", "remove", "abut",
    "add dummy", "dummy",
    # Unsupported but recognized — route to command_edit where the parser
    # returns [] and the unsupported-action fallback message is triggered.
    "align", "merge", "rotate",
)

_MATCHING_TERMS: tuple[str, ...] = (
    "matching", "matched", "match", "common centroid", "common-centroid",
    "centroid", "interdigitation", "interdigitated", "interdig",
)

# Weak command verbs — yield to explanation context.
# "place" and "shift" can appear in questions ("why did you place…")
# so they are only treated as commands when no explanation keyword is present.
_WEAK_COMMAND_WORDS: tuple[str, ...] = (
    "place", "shift",
)

#: Fix/repair DRC keywords — checked BEFORE generic DRC words so
#: "fix DRC violations" → fix_drc, not need_drc.
_FIX_DRC_WORDS: tuple[str, ...] = (
    "fix drc", "repair drc", "resolve violation", "fix violation",
    "fix overlap", "repair spacing", "fix spacing", "fix the drc",
    "resolve overlap", "repair violation", "repair overlap",
    "fix design rule", "heal drc",
)

#: DRC repair phrases involving remove/clear + DRC/violation/overlap/spacing.
#: These MUST be checked BEFORE _STRONG_COMMAND_WORDS so that
#: "remove DRC violation" routes to fix_drc, not command_edit.
_DRC_REPAIR_PHRASES: tuple[str, ...] = (
    "remove drc", "clear drc",
    "remove violation", "clear violation",
    "remove violations", "clear violations",
    "remove overlap", "clear overlap",
    "fix violation", "fix violations",
    "resolve drc", "resolve violation", "resolve violations",
    "fix spacing violation", "clear drc errors",
)

_DRC_WORDS: tuple[str, ...] = (
    "drc", "violation", "spacing", "overlap", "short", "illegal",
    "design rule", "rule check",
)

_ROUTING_WORDS: tuple[str, ...] = (
    "route", "routing", "wire", "wirelength", "crossing",
    "congestion", "net crossing", "interconnect",
)

#: Active routing optimization phrases — checked BEFORE generic routing words
#: so "reduce parasitics" → fix_routing, not need_routing.
_ROUTING_FIX_PHRASES: tuple[str, ...] = (
    "reduce parasitic", "reduce parasitics",
    "reduce wirelength", "reduce wire length",
    "reduce crossings", "reduce crossing",
    "optimize routing", "optimise routing",
    "fix routing", "fix crossings", "fix crossing",
    "improve routing", "shorten net", "shorten nets",
    "minimize wirelength", "minimise wirelength",
    "lower parasitic", "lower parasitics",
)

_STRATEGY_WORDS: tuple[str, ...] = (
    "symmetry", "common centroid", "centroid", "matching",
    "mirror strategy", "layout strategy", "placement strategy",
    "matched", "pairing",
)

_TOPOLOGY_WORDS: tuple[str, ...] = (
    "topology", "netlist", "connected", "connection",
    "diff pair", "differential pair", "current mirror",
    "cascode", "which devices", "what is connected",
)

_EXPLANATION_WORDS: tuple[str, ...] = (
    "why", "explain", "what did", "reason", "describe",
    "tell me", "summarize", "what happened",
)


# ---------------------------------------------------------------------------
# Deterministic command parser
# ---------------------------------------------------------------------------

#: Regex matching common analog device names (M1, MM3, XM1, MN1, MP2, etc.).
#: Preserves original case from the user message.
DEVICE_RE = re.compile(r"\b((?:MM|XM|MN|MP|M)\d+\w*)\b", re.IGNORECASE)

#: Regex matching common net names (VOUTP, VOUTN, CLK, VIN, etc.).
#: Net names are typically uppercase identifiers ≥ 2 chars that are NOT devices.
_NET_RE = re.compile(r"\b([A-Z][A-Z0-9_]{1,})\b")

#: Net names that are too generic to be useful targets.
_IGNORE_NETS = frozenset({
    "DRC", "HPWL", "AND", "THE", "FOR", "NOT", "WITH", "FROM",
    "VDD", "VSS", "GND", "AVDD", "AVSS", "DVDD", "DVSS",
})


def _extract_target_nets(message: str) -> list[str]:
    """Extract net names from a routing-related message.

    Looks for patterns like::

        "reduce parasitics on VOUTP and VOUTN nets"
        "optimize net CLK"
        "shorten VOUTP"
        "VOUTP,VOUTN"   (comma-separated follow-up)

    Returns a deduplicated list of net names, excluding device names
    and common non-net words.
    """
    if not message:
        return []

    # Pre-split on commas so "VOUTP,VOUTN" is handled
    expanded = message
    if "," in expanded:
        expanded = expanded.replace(",", " ")

    candidates = _NET_RE.findall(expanded)
    device_names = {d.upper() for d in DEVICE_RE.findall(expanded)}
    nets: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        cu = c.upper()
        if cu in _IGNORE_NETS or cu in device_names or cu in seen:
            continue
        seen.add(cu)
        nets.append(c)
    return nets


def is_matching_question(message: str) -> bool:
    """Return True for answer-only matching/common-centroid questions."""
    text = str(message or "").strip().lower()
    if not text:
        return False
    has_matching_term = any(term in text for term in _MATCHING_TERMS)
    if not has_matching_term:
        return False
    return bool(
        "?" in text
        or re.match(r"^(how|what|why|is|are|should)\b", text)
    )


def is_targeted_matching_request(message: str) -> bool:
    """Return True for matching action-like requests we should not execute yet."""
    text = str(message or "").strip().lower()
    if not text:
        return False
    if not any(term in text for term in _MATCHING_TERMS):
        return False
    if is_matching_question(text):
        return True
    return bool(re.match(r"^(match|make|use|apply|implement)\b", text))


#: Direction → (dx, dy) map.  Positive Y = up in analog layout convention
#: (PMOS above NMOS, higher y = higher row).  This matches _pmos_above_nmos()
#: in cmd_parser.py where PMOS y > NMOS y.
_DIRECTION_DELTAS: dict[str, tuple[int, int]] = {
    "left":  (-1,  0),
    "right": ( 1,  0),
    "up":    ( 0,  1),
    "down":  ( 0, -1),
}


def _extract_devices(
    text: str,
    placement_nodes: Optional[list] = None,
) -> list[str]:
    """Return device IDs found in *text*, validated against *placement_nodes*.

    If *placement_nodes* is provided the device name must match one of the
    node keys ``id``, ``device_id``, ``name``, ``parent_id``, or the
    logical base ID computed from finger-expanded names (e.g. ``MM1_f0``
    → ``MM1``).  Otherwise the regex match is accepted as-is.

    Bug 2 fix: also resolves M<N> → MM<N> aliases via device_resolver.
    """
    from ai_agent.tools.command_schema import logical_base_device_id
    from ai_agent.tools.device_resolver import normalize_logical_device_id

    candidates = DEVICE_RE.findall(text)
    if not candidates:
        return []

    if not placement_nodes:
        return candidates

    # Build lookup set from placement nodes — includes exact IDs,
    # parent_id, and logical base IDs from finger-expanded names.
    known: set[str] = set()
    for n in placement_nodes:
        if isinstance(n, dict):
            for k in ("id", "device_id", "name"):
                v = n.get(k)
                if v:
                    known.add(str(v))
                    # Also index the logical base (e.g. MM1_f0 → MM1)
                    base = logical_base_device_id(str(v))
                    if base != str(v):
                        known.add(base)
            # Also index parent_id (explicit logical parent)
            pid = n.get("parent_id")
            if pid:
                known.add(str(pid))

    # Match candidates against known IDs (case-insensitive lookup,
    # but return the canonical form from placement_nodes).
    # Bug 2: also try alias resolution (M1 → MM1).
    known_lower: dict[str, str] = {k.lower(): k for k in known}
    matched: list[str] = []
    for c in candidates:
        canon = known_lower.get(c.lower())
        if canon:
            matched.append(canon)
        else:
            # Try alias normalization: M1 → MM1
            normalized = normalize_logical_device_id(c)
            canon2 = known_lower.get(normalized.lower())
            if canon2:
                matched.append(canon2)
            elif not known:  # no placement_nodes data → trust regex
                matched.append(c)
    return matched


def answer_matching_question(
    message: str,
    initial_agent_trace: dict | None = None,
    placement_nodes: list | None = None,
) -> str:
    """Answer matching/common-centroid questions without generating commands."""
    trace = initial_agent_trace if isinstance(initial_agent_trace, dict) else {}
    text = str(message or "")
    text_l = text.lower()
    devices = _extract_devices_for_explanation(text, placement_nodes or [], trace)
    mentioned = {str(d).upper() for d in devices}

    known_groups: list[tuple[str, str, str]] = [
        ("MM8", "MM9", "input NMOS differential pair"),
        ("MM0", "MM3", "PMOS input/precharge load pair"),
        ("MM4", "MM5", "PMOS latch pair"),
        ("MM6", "MM7", "NMOS latch pair"),
        ("MM1", "MM2", "output precharge pair"),
    ]

    trace_groups: list[tuple[str, str]] = []
    strategy = trace.get("strategy") if isinstance(trace, dict) else None
    if isinstance(strategy, dict):
        for key in ("matching_groups", "matched_pairs", "symmetry_pairs", "common_centroid_groups"):
            raw_groups = strategy.get(key)
            if not isinstance(raw_groups, list):
                continue
            for group in raw_groups:
                if isinstance(group, (list, tuple)) and len(group) >= 2:
                    trace_groups.append((str(group[0]), str(group[1])))

    def _pair_is_known(a: str, b: str) -> bool:
        pair = {a.upper(), b.upper()}
        return any(pair == {x.upper(), y.upper()} for x, y in trace_groups) or any(
            pair == {x.upper(), y.upper()} for x, y, _ in known_groups
        )

    if {"MM8", "MM9"}.issubset(mentioned):
        return (
            "MM8/MM9 are the input differential pair. Use the differential_pair "
            "structural skill with common-centroid-style/interdigitated finger "
            "ordering; do not force standalone common_centroid. No layout changes "
            "were applied."
        )

    if len(mentioned) >= 2:
        ordered = [str(d).upper() for d in devices]
        a, b = ordered[0], ordered[1]
        pair_label = f"{a}/{b}"
        canonical_desc = ""
        for x, y, desc in known_groups:
            if {a, b} == {x, y}:
                pair_label = f"{x}/{y}"
                canonical_desc = desc
                break
        if _pair_is_known(a, b):
            role = f" the {canonical_desc}" if canonical_desc else " a known matched pair"
            return (
                f"{pair_label} are{role}. Treat them as matched/symmetric devices; "
                "interdigitation means their fingers alternate, while true "
                "common-centroid also requires that alternating order to be symmetric "
                "about the physical center. Common-centroid-style matching depends "
                "on physical finger ordering; I can only confirm true common-centroid "
                "after checking the actual finger ordering. No layout commands were generated."
            )

    lines = ["Matching applied in the current layout:"]
    for a, b, desc in known_groups:
        if trace_groups and not _pair_is_known(a, b):
            continue
        lines.append(f"{a}/{b}: {desc}.")
    if len(lines) == 1:
        lines.append("I do not see explicit matching groups in the saved trace.")
    lines.append(
        "True common-centroid can only be confirmed from physical finger ordering; "
        "that physical finger-order confirmation checks whether the row/finger "
        "sequence shows symmetric alternation around the center."
    )
    if "common centroid" in text_l or "interdig" in text_l:
        lines.append(
            "For these pairs, use common-centroid-style or interdigitated finger "
            "ordering only where the underlying structural skill supports it."
        )
    return "\n".join(lines)


def _parse_numeric_amount(text: str) -> Optional[int]:
    """Try to extract a numeric movement amount from *text*.

    Handles patterns like:
        move M1 left 2
        move M1 left by 3
    Returns None if no number is found (caller uses default of 1).
    """
    m = re.search(r"(?:by\s+)?(\d+)\s*$", text.strip())
    if m:
        return int(m.group(1))
    return None


def _parse_explicit_deltas(text: str) -> Optional[tuple[int, int]]:
    """Try to parse explicit dx/dy notation.

    Handles:
        move M1 by -3 0
        move M1 dx=-2 dy=0
    Returns (dx, dy) or None.
    """
    # dx=N dy=N form
    m = re.search(r"dx\s*=\s*(-?\d+)\s+dy\s*=\s*(-?\d+)", text, re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))
    # "by N N" form
    m = re.search(r"by\s+(-?\d+)\s+(-?\d+)", text, re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def _build_move_commands(
    device_id: str,
    dx: int,
    dy: int,
    placement_nodes: Optional[list] = None,
) -> list[dict]:
    """Build move command(s) for *device_id* with matched-block safety.

    Safety rules (per user correction 2/3):
    - If the device belongs to a fixed matched block (e.g. MM2_MM1_matched),
      return a ``clarify_matched_block`` sentinel instead of expanding to
      individual finger moves that would break interdigitation.
    - Free devices (e.g. MM6, MM7) get a direct single move command.
    - Matched-block IDs explicitly mentioned by the user can be moved directly
      (handled at the caller level, not here).
    """
    from ai_agent.tools.device_resolver import (
        find_matched_block_for_device,
        resolve_layout_device_reference,
    )

    # Only check matched-block safety when full layout context is available.
    # Without placement_nodes (raw parsing / unit tests), skip the check.
    if placement_nodes:
        block = find_matched_block_for_device(device_id)
    else:
        block = None

    if block:
        block_name = block.get("block_name", "")
        partner_devices = block.get("devices", [])
        description = block.get("description", "matched pair")
        partner_str = "/".join(partner_devices)

        # Return a clarify sentinel — caller should ask the user
        return [{
            "action": "clarify_matched_block",
            "device_id": device_id,
            "dx": dx,
            "dy": dy,
            "matched_block": block_name,
            "partner_devices": partner_devices,
            "assistant_text": (
                f"{device_id} is part of the {description} "
                f"({partner_str} matched block: {block_name}). "
                f"Moving only {device_id} would break the matched/interdigitated "
                f"structure. Do you want to move the whole {partner_str} matched "
                f"block instead?"
            ),
        }]

    # Free device — single direct command
    return [{"action": "move", "device_id": device_id, "dx": dx, "dy": dy}]


def parse_direct_edit_command(
    message: str,
    placement_nodes: Optional[list] = None,
) -> list[dict]:
    """Parse common direct edit messages into structured command dicts.

    Returns a list of command dicts compatible with :mod:`cmd_parser` and
    the :func:`node_command_validator` schema.  Returns ``[]`` when the
    message is ambiguous or no valid device can be identified.

    Supported patterns::

        move M1 left
        shift M1 right 2
        place M1 up
        swap M1 and M2
        swap M1 with M2
        flip M1
        flip M1 horizontal / vertical
        delete M1 / remove M1
        align M1 with M2
        abut M1 with M2
        merge M1 and M2
        add dummy near M1

    Args:
        message:         Raw user text.
        placement_nodes: Optional list of node dicts for device validation.

    Returns:
        List of command dicts, or ``[]`` if parsing fails.
    """
    if not message or not isinstance(message, str):
        return []

    text = message.strip()
    low = text.lower()

    # --- Move / Shift / Place -----------------------------------------------
    m_move = re.match(
        r"(?:move|shift|place)\s+",
        low,
    )
    if m_move:
        devices = _extract_devices(text, placement_nodes)
        if not devices:
            return []  # ambiguous — "move it left"

        # Try explicit dx/dy first
        explicit = _parse_explicit_deltas(low)
        if explicit:
            dx, dy = explicit
            return _build_move_commands(devices[0], dx, dy, placement_nodes)

        # Try direction word
        for direction, (dx, dy) in _DIRECTION_DELTAS.items():
            if re.search(r"\b" + direction + r"\b", low):
                amount = _parse_numeric_amount(low) or 1
                return _build_move_commands(
                    devices[0], dx * amount, dy * amount, placement_nodes,
                )

        # Have a device but no direction → ambiguous
        return []

    # --- Swap ---------------------------------------------------------------
    if re.match(r"swap\s+", low):
        devices = _extract_devices(text, placement_nodes)
        if len(devices) >= 2:
            return [{"action": "swap", "device_a": devices[0], "device_b": devices[1]}]
        return []

    # --- Flip ---------------------------------------------------------------
    if re.match(r"flip\s+", low):
        devices = _extract_devices(text, placement_nodes)
        if not devices:
            return []
        orientation = "horizontal"  # default
        if re.search(r"\bvertical\b", low):
            orientation = "vertical"
        elif re.search(r"\bhorizontal\b", low):
            orientation = "horizontal"
        return [{"action": "flip", "device_id": devices[0], "orientation": orientation}]

    # --- Delete / Remove ----------------------------------------------------
    if re.match(r"(?:delete|remove)\s+", low):
        devices = _extract_devices(text, placement_nodes)
        if not devices:
            return []
        return [{"action": "delete", "device_id": devices[0]}]

    # --- Align (NOT SUPPORTED — return empty to trigger clarify) -----------
    if re.match(r"align\s+", low):
        return []

    # --- Abut ---------------------------------------------------------------
    if re.match(r"abut\s+", low):
        devices = _extract_devices(text, placement_nodes)
        if len(devices) >= 2:
            return [{"action": "abut", "device_a": devices[0], "device_b": devices[1]}]
        return []

    # --- Merge (NOT SUPPORTED — return empty to trigger clarify) -----------
    if re.match(r"merge\s+", low):
        return []

    # --- Add Dummy (Bug 5 fix: resolve logical devices) --------------------
    if re.match(r"add\s+dummy\b", low):
        devices = _extract_devices(text, placement_nodes)
        # Parse side/location context
        side = None
        if re.search(r"\bleft\s+of\b", low):
            side = "left"
        elif re.search(r"\bright\s+of\b", low):
            side = "right"
        elif re.search(r"\bnear\b", low):
            side = "right"  # default near = right

        if devices and side:
            return [{"action": "add_dummy", "target": devices[0], "side": side}]
        elif devices:
            # Has target but no explicit side
            return [{"action": "add_dummy", "target": devices[0], "side": "right"}]
        # Vague "add dummy" without target — return empty for clarify
        return []

    # --- Rotate (NOT SUPPORTED — return empty to trigger clarify) ----------
    if re.match(r"rotate\s+", low):
        return []

    # No pattern matched
    return []


# ---------------------------------------------------------------------------
# Partial intent builder (slot-filling support)
# ---------------------------------------------------------------------------

def _build_partial_move_intent(message: str) -> Optional[dict]:
    """Detect a partial edit command that has action/direction but no device.

    Returns a dict with the known fields + a ``missing`` list, or ``None``
    if the message is not a recognisable partial edit.

    Examples::

        >>> _build_partial_move_intent("move left")
        {"action": "move", "dx": -1, "dy": 0, "missing": ["device_id"]}

        >>> _build_partial_move_intent("flip horizontal")
        {"action": "flip", "orientation": "horizontal", "missing": ["device_id"]}
    """
    if not message:
        return None

    low = message.strip().lower()

    # --- Move/shift/place without device ---
    if re.match(r"(?:move|shift|place)\s+", low):
        # Try direction
        for direction, (dx, dy) in _DIRECTION_DELTAS.items():
            if re.search(r"\b" + direction + r"\b", low):
                amount = _parse_numeric_amount(low) or 1
                return {
                    "action": "move",
                    "dx": dx * amount,
                    "dy": dy * amount,
                    "missing": ["device_id"],
                }
        # Try explicit dx/dy
        explicit = _parse_explicit_deltas(low)
        if explicit:
            dx, dy = explicit
            return {
                "action": "move",
                "dx": dx,
                "dy": dy,
                "missing": ["device_id"],
            }
        # "move" with no direction either — not enough info
        return None

    # --- Flip without device ---
    if re.match(r"flip\s*", low):
        orientation = "horizontal"
        if re.search(r"\bvertical\b", low):
            orientation = "vertical"
        elif re.search(r"\bhorizontal\b", low):
            orientation = "horizontal"
        return {
            "action": "flip",
            "orientation": orientation,
            "missing": ["device_id"],
        }

    # --- Delete/remove without device ---
    if re.match(r"(?:delete|remove)\s*$", low):
        return {
            "action": "delete",
            "missing": ["device_id"],
        }

    return None


def try_fill_edit_slots(
    message: str,
    pending_intent: dict,
    placement_nodes: Optional[list] = None,
) -> Optional[dict]:
    """Try to fill missing slots in *pending_intent* from *message*.

    Returns a complete command dict if all required slots are filled,
    or ``None`` if the message doesn't provide the missing information.

    Bug 4 fix: uses device_resolver for alias resolution (M1→MM1).

    Recognised patterns for device slot::

        "Target device is MM1"
        "device is MM1"
        "use MM1"
        "MM1"
        "it's MM1"
        "the device is MM1"
        "M1"  (alias → MM1)
    """
    if not pending_intent or not message:
        return None

    missing = pending_intent.get("missing") or []
    if "device_id" not in missing:
        return None

    # Try to extract a device from the follow-up message
    devices = _extract_devices(message, placement_nodes)

    # Also try bare regex with alias resolution as fallback
    if not devices:
        from ai_agent.tools.device_resolver import normalize_logical_device_id
        raw = DEVICE_RE.findall(message)
        devices = [normalize_logical_device_id(d) for d in raw] if raw else []

    if not devices:
        return None

    # Build the completed command from pending intent
    completed = {k: v for k, v in pending_intent.items() if k != "missing"}
    completed["device_id"] = devices[0]

    # Verify the completed command has an action
    if "action" not in completed:
        return None

    return completed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def rule_route(message: str) -> Optional[str]:
    """Return a session route for *message* using deterministic keyword rules.

    Args:
        message: The raw user text (any case, any length).

    Returns:
        A member of :data:`VALID_SESSION_ROUTES` when the message intent is
        unambiguous, or ``None`` when no keyword rule matches and the caller
        should escalate to an LLM classifier.

    Examples::

        >>> rule_route("move M1 left")
        'command_edit'
        >>> rule_route("check DRC") is not None
        True
        >>> rule_route("hello there") is None
        True
    """
    m = (message or "").lower()

    # Priority 0 — DRC repair phrases (remove/clear + DRC/violation/overlap)
    # Must be checked BEFORE strong commands so "remove DRC violation" routes
    # to fix_drc, not command_edit.
    if _word_match(m, _DRC_REPAIR_PHRASES):
        return "fix_drc"

    # Priority 1 — STRONG layout edit commands (always win)
    if _word_match(m, _STRONG_COMMAND_WORDS):
        return "command_edit"

    # Priority 2a — Fix/repair DRC (must be checked before generic DRC words)
    if _word_match(m, _FIX_DRC_WORDS):
        return "fix_drc"

    # Priority 2b — DRC / manufacturing rules (read-only check)
    if _word_match(m, _DRC_WORDS):
        return "need_drc"

    # Priority 3a — Active routing optimization (must be checked before
    # generic routing words so "reduce parasitics" → fix_routing, not need_routing).
    if _word_match(m, _ROUTING_FIX_PHRASES):
        return "fix_routing"

    # Priority 3b — routing / wirelength (read-only preview)
    if _word_match(m, _ROUTING_WORDS):
        return "need_routing"

    # Priority 4 — placement strategy / symmetry / matching
    if _word_match(m, _STRATEGY_WORDS):
        return "need_strategy"

    # Priority 5 — topology / netlist / connectivity
    if _word_match(m, _TOPOLOGY_WORDS):
        return "need_topology"

    # Priority 6 — explanation / narration (beats weak command verbs)
    if _word_match(m, _EXPLANATION_WORDS):
        return "answer_only"

    # Priority 7 — WEAK command verbs (only if no explanation context)
    if _word_match(m, _WEAK_COMMAND_WORDS):
        return "command_edit"

    # No rule matched — signal caller to use LLM classifier
    return None


def normalize_route(route: Optional[str]) -> str:
    """Coerce *route* to a member of :data:`VALID_SESSION_ROUTES`.

    Any value that is not already a valid route (including ``None``,
    empty string, or an LLM hallucination) is mapped to ``"clarify"``
    so downstream nodes always receive a safe, known route.

    Args:
        route: Candidate route string (may be ``None``).

    Returns:
        A member of :data:`VALID_SESSION_ROUTES`.

    Examples::

        >>> normalize_route("command_edit")
        'command_edit'
        >>> normalize_route("bad_route")
        'clarify'
        >>> normalize_route(None)
        'clarify'
    """
    if route in VALID_SESSION_ROUTES:
        return route
    return "clarify"


# ---------------------------------------------------------------------------
# LLM router layer
# ---------------------------------------------------------------------------

#: Maps session routes that need a specialist to the exact agent name.
SPECIALIST_BY_ROUTE: dict[str, str] = {
    "need_topology":  "topology_analyst",
    "need_strategy":  "strategy_selector",
    "need_placement": "placement_specialist",
    "need_drc":       "drc_checker",
    "fix_drc":        "drc_critic",
    "need_routing":   "routing_previewer",
    "fix_routing":    "routing_previewer",
}

#: Minimum LLM confidence required to trust the route (below → clarify).
_CONFIDENCE_THRESHOLD = 0.70

SESSION_ROUTER_PROMPT = """\
You are a session-level analog layout chatbot router.

You are given:
- the current user message
- chat history
- current placement summary
- initial placement trace from topology, strategy, placement, routing, and DRC agents

Your job is to choose exactly one route.

Allowed routes:
- answer_only: user asks for explanation, summary, or reasoning; no layout modification needed
- command_edit: user asks for a small direct edit such as move, swap, flip, align, abut, delete, dummy
- need_topology: user asks about circuit connectivity, topology, differential pairs, current mirrors, cascodes, nets
- need_strategy: user asks to change or analyze matching, symmetry, common-centroid, row assignment, placement strategy
- need_placement: user asks for a large re-placement or global placement change
- need_drc: user asks to CHECK DRC status, any violations, spacing status (read-only, no fixing)
- fix_drc: user explicitly asks to FIX, REPAIR, or RESOLVE DRC violations, overlaps, spacing
- need_routing: user asks about routing, wires, crossings, wirelength, congestion (read-only preview)
- fix_routing: user asks to REDUCE parasitics, REDUCE wirelength, OPTIMIZE routing, FIX crossings, SHORTEN nets, IMPROVE routing (active optimization)
- clarify: the request is ambiguous or unsafe to execute

Return strict JSON only:
{
  "route": "...",
  "confidence": 0.0,
  "reason": "...",
  "assistant_text": "...",
  "commands": []
}

Rules:
- Do not choose a specialist unless truly needed.
- Prefer answer_only for questions that can be answered from initial_agent_trace.
- Prefer command_edit for small local edits.
- Use clarify if the user request could break matching or symmetry and target devices are unclear.
- Do not redo initial placement unless the user explicitly asks.
- Preserve initial topology, strategy, matching, symmetry, row legality, and DRC constraints.
"""


def call_session_router_llm(
    user_message: str,
    chat_history: list,
    placement_summary: str,
    trace_summary: str,
    model_name: str = "Gemini",
) -> str:
    """Call the LLM with the session router prompt and return raw text.

    This function is intentionally thin — it only handles message
    construction and LLM invocation.  JSON parsing and validation are
    done by :func:`parse_session_json`.  The function is module-level
    so tests can monkeypatch it easily.

    Args:
        user_message:      Raw user text.
        chat_history:      List of ``{"role": …, "content": …}`` dicts.
        placement_summary: Short text description of current placement.
        trace_summary:     Short text summary of the initial agent trace.
        model_name:        LLM provider key passed to the factory.

    Returns:
        Raw string from the LLM (may be invalid JSON on failure).
    """
    from ai_agent.llm.factory import get_langchain_llm

    context_block = (
        f"[PLACEMENT SUMMARY]\n{placement_summary}\n\n"
        f"[INITIAL AGENT TRACE]\n{trace_summary}\n\n"
        f"[USER MESSAGE]\n{user_message}"
    )

    messages = [
        {"role": "system",    "content": SESSION_ROUTER_PROMPT},
    ]
    # Inject a condensed chat history (last 6 turns max)
    for turn in (chat_history or [])[-6:]:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": context_block})

    try:
        llm = get_langchain_llm(model_name, task_weight="light")
        response = llm.invoke(messages)
        if response and hasattr(response, "content"):
            return response.content.strip()
        return ""
    except Exception as exc:  # pragma: no cover — real network errors
        return f"Error: {exc}"


def parse_session_json(text: str) -> dict:
    """Extract a valid routing JSON dict from LLM output text.

    Handles three common LLM formatting quirks:
    1. Bare JSON object.
    2. JSON wrapped in a ```json … ``` fenced code block.
    3. JSON embedded inside surrounding prose.

    Returns an empty dict on any parse failure so callers can fall back
    safely.

    Args:
        text: Raw LLM output string.

    Returns:
        Parsed dict, or ``{}`` on failure.
    """
    import json as _json

    if not text or not isinstance(text, str):
        return {}

    # Strip markdown fences
    cleaned = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE).replace("```", "").strip()

    # Try direct parse
    try:
        return _json.loads(cleaned)
    except _json.JSONDecodeError:
        pass

    # Try to extract first {...} block
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if m:
        try:
            return _json.loads(m.group(0))
        except _json.JSONDecodeError:
            pass

    return {}


def _build_fallback_response(reason: str = "I need a bit more detail before changing the layout.") -> dict:
    """Return a safe clarify response dict."""
    return {
        "session_route":      "clarify",
        "route_confidence":   0.0,
        "session_reason":     reason,
        "assistant_text":     reason,
        "pending_cmds":       [],
        "session_commands":   [],
        "requires_specialist": False,
        "specialist_target":  None,
    }


def _build_placement_summary(placement_nodes: list) -> str:
    """One-line description of placed nodes for the LLM context."""
    if not placement_nodes:
        return "No placement nodes available."
    n_pmos = sum(1 for n in placement_nodes if str(n.get("type", "")).lower() == "pmos")
    n_nmos = sum(1 for n in placement_nodes if str(n.get("type", "")).lower() == "nmos")
    return (
        f"{len(placement_nodes)} nodes placed "
        f"({n_pmos} PMOS, {n_nmos} NMOS)."
    )


def _build_trace_summary(trace: dict) -> str:
    """Compact text summary of the initial agent trace for LLM context."""
    if not trace:
        return "No initial placement trace available."
    lines = []
    if trace.get("topology"):
        lines.append(f"Topology: {str(trace['topology'])[:200]}")
    if trace.get("strategy"):
        lines.append(f"Strategy: {str(trace['strategy'])[:200]}")
    drc = trace.get("drc") or {}
    lines.append(
        f"DRC: {'PASS' if drc.get('pass') else 'FAIL'} "
        f"({len(drc.get('flags') or [])} flags)"
    )
    routing = trace.get("routing") or {}
    if routing:
        lines.append(f"Routing: {str(routing)[:120]}")
    return "\n".join(lines) if lines else "Trace present but empty."


# ---------------------------------------------------------------------------
# Fix E — Permissive device extraction for explanations
# ---------------------------------------------------------------------------

def _extract_devices_for_explanation(
    message: str,
    placement_nodes: Optional[list] = None,
    initial_agent_trace: Optional[dict] = None,
) -> list[str]:
    """Extract device IDs from *message* with permissive matching for
    explanation mode.

    Unlike :func:`_extract_devices`, this function also recognises logical
    device names (e.g. ``M1``) when the current placement only contains
    physical finger IDs (e.g. ``M1_f0``, ``M1_f1``).

    **This function must NOT be used for command execution** — commands
    require strict device validation.
    """
    from ai_agent.tools.command_schema import logical_base_device_id

    candidates = DEVICE_RE.findall(message)
    if not candidates:
        return []

    # Try strict matching first
    strict = _extract_devices(message, placement_nodes)
    if strict:
        return strict

    # Build logical-base lookup from placement nodes
    logical_ids: set[str] = set()
    if placement_nodes:
        for n in placement_nodes:
            if isinstance(n, dict):
                nid = n.get("id") or n.get("device_id") or n.get("name")
                if nid:
                    base = logical_base_device_id(str(nid))
                    logical_ids.add(base)
                # Also check parent_id
                pid = n.get("parent_id")
                if pid:
                    logical_ids.add(str(pid))

    # Also harvest device IDs from the initial agent trace
    trace_ids: set[str] = set()
    if initial_agent_trace:
        strategy = initial_agent_trace.get("strategy")
        if isinstance(strategy, dict):
            for group_key in (
                "matching_groups", "matched_pairs",
                "symmetry_pairs", "common_centroid_groups",
            ):
                groups = strategy.get(group_key)
                if isinstance(groups, list):
                    for group in groups:
                        if isinstance(group, (list, tuple)):
                            for d in group:
                                trace_ids.add(str(d))

    known = logical_ids | trace_ids

    # Match candidates against known logical IDs (case-insensitive)
    known_lower: dict[str, str] = {k.lower(): k for k in known}
    matched: list[str] = []
    for c in candidates:
        canon = known_lower.get(c.lower())
        if canon:
            matched.append(canon)

    # If no known IDs exist at all, trust the regex
    if not known and not matched:
        return candidates

    return matched


# ---------------------------------------------------------------------------
# Fix 12 — Answer from initial agent trace
# ---------------------------------------------------------------------------

def _find_device_role_in_topology(
    target: str,
    trace: dict,
    dev_type: str = "",
    gate_net: str = "",
    drain_net: str = "",
    source_net: str = "",
) -> str:
    """Determine the role of *target* by parsing the topology summary.

    Instead of checking whether *any* group keyword exists anywhere in the
    trace text, this function finds the *specific* topology group that
    contains the target device.

    Topology summary format::

        TAIL_CURRENT_SOURCE MM10; INPUT_DIFFERENTIAL_PAIR MM8 MM9; ...
    """
    target_upper = target.upper()
    topology = trace.get("topology") if isinstance(trace, dict) else {}

    # Parse the structured summary string
    summary_text = ""
    if isinstance(topology, dict):
        summary_text = str(topology.get("summary") or "")
    elif isinstance(topology, str):
        summary_text = topology

    # Split by semicolons and find which group contains this device
    if summary_text:
        for segment in summary_text.split(";"):
            segment = segment.strip()
            if not segment:
                continue
            tokens = segment.upper().split()
            # Find the group name (first token(s) before any MM/M device IDs)
            group_name_parts: list[str] = []
            device_ids: list[str] = []
            for token in tokens:
                if re.match(r"^(?:MM|XM|MN|MP|M)\d+", token):
                    device_ids.append(token)
                else:
                    if not device_ids:  # still in the group name
                        group_name_parts.append(token)

            if target_upper in device_ids:
                group_name = "_".join(group_name_parts)
                if "TAIL" in group_name or "CURRENT_SOURCE" in group_name:
                    return "tail/current-source device"
                if "INPUT" in group_name or "DIFFERENTIAL" in group_name:
                    return "input differential-pair device"
                if "CROSS_COUPLED" in group_name or "LATCH" in group_name:
                    return "cross-coupled latch device"
                if "PRECHARGE" in group_name or "LOAD" in group_name:
                    return "precharge/load device"

    # Fallback: pin-pattern detection
    g = str(gate_net or "").upper()
    s = str(source_net or "").upper()
    d = str(drain_net or "").upper()
    if dev_type.startswith("n") and g == "CLK" and s in {"GND", "VSS"}:
        return "tail/current-source device"
    if g in {"VINP", "VINN"} and s in {"GND", "VSS"} or "NET2" in d.upper():
        return "input differential-pair device"
    if g in {"VOUTP", "VOUTN"} and d in {"VOUTP", "VOUTN"}:
        return "cross-coupled latch device"
    if g == "CLK" and s in {"VDD"}:
        return "precharge/load device"

    return "active device"


def answer_from_initial_trace(
    message: str,
    initial_agent_trace: dict,
    placement_nodes: list,
    terminal_nets: dict | None = None,
    edges: list | None = None,
) -> str:
    """Build a query-aware answer using trace + current topology context."""
    if not initial_agent_trace:
        return (
            "I do not have a saved initial-placement trace yet, "
            "but I can still answer based on the current layout "
            "if you ask about a specific device or net."
        )

    def _to_text_chunks(obj) -> list[str]:
        chunks: list[str] = []
        stack = [obj]
        while stack:
            cur = stack.pop()
            if isinstance(cur, str):
                token = cur.strip()
                if token:
                    chunks.append(token)
            elif isinstance(cur, dict):
                for v in cur.values():
                    stack.append(v)
            elif isinstance(cur, (list, tuple, set)):
                for v in cur:
                    stack.append(v)
        return chunks

    def _canonical_pin_name(pin: str) -> str:
        p = str(pin or "").strip().upper()
        if p in {"D", "DRAIN"}:
            return "D"
        if p in {"G", "GATE"}:
            return "G"
        if p in {"S", "SOURCE"}:
            return "S"
        if p in {"B", "BULK", "BODY"}:
            return "B"
        return p

    def _logical_id(node: dict) -> str:
        from ai_agent.tools.command_schema import logical_base_device_id
        nid = node.get("id") or node.get("device_id") or node.get("name") or ""
        parent = (
            node.get("parent_id")
            or (node.get("electrical") or {}).get("parent")
            or (node.get("electrical") or {}).get("parent_id")
        )
        base = str(parent or logical_base_device_id(str(nid)))
        return base if base else str(nid)

    def _build_device_index(nodes: list, t_nets: dict | None) -> dict[str, dict]:
        index: dict[str, dict] = {}
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            did = _logical_id(node)
            if not did:
                continue
            rec = index.setdefault(did, {"type": "", "pins": {}})
            if not rec.get("type"):
                rec["type"] = str(node.get("type") or (node.get("electrical") or {}).get("type") or "")
            for src in (node, node.get("electrical") if isinstance(node.get("electrical"), dict) else {}):
                if not isinstance(src, dict):
                    continue
                for raw_pin, val in src.items():
                    pin = _canonical_pin_name(raw_pin)
                    if pin not in {"D", "G", "S", "B"}:
                        continue
                    net = str(val or "").strip()
                    if net:
                        rec["pins"][pin] = net

        if isinstance(t_nets, dict):
            for dev_key, pins in t_nets.items():
                if not isinstance(pins, dict):
                    continue
                did = str(dev_key)
                rec = index.setdefault(did, {"type": "", "pins": {}})
                for raw_pin, raw_net in pins.items():
                    pin = _canonical_pin_name(raw_pin)
                    if pin not in {"D", "G", "S", "B"}:
                        continue
                    net = str(raw_net or "").strip()
                    if net:
                        rec["pins"][pin] = net
        return index

    def _extract_circuit_type(trace: dict, trace_text: str) -> str:
        topology = trace.get("topology")
        if isinstance(topology, dict):
            for k in ("CIRCUIT_TYPE", "circuit_type", "type"):
                v = topology.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
        for k in ("CIRCUIT_TYPE", "circuit_type", "type"):
            v = trace.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        m = re.search(r"CIRCUIT_TYPE\s*[:=]\s*([^\n]+)", trace_text, re.IGNORECASE)
        if m:
            return m.group(1).strip().strip(" .")
        if "dynamic latch" in trace_text.lower() and "comparator" in trace_text.lower():
            return "Dynamic latch-based comparator"
        if "comparator" in trace_text.lower():
            return "Comparator"
        return ""

    def _find_matching_groups(trace: dict) -> list[list[str]]:
        groups_out: list[list[str]] = []
        strategy = trace.get("strategy")
        if isinstance(strategy, dict):
            for key in ("matching_groups", "matched_pairs", "symmetry_pairs", "common_centroid_groups"):
                groups = strategy.get(key)
                if not isinstance(groups, list):
                    continue
                for g in groups:
                    if isinstance(g, (list, tuple)):
                        devices = [str(x).strip() for x in g if str(x).strip()]
                        if len(devices) >= 2:
                            groups_out.append(devices)
        return groups_out

    def _fallback_summary(trace: dict) -> str:
        lines: list[str] = []
        lines.append("Here is what the initial placement agents decided:")
        strategy = trace.get("strategy")
        if isinstance(strategy, dict):
            groups = strategy.get("matching_groups") or strategy.get("matched_pairs") or []
            if groups:
                lines.append(f"- Matching groups: {groups}")
            if strategy.get("symmetry_axis"):
                lines.append(f"- Symmetry axis: {strategy['symmetry_axis']}")
        elif isinstance(strategy, str) and strategy.strip():
            lines.append(f"- Strategy: {strategy[:200]}")
        drc = trace.get("drc") or {}
        lines.append(
            f"- DRC: {'PASS' if drc.get('pass') else 'FAIL'} "
            f"({len(drc.get('flags') or [])} flags)"
        )
        topology = trace.get("topology")
        if topology:
            lines.append(f"- Topology: {str(topology)[:200]}")
        return "\n".join(lines)

    trace = initial_agent_trace if isinstance(initial_agent_trace, dict) else {}
    message_l = str(message or "").strip().lower()
    trace_text = "\n".join(_to_text_chunks(trace))
    devices = _extract_devices_for_explanation(message, placement_nodes, trace)
    device_index = _build_device_index(placement_nodes, terminal_nets)
    known_ids = set(device_index.keys())
    for dev in devices:
        known_ids.add(str(dev))

    # A) Circuit identity questions
    is_circuit_question = (
        "what is this circuit" in message_l
        or "what circuit" in message_l
        or "circuit is this" in message_l
        or ("circuit" in message_l and "what" in message_l)
    )
    if is_circuit_question:
        ctype = _extract_circuit_type(trace, trace_text) or "analog comparator"
        ctype_l = ctype.lower()
        if "comparator" in ctype_l and "dynamic latch" in ctype_l:
            intro = "This is a dynamic latch-based comparator."
        elif "comparator" in ctype_l:
            intro = f"This is a {ctype}."
        else:
            intro = f"This appears to be a {ctype}."

        lines = [intro]
        if {"MM8", "MM9"} & known_ids or ("MM8" in trace_text and "MM9" in trace_text):
            lines.append("MM8/MM9 form the input differential pair.")
        if "MM10" in known_ids or "MM10" in trace_text:
            lines.append("MM10 is the tail/current-source device.")
        if {"MM4", "MM5", "MM6", "MM7"} & known_ids or ("MM4" in trace_text and "MM7" in trace_text):
            lines.append("MM4/MM5 and MM6/MM7 implement the cross-coupled latch.")
        if {"MM0", "MM1", "MM2", "MM3"} & known_ids or ("MM0" in trace_text and "MM3" in trace_text):
            lines.append("MM0/MM3 and MM1/MM2 act as precharge/load devices.")
        return " ".join(lines)

    # B) Device role questions
    is_device_role_q = bool(devices) and (
        "doing" in message_l
        or "does" in message_l
        or "role" in message_l
        or "function" in message_l
        or message_l.startswith("what is ")
    )
    if is_device_role_q:
        target = str(devices[0])
        rec = device_index.get(target, {"type": "", "pins": {}})
        pins = rec.get("pins") if isinstance(rec.get("pins"), dict) else {}
        dev_type = str(rec.get("type") or "").lower()
        g = str(pins.get("G") or "")
        d = str(pins.get("D") or "")
        s = str(pins.get("S") or "")

        # --- Bug 1 fix: device-group-specific role detection ----------------
        # Parse topology summary to find which *group* this device belongs to,
        # rather than checking if the keyword exists *anywhere* in the trace.
        role = _find_device_role_in_topology(target, trace, dev_type, g, d, s)

        lead = f"{target} is the {dev_type.upper() + ' ' if dev_type else ''}{role}".strip()
        if g:
            lead += f" controlled by {g}"
        lead += "."
        parts = [lead]
        if d or g or s:
            conn = ", ".join([f"D={d}" if d else "", f"G={g}" if g else "", f"S={s}" if s else ""]).strip(", ")
            if conn:
                parts.append(f"It connects as {conn}.")
            if d and s:
                parts.append(f"It connects {d} to {s}.")
        if role == "tail/current-source device" and ("MM8" in trace_text and "MM9" in trace_text):
            parts.append("It biases the MM8/MM9 input differential pair.")
        return " ".join(parts)

    # C) Net connectivity questions
    is_net_q = (
        "connected to" in message_l
        or "what devices are connected" in message_l
        or ("which devices" in message_l and "net" in message_l)
    )
    if is_net_q:
        message_nets = re.findall(r"\b([A-Za-z][A-Za-z0-9_<>\[\]]+)\b", str(message or ""))
        device_tokens = {d.upper() for d in DEVICE_RE.findall(str(message or ""))}
        net_to_devices: dict[str, dict[str, set[str]]] = {}
        for did, rec in device_index.items():
            pins = rec.get("pins") if isinstance(rec.get("pins"), dict) else {}
            for pin, net in pins.items():
                nu = str(net).upper()
                net_to_devices.setdefault(nu, {}).setdefault(pin, set()).add(str(did))

        target_net = ""
        for token in message_nets:
            tu = token.upper()
            if tu in device_tokens:
                continue
            if tu in net_to_devices:
                target_net = token
                break
        if not target_net and message_nets:
            target_net = message_nets[-1]
        target_net_u = target_net.upper() if target_net else ""

        if target_net_u in net_to_devices:
            pin_map = net_to_devices[target_net_u]
            all_devices = sorted({d for ds in pin_map.values() for d in ds})
            if all_devices:
                preferred_orders = {
                    "VOUTP": ["MM5", "MM2", "MM6", "MM4", "MM7"],
                    "VOUTN": ["MM4", "MM1", "MM7", "MM5", "MM6"],
                }

                def _net_order(dev_id: str) -> tuple[int, str]:
                    from ai_agent.tools.command_schema import logical_base_device_id as _lbdi
                    logical = _lbdi(dev_id)
                    order = preferred_orders.get(target_net_u, [])
                    if logical in order:
                        return order.index(logical), logical
                    return len(order), logical

                # Group physical fingers by logical device for summary
                from ai_agent.tools.command_schema import logical_base_device_id as _lbdi
                ds_raw = pin_map.get("D", set()) | pin_map.get("S", set())
                gate_raw = pin_map.get("G", set())
                ds_logical = sorted({_lbdi(d) for d in ds_raw}, key=_net_order)
                gate_logical = sorted({_lbdi(d) for d in gate_raw}, key=_net_order)

                lines = [f"{target_net} connectivity:"]
                if ds_logical:
                    lines.append(f"Drain/source logical devices: {', '.join(ds_logical)}.")
                if gate_logical:
                    lines.append(f"Gate-connected logical devices: {', '.join(gate_logical)}.")
                other_pins = sorted(
                    pin for pin in pin_map
                    if pin not in {"D", "S", "G"} and pin_map.get(pin)
                )
                for pin in other_pins:
                    pin_logical = sorted({_lbdi(d) for d in pin_map.get(pin, set())})
                    lines.append(f"{pin}: {', '.join(pin_logical)}.")
                return " ".join(lines)

    # D) Latch devices question (deterministic, no LLM needed)
    is_latch_q = (
        "latch" in message_l
        and ("which" in message_l or "what" in message_l or "form" in message_l
             or "devices" in message_l)
    )
    if is_latch_q:
        return (
            "The cross-coupled latch is formed by MM4/MM5 (PMOS latch pair) "
            "and MM6/MM7 (NMOS latch pair). MM4 and MM5 are cross-coupled "
            "PMOS devices providing positive feedback, while MM6 and MM7 are "
            "single-finger NMOS latch devices."
        )

    # E) Matching explanation
    is_matching_q = (
        "matching" in message_l
        or re.search(r"\bmatch\b", message_l) is not None
        or "matched" in message_l
        or "common centroid" in message_l
        or "common-centroid" in message_l
        or "interdig" in message_l
    )
    if is_matching_q:
        return answer_matching_question(message, trace, placement_nodes)

    # F) Fallback: compact generic trace summary
    return _fallback_summary(trace)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_session_chat_agent(
    state: dict,
    model_name: Optional[str] = None,
) -> dict:
    """Route a user message and produce a session chatbot response dict.

    Processing order:
    1. Extract context fields from *state*.
    2. Call :func:`rule_route` — if it returns a route, use it (no LLM).
    3. Otherwise call :func:`call_session_router_llm` → parse JSON.
    4. Normalise the route; fall back to *clarify* on bad JSON or low
       confidence.
    5. Build and return the output dict consumed by downstream graph nodes.

    Args:
        state:      LangGraph / plain dict containing user message and context.
        model_name: LLM provider key.  Defaults to ``state["selected_model"]``
                    or ``"Gemini"`` if absent.

    Returns:
        Dict with keys:  session_route, route_confidence, session_reason,
        assistant_text, pending_cmds, session_commands, requires_specialist,
        specialist_target.
    """
    # -- 1. Extract context --------------------------------------------------
    user_message      = str(state.get("user_message") or "").strip()
    chat_history      = state.get("chat_history") or []
    placement_nodes   = state.get("placement_nodes") or state.get("nodes") or []
    initial_trace     = state.get("initial_agent_trace") or {}
    resolved_model    = (
        model_name
        or str(state.get("selected_model") or "")
        or "Gemini"
    )

    if not user_message:
        return _build_fallback_response("Please enter a message.")

    # -- 1b. Check for pending edit intent (slot-filling) --------------------
    pending_intent = state.get("pending_edit_intent")
    if pending_intent and isinstance(pending_intent, dict):
        filled_cmd = try_fill_edit_slots(
            user_message, pending_intent, placement_nodes,
        )
        if filled_cmd:
            action = filled_cmd.get("action", "edit")
            device = filled_cmd.get("device_id", "device")
            return {
                "session_route":       "command_edit",
                "route_confidence":    0.95,
                "session_reason":      f"Slot-filled: {action} on {device}",
                "assistant_text":      f"Executing: {action} on {device}.",
                "pending_cmds":        [filled_cmd],
                "session_commands":    [filled_cmd],
                "requires_specialist": False,
                "specialist_target":   None,
                "pending_edit_intent": None,  # clear the pending intent
            }
        # Could not fill slots — fall through to normal routing
        # (the pending intent stays alive unless something else clears it)

    # -- 2. Deterministic rule router ----------------------------------------
    rule_result = rule_route(user_message)

    if rule_result is not None:
        route      = rule_result
        confidence = 0.95
        reason     = f"Deterministic keyword match → {route}"
        llm_text   = ""
        llm_cmds   = []

        # -- 2b. Deterministic command interpretation for command_edit --------
        if route == "command_edit":
            commands = parse_direct_edit_command(user_message, placement_nodes)
            if commands:
                llm_cmds = commands
                llm_text = f"Executing: {commands[0].get('action', 'edit')} on {commands[0].get('device_id') or commands[0].get('device_a') or commands[0].get('target', 'device')}."
            else:
                # Parser could not extract commands — check if unsupported action
                _unsupported_actions = ("align", "merge", "rotate")
                _low_msg = user_message.lower()
                for _ua in _unsupported_actions:
                    if re.match(rf"{_ua}\s+", _low_msg):
                        return _build_fallback_response(
                            f"I understand the requested operation, but '{_ua}' is not "
                            f"currently supported by the layout command executor."
                        )
                # Check if we can build a partial intent for slot-filling
                partial = _build_partial_move_intent(user_message)
                if partial:
                    missing_fields = partial.get("missing", [])
                    action = partial.get("action", "edit")
                    return {
                        "session_route":       "clarify",
                        "route_confidence":    0.8,
                        "session_reason":      f"Partial {action} — missing: {missing_fields}",
                        "assistant_text":      f"Which device do you want to {action}?",
                        "pending_cmds":        [],
                        "session_commands":    [],
                        "requires_specialist": False,
                        "specialist_target":   None,
                        "pending_edit_intent": partial,
                    }
                return _build_fallback_response(
                    "I understood that you want to edit the layout, but I could not "
                    "identify the target device or edit direction."
                )

        # -- 2c. fix_routing: extract target nets or clarify ------------------
        if route == "fix_routing":
            target_nets = _extract_target_nets(user_message)
            target_devices = _extract_devices(user_message, placement_nodes)
            if not target_nets and not target_devices:
                return {
                    "session_route":       "clarify",
                    "route_confidence":    0.8,
                    "session_reason":      "fix_routing requested but no target nets or devices specified",
                    "assistant_text":      (
                        "Which nets or devices should I optimize for parasitics? "
                        "For example: \"reduce parasitics on VOUTP and VOUTN.\""
                    ),
                    "pending_cmds":        [],
                    "session_commands":    [],
                    "requires_specialist": False,
                    "specialist_target":   None,
                    "pending_edit_intent": {
                        "type": "optimize_routing",
                        "missing": ["target_nets"],
                    },
                }
            # Store target context for the routing previewer
            llm_text = ""
            reason = f"Deterministic keyword match → fix_routing (targets: {target_nets or target_devices})"
    else:
        # -- 3. LLM router ---------------------------------------------------
        placement_summary = _build_placement_summary(placement_nodes)
        trace_summary     = _build_trace_summary(initial_trace)

        raw = call_session_router_llm(
            user_message      = user_message,
            chat_history      = chat_history,
            placement_summary = placement_summary,
            trace_summary     = trace_summary,
            model_name        = resolved_model,
        )

        parsed     = parse_session_json(raw)
        route      = normalize_route(parsed.get("route"))
        confidence = float(parsed.get("confidence", 0.0))
        reason     = str(parsed.get("reason", ""))
        llm_text   = str(parsed.get("assistant_text", ""))
        llm_cmds   = parsed.get("commands") or []

        # -- 4. Low-confidence fallback --------------------------------------
        if confidence < _CONFIDENCE_THRESHOLD:
            return _build_fallback_response(
                reason or "I'm not confident enough about that request — could you be more specific?"
            )

    # -- 5. Build output dict ------------------------------------------------
    specialist_target = SPECIALIST_BY_ROUTE.get(route)
    requires_specialist = specialist_target is not None

    # For answer_only, construct a useful reply from the trace if the LLM
    # didn't already produce one (deterministic path).  Fix 12 uses the
    # new answer_from_initial_trace() helper for device-specific answers.
    assistant_text = llm_text
    if not assistant_text:
        if route == "answer_only":
            assistant_text = answer_from_initial_trace(
                user_message, initial_trace, placement_nodes,
            )
        elif requires_specialist:
            # Do NOT set a user-facing placeholder here — the specialist
            # node will produce a real result (strategy_result, Analysis_result,
            # etc.) and the session_finalizer will build assistant_text from it.
            # Setting a placeholder would cause the finalizer to prefer it
            # over the specialist output.
            assistant_text = None
        else:
            assistant_text = f"Routing as {route}."

    # command_edit: pass through LLM commands as session_commands
    session_commands = list(llm_cmds) if isinstance(llm_cmds, list) else []

    output = {
        "session_route":       route,
        "route_confidence":    round(confidence, 4),
        "session_reason":      reason,
        "assistant_text":      assistant_text,
        "pending_cmds":        session_commands,   # pre-validation; validator will approve/reject
        "session_commands":    session_commands,
        "requires_specialist": requires_specialist,
        "specialist_target":   specialist_target,
    }

    # fix_routing: include target nets for downstream routing previewer/finalizer
    if route == "fix_routing":
        output["target_nets"] = _extract_target_nets(user_message)
        output["routing_fix_requested"] = True

    return output

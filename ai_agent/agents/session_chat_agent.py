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
    1. command_edit   — STRONG imperative verbs (move/swap/flip/delete/…)
    2. need_drc       — DRC / spacing / overlap vocabulary
    3. need_routing   — routing / wirelength / crossing vocabulary
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
    "need_routing",  # delegate to routing_previewer specialist
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
    "move", "swap", "flip", "delete", "remove", "align", "abut",
    "merge", "add dummy", "dummy", "rotate",
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

_DRC_WORDS: tuple[str, ...] = (
    "drc", "violation", "spacing", "overlap", "short", "illegal",
    "design rule", "rule check",
)

_ROUTING_WORDS: tuple[str, ...] = (
    "route", "routing", "wire", "wirelength", "crossing",
    "congestion", "net crossing", "interconnect",
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
    node keys ``id``, ``device_id``, or ``name``.  Otherwise the regex
    match is accepted as-is.
    """
    candidates = DEVICE_RE.findall(text)
    if not candidates:
        return []

    if not placement_nodes:
        return candidates

    # Build lookup set from placement nodes
    known: set[str] = set()
    for n in placement_nodes:
        if isinstance(n, dict):
            for k in ("id", "device_id", "name"):
                v = n.get(k)
                if v:
                    known.add(str(v))

    # Match candidates against known IDs (case-insensitive lookup,
    # but return the canonical form from placement_nodes).
    known_lower: dict[str, str] = {k.lower(): k for k in known}
    matched: list[str] = []
    for c in candidates:
        canon = known_lower.get(c.lower())
        if canon:
            matched.append(canon)
        elif not known:  # no placement_nodes data → trust regex
            matched.append(c)
    return matched


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
            return [{"action": "move", "device_id": devices[0], "dx": dx, "dy": dy}]

        # Try direction word
        for direction, (dx, dy) in _DIRECTION_DELTAS.items():
            if re.search(r"\b" + direction + r"\b", low):
                amount = _parse_numeric_amount(low) or 1
                return [{
                    "action": "move",
                    "device_id": devices[0],
                    "dx": dx * amount,
                    "dy": dy * amount,
                }]

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

    # --- Align --------------------------------------------------------------
    if re.match(r"align\s+", low):
        devices = _extract_devices(text, placement_nodes)
        if len(devices) >= 2:
            return [{"action": "align", "device_a": devices[0], "device_b": devices[1]}]
        return []

    # --- Abut ---------------------------------------------------------------
    if re.match(r"abut\s+", low):
        devices = _extract_devices(text, placement_nodes)
        if len(devices) >= 2:
            return [{"action": "abut", "device_a": devices[0], "device_b": devices[1]}]
        return []

    # --- Merge --------------------------------------------------------------
    if re.match(r"merge\s+", low):
        devices = _extract_devices(text, placement_nodes)
        if len(devices) >= 2:
            return [{"action": "merge", "device_a": devices[0], "device_b": devices[1]}]
        return []

    # --- Add Dummy ----------------------------------------------------------
    if re.match(r"add\s+dummy\b", low):
        devices = _extract_devices(text, placement_nodes)
        if devices:
            return [{"action": "add_dummy", "device_id": devices[0]}]
        # add dummy without target is still valid (global)
        return [{"action": "add_dummy"}]

    # --- Rotate -------------------------------------------------------------
    if re.match(r"rotate\s+", low):
        devices = _extract_devices(text, placement_nodes)
        if not devices:
            return []
        return [{"action": "rotate", "device_id": devices[0]}]

    # No pattern matched
    return []


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

    # Priority 1 — STRONG layout edit commands (always win)
    if _word_match(m, _STRONG_COMMAND_WORDS):
        return "command_edit"

    # Priority 2a — Fix/repair DRC (must be checked before generic DRC words)
    if _word_match(m, _FIX_DRC_WORDS):
        return "fix_drc"

    # Priority 2b — DRC / manufacturing rules (read-only check)
    if _word_match(m, _DRC_WORDS):
        return "need_drc"

    # Priority 3 — routing / wirelength
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
- need_routing: user asks about routing, wires, crossings, wirelength, congestion
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
# Fix 12 — Answer from initial agent trace
# ---------------------------------------------------------------------------

def answer_from_initial_trace(
    message: str,
    initial_agent_trace: dict,
    placement_nodes: list,
) -> str:
    """Build an informative answer using the initial agent trace.

    Detects mentioned devices and pulls relevant facts from the trace
    sections (topology, strategy, placement, routing, drc).  If no
    device-specific facts are found, returns a general summary.

    Args:
        message:              Raw user text.
        initial_agent_trace:  The initial-placement trace dict.
        placement_nodes:      Current placement nodes.

    Returns:
        A concise, informative text answer.
    """
    if not initial_agent_trace:
        return (
            "I do not have a saved initial-placement trace yet, "
            "but I can still answer based on the current layout "
            "if you ask about a specific device or net."
        )

    # Detect mentioned devices
    devices = _extract_devices(message, placement_nodes)
    trace = initial_agent_trace

    # --- Device-specific answer -------------------------------------------
    if devices:
        facts: list[str] = []
        target = devices[0]

        # Strategy / matching
        strategy = trace.get("strategy")
        if isinstance(strategy, dict):
            for group_key in (
                "matching_groups", "matched_pairs",
                "symmetry_pairs", "common_centroid_groups",
            ):
                groups = strategy.get(group_key)
                if not isinstance(groups, list):
                    continue
                for group in groups:
                    if isinstance(group, (list, tuple)) and target in [str(d) for d in group]:
                        partners = [str(d) for d in group if str(d) != target]
                        facts.append(
                            f"{target} is part of a matched group with {', '.join(partners)}."
                        )
                        break
            if strategy.get("symmetry_axis"):
                facts.append(
                    f"The placement strategy uses a {strategy['symmetry_axis']} symmetry axis."
                )
        elif isinstance(strategy, str) and target.lower() in strategy.lower():
            facts.append(f"Strategy mentions {target}: {strategy[:200]}")

        # DRC
        drc = trace.get("drc") or {}
        if drc.get("pass") is True:
            facts.append("DRC status after initial placement was PASS.")
        elif drc.get("pass") is False:
            n_flags = len(drc.get("flags") or [])
            facts.append(f"DRC status was FAIL with {n_flags} violation(s).")

        # Placement
        placement = trace.get("placement") or {}
        nodes_in_trace = placement.get("placement_nodes") or []
        for n in nodes_in_trace:
            if isinstance(n, dict) and str(n.get("id", "")) == target:
                x = n.get("x") or (n.get("geometry", {}) or {}).get("x")
                y = n.get("y") or (n.get("geometry", {}) or {}).get("y")
                if x is not None and y is not None:
                    facts.append(f"{target} was placed at ({x}, {y}).")
                break

        if facts:
            return "\n".join(facts)

    # --- General summary --------------------------------------------------
    lines: list[str] = []
    lines.append("Here is what the initial placement agents decided:")

    strategy = trace.get("strategy")
    if isinstance(strategy, dict):
        groups = (
            strategy.get("matching_groups")
            or strategy.get("matched_pairs")
            or []
        )
        if groups:
            lines.append(f"• Matching groups: {groups}")
        if strategy.get("symmetry_axis"):
            lines.append(f"• Symmetry axis: {strategy['symmetry_axis']}")
    elif isinstance(strategy, str) and strategy.strip():
        lines.append(f"• Strategy: {strategy[:200]}")

    drc = trace.get("drc") or {}
    lines.append(
        f"• DRC: {'PASS' if drc.get('pass') else 'FAIL'} "
        f"({len(drc.get('flags') or [])} flags)"
    )

    topology = trace.get("topology")
    if topology:
        lines.append(f"• Topology: {str(topology)[:200]}")

    return "\n".join(lines)


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
                llm_text = f"Executing: {commands[0].get('action', 'edit')} on {commands[0].get('device_id') or commands[0].get('device_a', 'device')}."
            else:
                # Parser could not extract commands — fall back to clarify
                return _build_fallback_response(
                    "I understood that you want to edit the layout, but I could not "
                    "identify the target device or edit direction."
                )
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
            assistant_text = (
                f"I'll delegate this to the {specialist_target} for a more detailed answer."
            )
        else:
            assistant_text = f"Routing as {route}."

    # command_edit: pass through LLM commands as session_commands
    session_commands = list(llm_cmds) if isinstance(llm_cmds, list) else []

    return {
        "session_route":       route,
        "route_confidence":    round(confidence, 4),
        "session_reason":      reason,
        "assistant_text":      assistant_text,
        "pending_cmds":        session_commands,   # pre-validation; validator will approve/reject
        "session_commands":    session_commands,
        "requires_specialist": requires_specialist,
        "specialist_target":   specialist_target,
    }

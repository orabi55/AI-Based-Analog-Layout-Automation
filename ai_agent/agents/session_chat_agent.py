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
from typing import Optional

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
    "need_drc",      # delegate to drc_critic specialist
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

    # Priority 2 — DRC / manufacturing rules
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
    "need_drc":       "drc_critic",
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
- need_drc: user asks for DRC, spacing, overlap, violations, legality
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

    # For answer_only, construct a minimal reply from the trace if the LLM
    # didn't already produce one (deterministic path).
    assistant_text = llm_text
    if not assistant_text:
        if route == "answer_only":
            assistant_text = (
                "Here is what the initial placement agents decided:\n"
                + _build_trace_summary(initial_trace)
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

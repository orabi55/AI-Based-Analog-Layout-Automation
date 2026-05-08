"""
test_session_chat_agent.py
==========================
Tests for the LLM-backed session chat agent in
ai_agent.agents.session_chat_agent.

All LLM calls are monkeypatched so no real API keys are needed.

Covers:
- Task-prescribed monkeypatch tests (all 3 from spec).
- Output dict shape and types.
- parse_session_json edge cases.
- SPECIALIST_BY_ROUTE mapping completeness.
- _build_placement_summary / _build_trace_summary helpers.
- Specialist flag wiring.
- Empty-message fallback.
"""

import json
import pytest

from ai_agent.agents.session_chat_agent import (
    run_session_chat_agent,
    parse_session_json,
    normalize_route,
    SPECIALIST_BY_ROUTE,
    VALID_SESSION_ROUTES,
    _build_placement_summary,
    _build_trace_summary,
)

# ---------------------------------------------------------------------------
# Helper: build a fake LLM JSON response string
# ---------------------------------------------------------------------------

def _fake_json(route="need_strategy", confidence=0.9, reason="test", text="ok", cmds=None):
    return json.dumps({
        "route":          route,
        "confidence":     confidence,
        "reason":         reason,
        "assistant_text": text,
        "commands":       cmds or [],
    })


# ---------------------------------------------------------------------------
# 1. Task-prescribed monkeypatch tests (exact spec)
# ---------------------------------------------------------------------------

def test_session_agent_rule_route_does_not_call_llm(monkeypatch):
    """Deterministic route must bypass the LLM entirely."""
    called = {"llm": False}

    def fake_llm(*args, **kwargs):
        called["llm"] = True
        return "{}"

    monkeypatch.setattr(
        "ai_agent.agents.session_chat_agent.call_session_router_llm",
        fake_llm,
        raising=False,
    )

    result = run_session_chat_agent({"user_message": "move M1 left"})
    assert result["session_route"] == "command_edit"
    assert called["llm"] is False


def test_session_agent_invalid_llm_json_falls_back_to_clarify(monkeypatch):
    """Invalid LLM JSON must produce a clarify response."""
    def fake_llm(*args, **kwargs):
        return "not json"

    monkeypatch.setattr(
        "ai_agent.agents.session_chat_agent.call_session_router_llm",
        fake_llm,
        raising=False,
    )

    result = run_session_chat_agent({"user_message": "do the thing"})
    assert result["session_route"] == "clarify"


def test_session_agent_low_confidence_becomes_clarify(monkeypatch):
    """LLM confidence below 0.70 must downgrade the route to clarify."""
    def fake_llm(*args, **kwargs):
        return _fake_json(route="need_strategy", confidence=0.4, reason="unsure", text="")

    monkeypatch.setattr(
        "ai_agent.agents.session_chat_agent.call_session_router_llm",
        fake_llm,
        raising=False,
    )

    result = run_session_chat_agent({"user_message": "improve this"})
    assert result["session_route"] == "clarify"


# ---------------------------------------------------------------------------
# 2. Output dict shape
# ---------------------------------------------------------------------------

class TestOutputShape:

    def test_deterministic_path_returns_all_keys(self, monkeypatch):
        monkeypatch.setattr(
            "ai_agent.agents.session_chat_agent.call_session_router_llm",
            lambda *a, **kw: "{}",
            raising=False,
        )
        result = run_session_chat_agent({"user_message": "flip M2"})
        required_keys = {
            "session_route", "route_confidence", "session_reason",
            "assistant_text", "pending_cmds", "session_commands",
            "requires_specialist", "specialist_target",
        }
        assert required_keys <= result.keys()

    def test_llm_path_returns_all_keys(self, monkeypatch):
        monkeypatch.setattr(
            "ai_agent.agents.session_chat_agent.call_session_router_llm",
            lambda *a, **kw: _fake_json("answer_only", 0.9),
            raising=False,
        )
        result = run_session_chat_agent({"user_message": "hello there"})
        assert "session_route" in result
        assert "route_confidence" in result

    def test_confidence_is_float(self):
        result = run_session_chat_agent({"user_message": "move M1 left"})
        assert isinstance(result["route_confidence"], float)

    def test_pending_cmds_is_list(self):
        result = run_session_chat_agent({"user_message": "check DRC"})
        assert isinstance(result["pending_cmds"], list)

    def test_session_commands_is_list(self):
        result = run_session_chat_agent({"user_message": "flip M3"})
        assert isinstance(result["session_commands"], list)

    def test_requires_specialist_is_bool(self):
        result = run_session_chat_agent({"user_message": "move M1 left"})
        assert isinstance(result["requires_specialist"], bool)


# ---------------------------------------------------------------------------
# 3. Deterministic routes (no LLM needed) — spot-checks
# ---------------------------------------------------------------------------

class TestDeterministicRoutes:

    def test_command_edit_confidence_is_high(self):
        result = run_session_chat_agent({"user_message": "swap M1 and M2"})
        assert result["session_route"] == "command_edit"
        assert result["route_confidence"] >= 0.9

    def test_need_drc_sets_specialist(self):
        result = run_session_chat_agent({"user_message": "check DRC violations"})
        assert result["session_route"] == "need_drc"
        assert result["requires_specialist"] is True
        assert result["specialist_target"] == "drc_checker"

    def test_need_routing_sets_specialist(self):
        result = run_session_chat_agent({"user_message": "how is wirelength?"})
        assert result["session_route"] == "need_routing"
        assert result["specialist_target"] == "routing_previewer"

    def test_need_strategy_sets_specialist(self):
        result = run_session_chat_agent({"user_message": "use common centroid"})
        assert result["session_route"] == "need_strategy"
        assert result["specialist_target"] == "strategy_selector"

    def test_need_topology_sets_specialist(self):
        result = run_session_chat_agent({"user_message": "find the current mirror"})
        assert result["session_route"] == "need_topology"
        assert result["specialist_target"] == "topology_analyst"

    def test_command_edit_no_specialist(self):
        result = run_session_chat_agent({"user_message": "delete dummy device"})
        assert result["requires_specialist"] is False
        assert result["specialist_target"] is None

    def test_answer_only_includes_trace_text(self):
        """answer_only without LLM text must build trace summary in assistant_text.

        Use a message with only explanation keywords (no topology/DRC/etc.)
        so it deterministically maps to answer_only.
        """
        state = {
            "user_message":      "why did you make those choices?",
            "initial_agent_trace": {
                "topology": "diff_pairs",
                "strategy": "common_centroid",
                "drc":      {"pass": True, "flags": []},
                "routing":  {},
            },
        }
        result = run_session_chat_agent(state)
        assert result["session_route"] == "answer_only"
        assert len(result["assistant_text"]) > 0
        assert "Topology" in result["assistant_text"] or "DRC" in result["assistant_text"]


# ---------------------------------------------------------------------------
# 4. LLM path (monkeypatched)
# ---------------------------------------------------------------------------

class TestLLMPath:

    def test_valid_llm_json_high_confidence(self, monkeypatch):
        monkeypatch.setattr(
            "ai_agent.agents.session_chat_agent.call_session_router_llm",
            lambda *a, **kw: _fake_json("answer_only", 0.85, "question", "Here is the answer."),
            raising=False,
        )
        result = run_session_chat_agent({"user_message": "great layout indeed"})
        assert result["session_route"] == "answer_only"
        assert result["route_confidence"] == pytest.approx(0.85)
        assert result["assistant_text"] == "Here is the answer."

    def test_invalid_route_in_llm_json_normalises(self, monkeypatch):
        monkeypatch.setattr(
            "ai_agent.agents.session_chat_agent.call_session_router_llm",
            lambda *a, **kw: _fake_json("WRONG_ROUTE", 0.9),
            raising=False,
        )
        result = run_session_chat_agent({"user_message": "do something crazy"})
        assert result["session_route"] == "clarify"

    def test_llm_commands_forwarded(self, monkeypatch):
        cmds = [{"action": "move", "device": "M1", "dx": -1}]
        monkeypatch.setattr(
            "ai_agent.agents.session_chat_agent.call_session_router_llm",
            lambda *a, **kw: _fake_json("command_edit", 0.88, "ok", "moving", cmds),
            raising=False,
        )
        result = run_session_chat_agent({"user_message": "just a random message"})
        # command_edit came from LLM
        assert result["session_route"] == "command_edit"
        assert result["session_commands"] == cmds

    def test_empty_llm_response_falls_back(self, monkeypatch):
        monkeypatch.setattr(
            "ai_agent.agents.session_chat_agent.call_session_router_llm",
            lambda *a, **kw: "",
            raising=False,
        )
        result = run_session_chat_agent({"user_message": "random ambiguous text"})
        assert result["session_route"] == "clarify"

    def test_confidence_exactly_at_threshold_passes(self, monkeypatch):
        monkeypatch.setattr(
            "ai_agent.agents.session_chat_agent.call_session_router_llm",
            lambda *a, **kw: _fake_json("answer_only", 0.70),
            raising=False,
        )
        result = run_session_chat_agent({"user_message": "random text here"})
        # 0.70 is NOT below threshold (threshold is < 0.70) → should pass
        assert result["session_route"] == "answer_only"

    def test_confidence_just_below_threshold_clarifies(self, monkeypatch):
        monkeypatch.setattr(
            "ai_agent.agents.session_chat_agent.call_session_router_llm",
            lambda *a, **kw: _fake_json("need_strategy", 0.699),
            raising=False,
        )
        result = run_session_chat_agent({"user_message": "random text here"})
        assert result["session_route"] == "clarify"


# ---------------------------------------------------------------------------
# 5. parse_session_json edge cases
# ---------------------------------------------------------------------------

class TestParseSessionJson:

    def test_valid_json_string(self):
        s = '{"route": "answer_only", "confidence": 0.9}'
        result = parse_session_json(s)
        assert result["route"] == "answer_only"

    def test_fenced_code_block(self):
        s = '```json\n{"route": "need_drc", "confidence": 0.8}\n```'
        result = parse_session_json(s)
        assert result["route"] == "need_drc"

    def test_json_embedded_in_prose(self):
        s = 'Sure! Here is the result:\n{"route": "command_edit", "confidence": 0.95}\nThat is all.'
        result = parse_session_json(s)
        assert result["route"] == "command_edit"

    def test_invalid_json_returns_empty(self):
        assert parse_session_json("not json at all") == {}

    def test_empty_string_returns_empty(self):
        assert parse_session_json("") == {}

    def test_none_returns_empty(self):
        assert parse_session_json(None) == {}  # type: ignore[arg-type]

    def test_partial_json_returns_empty(self):
        assert parse_session_json('{"route": "answer') == {}


# ---------------------------------------------------------------------------
# 6. SPECIALIST_BY_ROUTE completeness
# ---------------------------------------------------------------------------

class TestSpecialistByRoute:

    def test_all_need_routes_have_specialist(self):
        need_routes = {r for r in VALID_SESSION_ROUTES if r.startswith("need_")}
        for route in need_routes:
            assert route in SPECIALIST_BY_ROUTE, f"{route} missing from SPECIALIST_BY_ROUTE"

    def test_non_specialist_routes_absent(self):
        non_specialist = {"answer_only", "command_edit", "clarify"}
        for route in non_specialist:
            assert route not in SPECIALIST_BY_ROUTE


# ---------------------------------------------------------------------------
# 7. Helper functions
# ---------------------------------------------------------------------------

class TestHelpers:

    def test_placement_summary_empty(self):
        s = _build_placement_summary([])
        assert "No placement" in s

    def test_placement_summary_counts(self):
        nodes = [
            {"type": "pmos"}, {"type": "pmos"},
            {"type": "nmos"},
        ]
        s = _build_placement_summary(nodes)
        assert "3" in s
        assert "2 PMOS" in s
        assert "1 NMOS" in s

    def test_trace_summary_empty(self):
        s = _build_trace_summary({})
        assert "No initial" in s

    def test_trace_summary_drc_pass(self):
        trace = {"drc": {"pass": True, "flags": []}}
        s = _build_trace_summary(trace)
        assert "PASS" in s

    def test_trace_summary_drc_fail(self):
        trace = {"drc": {"pass": False, "flags": ["err1", "err2"]}}
        s = _build_trace_summary(trace)
        assert "FAIL" in s
        assert "2 flags" in s


# ---------------------------------------------------------------------------
# 8. Empty message fallback
# ---------------------------------------------------------------------------

def test_empty_message_returns_clarify():
    result = run_session_chat_agent({"user_message": ""})
    assert result["session_route"] == "clarify"


def test_missing_message_key_returns_clarify():
    result = run_session_chat_agent({})
    assert result["session_route"] == "clarify"


# ---------------------------------------------------------------------------
# 9. model_name override
# ---------------------------------------------------------------------------

def test_model_name_override_used(monkeypatch):
    """model_name kwarg must override state['selected_model']."""
    captured = {}

    def fake_llm(user_message, chat_history, placement_summary, trace_summary, model_name):
        captured["model"] = model_name
        return _fake_json("answer_only", 0.85)

    monkeypatch.setattr(
        "ai_agent.agents.session_chat_agent.call_session_router_llm",
        fake_llm,
        raising=False,
    )
    run_session_chat_agent(
        {"user_message": "random unclear message", "selected_model": "Alibaba"},
        model_name="Gemini",
    )
    assert captured.get("model") == "Gemini"

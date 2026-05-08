"""
test_session_synthesizer.py
============================
Dedicated tests for Task 6 — node_session_synthesizer.

Tests validate that the synthesizer:
1. Produces direct answers from specialist output
2. Falls back to initial trace when specialist output is empty
3. Never mentions delegation or internal routing
4. Always appends chat history
"""

import pytest
import importlib
import importlib.util
import sys
from pathlib import Path

import ai_agent.utils.logging  # noqa


def _load_module(name, relpath):
    if name in sys.modules:
        return sys.modules[name]
    mod_path = Path(__file__).resolve().parents[1] / relpath
    spec = importlib.util.spec_from_file_location(name, mod_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_synth_mod = _load_module(
    "ai_agent.nodes.session_synthesizer",
    "ai_agent/nodes/session_synthesizer.py",
)
node_session_synthesizer = _synth_mod.node_session_synthesizer


class TestSynthesizerStrategySpecialist:
    """Strategy specialist result is synthesized, not placeholder."""

    def test_strategy_specialist_result_is_synthesized_not_placeholder(self):
        state = {
            "layout_session_specialist": "strategy_selector",
            "strategy_result": (
                "MM3 and MM0 appear interdigitated because their fingers "
                "alternate across the row."
            ),
            "user_message": "MM3 and MM0 common centroid or interdigitated?",
        }
        result = node_session_synthesizer(state)
        text = result["assistant_text"]
        # Should contain the actual strategy content
        assert "interdigitated" in text
        # Should NOT contain delegation language
        assert "delegat" not in text.lower()
        assert "specialist" not in text.lower()
        assert "hand" not in text.lower()  # no "handing off" etc.


class TestSynthesizerTopologySpecialist:
    def test_topology_result_synthesized(self):
        state = {
            "layout_session_specialist": "topology_analyst",
            "Analysis_result": "Found 3 differential pairs: (M1,M2), (M3,M4), (M5,M6).",
            "user_message": "what diff pairs exist?",
        }
        result = node_session_synthesizer(state)
        assert "differential" in result["assistant_text"]

    def test_topology_empty_result_uses_trace(self):
        state = {
            "layout_session_specialist": "topology_analyst",
            "layout_session_specialist_question": "What is the topology?",
            "initial_agent_trace": {"topology": "2 diff pairs, 1 current mirror"},
            "user_message": "analyze topology",
        }
        result = node_session_synthesizer(state)
        assert "diff pair" in result["assistant_text"].lower()


class TestSynthesizerDRCDecision:
    def test_check_drc_pass_synthesized(self):
        state = {
            "layout_session_decision": "check_drc",
            "drc_pass": True,
            "user_message": "check DRC",
        }
        result = node_session_synthesizer(state)
        assert "passed" in result["assistant_text"].lower()
        assert "no violation" in result["assistant_text"].lower()

    def test_check_drc_fail_with_flags(self):
        state = {
            "layout_session_decision": "check_drc",
            "drc_pass": False,
            "drc_flags": [
                {"description": "M1-M2 minimum spacing violation"},
                {"description": "M3-M4 overlap detected"},
            ],
            "user_message": "run DRC",
        }
        result = node_session_synthesizer(state)
        assert "2 issue" in result["assistant_text"]
        assert "spacing" in result["assistant_text"]


class TestSynthesizerRoutingDecision:
    def test_routing_with_target_nets(self):
        state = {
            "layout_session_decision": "optimize_routing",
            "routing_result": {"log_text": "HPWL=15.2um, 3 crossings"},
            "layout_session_target_nets": ["VOUTP", "VOUTN"],
            "user_message": "reduce parasitics on VOUTP and VOUTN",
        }
        result = node_session_synthesizer(state)
        text = result["assistant_text"]
        assert "VOUTP" in text
        assert "VOUTN" in text
        assert "symmetric" in text.lower()

    def test_check_routing_synthesized(self):
        state = {
            "layout_session_decision": "check_routing",
            "routing_result": {"summary": "All nets routed. HPWL=12.5um."},
            "user_message": "show routing",
        }
        result = node_session_synthesizer(state)
        assert "HPWL" in result["assistant_text"]

    def test_routing_previewer_specialist_synthesized(self):
        state = {
            "layout_session_specialist": "routing_previewer",
            "routing_result": {"log_text": "Routing check: HPWL=10.5um, crossings=1"},
            "user_message": "check routing",
        }
        result = node_session_synthesizer(state)
        text = result["assistant_text"]
        assert "HPWL" in text
        assert "delegate" not in text.lower()
        assert "routing_previewer" not in text


class TestSynthesizerChatHistoryIntegrity:
    def test_always_appends_user_and_assistant(self):
        state = {
            "layout_session_specialist": "topology_analyst",
            "Analysis_result": "2 diff pairs found.",
            "user_message": "analyze",
            "chat_history": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
        }
        result = node_session_synthesizer(state)
        history = result["chat_history"]
        # Should have original 2 + new user + new assistant = 4
        assert len(history) == 4
        assert history[-2]["role"] == "user"
        assert history[-1]["role"] == "assistant"


class TestSynthesizerFinalFallback:
    def test_unknown_specialist_gives_safe_message(self):
        state = {
            "layout_session_specialist": "nonexistent_agent",
        }
        result = node_session_synthesizer(state)
        assert "could not determine" in result["assistant_text"].lower()

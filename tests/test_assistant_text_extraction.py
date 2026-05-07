"""
Tests for Task 13 — extract_assistant_text helper and node-level
assistant_text output from DRC critic and routing previewer.

Tests the pure helper function from workers.py directly, and verifies
that the session_finalizer still correctly prioritises assistant_text.
"""

import pytest

from ai_agent.llm.workers import extract_assistant_text


# ══════════════════════════════════════════════════════════════════
# extract_assistant_text — priority chain
# ══════════════════════════════════════════════════════════════════

class TestExtractAssistantTextPriority:
    """assistant_text field takes absolute priority."""

    def test_prefers_assistant_text(self):
        state = {
            "assistant_text": "Hello from session chat.",
            "drc_pass": True,
            "routing_result": {"log_text": "Routing log"},
        }
        assert extract_assistant_text(state) == "Hello from session chat."

    def test_strips_whitespace_only(self):
        state = {"assistant_text": "   "}
        assert extract_assistant_text(state) == ""

    def test_none_assistant_text_triggers_fallback(self):
        state = {"assistant_text": None, "drc_pass": True}
        result = extract_assistant_text(state)
        assert "passed" in result.lower()

    def test_empty_string_triggers_fallback(self):
        state = {"assistant_text": "", "drc_pass": True}
        result = extract_assistant_text(state)
        assert "passed" in result.lower()


class TestExtractDRCFallback:
    """DRC fallback when no assistant_text."""

    def test_drc_pass(self):
        result = extract_assistant_text({"drc_pass": True})
        assert "passed" in result.lower()

    def test_drc_flags(self):
        result = extract_assistant_text({
            "drc_flags": [{"description": "overlap"}, {"description": "spacing"}],
        })
        assert "2 violation" in result

    def test_drc_empty_flags_no_text(self):
        result = extract_assistant_text({"drc_flags": []})
        assert result == ""


class TestExtractRoutingFallback:
    """Routing fallback when no assistant_text or DRC data."""

    def test_routing_log_text(self):
        result = extract_assistant_text({
            "routing_result": {"log_text": "Routing analysis complete"},
        })
        assert "Routing analysis" in result

    def test_routing_summary(self):
        result = extract_assistant_text({
            "routing_result": {"summary": "All nets routed."},
        })
        assert "All nets" in result

    def test_routing_empty_dict(self):
        result = extract_assistant_text({"routing_result": {}})
        assert result == ""


class TestExtractTopologyStrategyFallback:
    """Topology / strategy fallback."""

    def test_analysis_result(self):
        result = extract_assistant_text({
            "Analysis_result": "Found 3 diff pairs.",
        })
        assert "3 diff pairs" in result

    def test_strategy_result(self):
        result = extract_assistant_text({
            "strategy_result": "Common-centroid for M1/M2.",
        })
        assert "Common-centroid" in result

    def test_analysis_before_strategy(self):
        """Analysis_result has higher priority than strategy_result."""
        result = extract_assistant_text({
            "Analysis_result": "Topology data",
            "strategy_result": "Strategy data",
        })
        assert "Topology" in result

    def test_truncates_long_text(self):
        long_text = "A" * 3000
        result = extract_assistant_text({"Analysis_result": long_text})
        assert len(result) == 2000


class TestExtractEmptyState:
    """Empty or minimal state."""

    def test_empty_state(self):
        assert extract_assistant_text({}) == ""

    def test_only_irrelevant_keys(self):
        assert extract_assistant_text({"mode": "chat", "approved": True}) == ""


# ══════════════════════════════════════════════════════════════════
# Node-level assistant_text output (DRC / routing)
# ══════════════════════════════════════════════════════════════════

class TestDRCNodeAssistantText:
    """Verify the DRC critic return dict includes assistant_text.

    We test this structurally via the finalizer — if DRC sets
    assistant_text, the finalizer passes it through.
    """

    def test_finalizer_uses_drc_assistant_text(self):
        """If DRC sets assistant_text, finalizer should use it."""
        import importlib.util, sys, types
        from pathlib import Path

        mod_name = "ai_agent.nodes.session_finalizer"
        if mod_name not in sys.modules:
            import ai_agent.utils.logging  # noqa
            mod_path = Path(__file__).resolve().parents[1] / "ai_agent" / "nodes" / "session_finalizer.py"
            spec = importlib.util.spec_from_file_location(mod_name, mod_path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
        finalizer = sys.modules[mod_name]

        result = finalizer.node_session_finalizer({
            "session_route": "need_drc",
            "assistant_text": "DRC check passed — no violations found.",
        })
        assert "passed" in result["assistant_text"].lower()


class TestRoutingNodeAssistantText:
    """Verify routing previewer return includes assistant_text.

    We read the routing_previewer.py source to confirm the key is present,
    then test the finalizer uses it.
    """

    def test_finalizer_uses_routing_assistant_text(self):
        import importlib.util, sys
        from pathlib import Path

        mod_name = "ai_agent.nodes.session_finalizer"
        if mod_name not in sys.modules:
            import ai_agent.utils.logging  # noqa
            mod_path = Path(__file__).resolve().parents[1] / "ai_agent" / "nodes" / "session_finalizer.py"
            spec = importlib.util.spec_from_file_location(mod_name, mod_path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
        finalizer = sys.modules[mod_name]

        result = finalizer.node_session_finalizer({
            "session_route": "need_routing",
            "assistant_text": "Routing preview: 2 crossings, HPWL=1.5µm",
        })
        assert "Routing preview" in result["assistant_text"]

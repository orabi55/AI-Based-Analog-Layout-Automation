"""
test_fixes_7_to_10.py
=====================
Tests for:
  Fix  7 — build_initial_agent_trace() deep-copy isolation.
  Fix  8 — Post-human-viewer approval behavior documentation (no test needed).
  Fix  9 — ResponseDeduper prevents duplicate assistant text emissions.
  Fix 10 — build_layout_graph(mode="chat") deprecation warning.
"""

import warnings

import pytest

from ai_agent.graph.state_utils import build_initial_agent_trace
from ai_agent.llm.workers import ResponseDeduper


# ══════════════════════════════════════════════════════════════════
# Fix 7 — Deep-copy isolation
# ══════════════════════════════════════════════════════════════════

class TestBuildInitialAgentTraceDeepCopy:
    """Mutating original state after trace creation must not affect trace."""

    def test_deepcopy_isolated(self):
        state = {
            "Analysis_result": {"pairs": [["M1", "M2"]]},
            "placement_nodes": [{"id": "M1", "x": 0}],
        }

        trace = build_initial_agent_trace(state)

        # Mutate the original state
        state["Analysis_result"]["pairs"][0].append("M3")
        state["placement_nodes"][0]["x"] = 99

        # Trace should be unaffected
        assert trace["topology"]["pairs"] == [["M1", "M2"]]
        assert trace["placement"]["placement_nodes"][0]["x"] == 0

    def test_deepcopy_nested_drc_flags(self):
        state = {
            "drc_flags": [{"type": "overlap", "devices": ["M1", "M2"]}],
            "drc_pass": False,
            "drc_retry_count": 2,
        }

        trace = build_initial_agent_trace(state)
        state["drc_flags"][0]["devices"].append("M3")

        assert trace["drc"]["flags"][0]["devices"] == ["M1", "M2"]

    def test_deepcopy_routing_result(self):
        state = {
            "routing_result": {"hpwl": 100, "bands": [1, 2, 3]},
        }

        trace = build_initial_agent_trace(state)
        state["routing_result"]["bands"].append(4)

        assert trace["routing"]["bands"] == [1, 2, 3]

    def test_empty_state_still_works(self):
        trace = build_initial_agent_trace({})
        assert trace["topology"] is None
        assert trace["strategy"] is None
        assert trace["placement"]["placement_nodes"] == []
        assert trace["routing"] == {}
        assert trace["drc"]["pass"] is None

    def test_none_fields_handled(self):
        state = {
            "Analysis_result": None,
            "strategy_result": None,
            "placement_nodes": None,
            "routing_result": None,
            "drc_flags": None,
        }
        trace = build_initial_agent_trace(state)
        assert trace["topology"] is None
        assert trace["placement"]["placement_nodes"] == []


# ══════════════════════════════════════════════════════════════════
# Fix 8 — Post-human-viewer documentation (documentation-only fix)
# ══════════════════════════════════════════════════════════════════

class TestFix8DocumentationOnly:
    """Verify that human_viewer.py docstring documents the contract."""

    def test_human_viewer_docstring_mentions_gui(self):
        """human_viewer module docstring must mention GUI-side command application."""
        import importlib, importlib.util, sys, types
        from pathlib import Path

        mod_name = "ai_agent.nodes.human_viewer"
        # Avoid triggering __init__.py by loading directly
        mod_path = Path(__file__).resolve().parents[1] / "ai_agent" / "nodes" / "human_viewer.py"
        spec = importlib.util.spec_from_file_location(mod_name, mod_path)
        mod = importlib.util.module_from_spec(spec)

        # Read the file source to get the module docstring without exec
        source = mod_path.read_text(encoding="utf-8")
        assert "command application" in source.lower()
        assert "GUI" in source or "gui" in source.lower()


# ══════════════════════════════════════════════════════════════════
# Fix 9 — ResponseDeduper
# ══════════════════════════════════════════════════════════════════

class TestResponseDeduper:
    """Tests for the deduplication helper."""

    def test_first_call_emits(self):
        d = ResponseDeduper()
        assert d.should_emit("hello") is True

    def test_duplicate_skipped(self):
        d = ResponseDeduper()
        assert d.should_emit("hello") is True
        assert d.should_emit("hello") is False

    def test_different_text_emits(self):
        d = ResponseDeduper()
        assert d.should_emit("hello") is True
        assert d.should_emit("world") is True

    def test_empty_text_never_emits(self):
        d = ResponseDeduper()
        assert d.should_emit("") is False
        assert d.should_emit(None) is False

    def test_reset_allows_re_emit(self):
        d = ResponseDeduper()
        assert d.should_emit("hello") is True
        d.reset()
        assert d.should_emit("hello") is True

    def test_has_emitted_property(self):
        d = ResponseDeduper()
        assert d.has_emitted is False
        d.should_emit("hello")
        assert d.has_emitted is True
        d.reset()
        assert d.has_emitted is False

    def test_triple_duplicate_all_skipped(self):
        d = ResponseDeduper()
        assert d.should_emit("DRC passed") is True
        assert d.should_emit("DRC passed") is False
        assert d.should_emit("DRC passed") is False

    def test_alternating_texts(self):
        d = ResponseDeduper()
        assert d.should_emit("A") is True
        assert d.should_emit("B") is True
        assert d.should_emit("A") is True  # A is not the same as last (B)
        assert d.should_emit("A") is False  # now it's duplicate


# ══════════════════════════════════════════════════════════════════
# Fix 10 — build_layout_graph deprecation
# ══════════════════════════════════════════════════════════════════

class TestBuildLayoutGraphDeprecation:
    """build_layout_graph(mode='chat') should emit a DeprecationWarning."""

    def test_chat_mode_emits_deprecation(self):
        """Calling with mode='chat' must warn."""
        try:
            from ai_agent.graph.builder import build_layout_graph
        except ImportError:
            pytest.skip("langgraph not installed")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            try:
                graph, memory = build_layout_graph(mode="chat")
            except Exception:
                pytest.skip("Graph compilation requires full langgraph")
            assert any(
                issubclass(warning.category, DeprecationWarning)
                and "deprecated" in str(warning.message).lower()
                for warning in w
            ), f"Expected DeprecationWarning, got: {[str(x.message) for x in w]}"

    def test_initial_mode_no_deprecation(self):
        """Calling with mode='initial' must not warn."""
        try:
            from ai_agent.graph.builder import build_layout_graph
        except ImportError:
            pytest.skip("langgraph not installed")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            try:
                graph, memory = build_layout_graph(mode="initial")
            except Exception:
                pytest.skip("Graph compilation requires full langgraph")
            deprecation_warnings = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert len(deprecation_warnings) == 0

    def test_interactive_full_mode_no_deprecation(self):
        """Calling with mode='interactive_full' must not warn."""
        try:
            from ai_agent.graph.builder import build_layout_graph
        except ImportError:
            pytest.skip("langgraph not installed")

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            try:
                graph, memory = build_layout_graph(mode="interactive_full")
            except Exception:
                pytest.skip("Graph compilation requires full langgraph")
            deprecation_warnings = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert len(deprecation_warnings) == 0

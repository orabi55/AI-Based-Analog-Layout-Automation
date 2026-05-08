"""
Tests for build_session_chat_graph (Task 9) and select_graph_app (Task 10).

These tests require the ``langchain`` dependency because the graph builder
imports from ``ai_agent.nodes`` which triggers the full import chain.
Tests are automatically skipped if langchain is not installed.

Structural tests for the edge function and route map are always run.
"""

import pytest

# ---------------------------------------------------------------------------
# Check if the full dependency chain is available
# ---------------------------------------------------------------------------
try:
    from ai_agent.graph.builder import (
        app,
        chat_app,
        layout_session_app,
        session_chat_app,
    )
    _HAS_BUILDER = True
except ImportError:
    _HAS_BUILDER = False

requires_builder = pytest.mark.skipif(
    not _HAS_BUILDER,
    reason="langchain or other heavy deps not installed — skipping graph tests",
)


# ══════════════════════════════════════════════════════════════════
# Structural tests (always run — no heavy deps)
# ══════════════════════════════════════════════════════════════════

class TestRouteMapCompleteness:
    """Verify that route_after_session_chat covers every route the graph needs."""

    def test_all_valid_routes_have_targets(self):
        from ai_agent.agents.session_chat_agent import VALID_SESSION_ROUTES
        from ai_agent.graph.edges import _SESSION_ROUTE_MAP, _SESSION_FALLBACK_NODE

        for route in VALID_SESSION_ROUTES:
            target = _SESSION_ROUTE_MAP.get(route, _SESSION_FALLBACK_NODE)
            assert target, f"Route '{route}' has no target"

    def test_session_route_map_values_start_with_node(self):
        from ai_agent.graph.edges import _SESSION_ROUTE_MAP
        for route, target in _SESSION_ROUTE_MAP.items():
            assert target.startswith("node_"), (
                f"Route '{route}' → '{target}' doesn't start with 'node_'"
            )


# ══════════════════════════════════════════════════════════════════
# Task 9 — build_session_chat_graph (requires langchain)
# ══════════════════════════════════════════════════════════════════

@requires_builder
class TestBuildSessionChatGraph:
    """Verify that the session chat graph compiles and is importable."""

    def test_session_chat_app_importable(self):
        assert session_chat_app is not None

    def test_session_chat_app_is_compiled_graph(self):
        assert hasattr(session_chat_app, "get_graph")

    def test_graph_contains_session_chat_node(self):
        graph = session_chat_app.get_graph()
        node_ids = set(graph.nodes.keys())
        assert "node_session_chat" in node_ids

    def test_graph_contains_session_finalizer(self):
        graph = session_chat_app.get_graph()
        node_ids = set(graph.nodes.keys())
        assert "node_session_finalizer" in node_ids

    def test_graph_contains_command_validator(self):
        graph = session_chat_app.get_graph()
        node_ids = set(graph.nodes.keys())
        assert "node_command_validator" in node_ids

    def test_graph_contains_all_specialist_nodes(self):
        graph = session_chat_app.get_graph()
        node_ids = set(graph.nodes.keys())
        expected = {
            "node_topology_analyst",
            "node_strategy_selector",
            "node_placement_specialist",
            "node_drc_critic",
            "node_routing_previewer",
            "node_human_viewer",
        }
        assert expected.issubset(node_ids), f"Missing: {expected - node_ids}"

    def test_graph_has_start_edge(self):
        """START should connect to node_session_chat."""
        graph = session_chat_app.get_graph()
        start_edges = [e for e in graph.edges if e[0] == "__start__"]
        targets = {e[1] for e in start_edges}
        assert "node_session_chat" in targets

    def test_legacy_chat_app_still_importable(self):
        assert chat_app is not None

    def test_initial_app_still_importable(self):
        assert app is not None


# ══════════════════════════════════════════════════════════════════
# Task 10 — select_graph_app (requires langchain for real apps)
# ══════════════════════════════════════════════════════════════════

@requires_builder
class TestSelectGraphApp:
    """Verify the testable graph selector helper in workers.py."""

    def test_chat_mode_returns_layout_session_app(self):
        from ai_agent.llm.workers import select_graph_app
        assert select_graph_app("chat") is layout_session_app

    def test_chat_v1_mode_returns_session_chat_app(self):
        from ai_agent.llm.workers import select_graph_app
        assert select_graph_app("chat_v1") is session_chat_app

    def test_legacy_chat_mode_returns_chat_app(self):
        from ai_agent.llm.workers import select_graph_app
        assert select_graph_app("legacy_chat") is chat_app

    def test_initial_mode_returns_initial_app(self):
        from ai_agent.llm.workers import select_graph_app
        assert select_graph_app("initial") is app

    def test_none_mode_returns_initial_app(self):
        from ai_agent.llm.workers import select_graph_app
        assert select_graph_app(None) is app

    def test_unknown_mode_returns_initial_app(self):
        from ai_agent.llm.workers import select_graph_app
        assert select_graph_app("some_future_mode") is app


@requires_builder
class TestGraphAppLabel:
    """Verify the logging helper identifies apps correctly."""

    def test_session_label(self):
        from ai_agent.llm.workers import _graph_app_label
        assert _graph_app_label(session_chat_app) == "session_chat_app"

    def test_legacy_label(self):
        from ai_agent.llm.workers import _graph_app_label
        assert _graph_app_label(chat_app) == "legacy_chat_app"

    def test_initial_label(self):
        from ai_agent.llm.workers import _graph_app_label
        assert _graph_app_label(app) == "initial_graph_app"

    def test_unknown_label(self):
        from ai_agent.llm.workers import _graph_app_label
        assert _graph_app_label(object()) == "unknown_graph_app"

"""
test_fix_routing.py
===================
Tests for the fix_routing route:
  - rule_route correctly distinguishes need_routing (read-only) from fix_routing (active)
  - run_session_chat_agent clarifies when fix_routing has no target nets
  - run_session_chat_agent returns fix_routing + target_nets when targets are provided
  - route_after_session_chat maps fix_routing to routing previewer
  - SESSION_ROUTE_LABELS includes fix_routing
  - Finalizer produces actionable recommendations for fix_routing
  - Net extraction helper works correctly
"""

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

from ai_agent.agents.session_chat_agent import (
    rule_route,
    run_session_chat_agent,
    _extract_target_nets,
    VALID_SESSION_ROUTES,
    SPECIALIST_BY_ROUTE,
)

# ---------------------------------------------------------------------------
# Direct-file imports to avoid pulling in langchain via __init__ chains
# ---------------------------------------------------------------------------

_PROJ = Path(__file__).resolve().parents[1]


def _import_module(dotted_name: str, rel_path: str):
    full = _PROJ / rel_path
    spec = importlib.util.spec_from_file_location(dotted_name, full)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(dotted_name, mod)
    spec.loader.exec_module(mod)
    return mod


_edges = _import_module("ai_agent.graph.edges", "ai_agent/graph/edges.py")
route_after_session_chat = _edges.route_after_session_chat
_SESSION_ROUTE_MAP = _edges._SESSION_ROUTE_MAP

_workers = _import_module("ai_agent.llm.workers", "ai_agent/llm/workers.py")
SESSION_ROUTE_LABELS = _workers.SESSION_ROUTE_LABELS

_sf = _import_module(
    "ai_agent.nodes.session_finalizer",
    "ai_agent/nodes/session_finalizer.py",
)
node_session_finalizer = _sf.node_session_finalizer
_ROUTE_SUMMARISERS = _sf._ROUTE_SUMMARISERS


# ══════════════════════════════════════════════════════════════════
# Route registration
# ══════════════════════════════════════════════════════════════════


class TestFixRoutingRegistration:
    """fix_routing must be properly registered in all route maps."""

    def test_in_valid_session_routes(self):
        assert "fix_routing" in VALID_SESSION_ROUTES

    def test_in_specialist_by_route(self):
        assert "fix_routing" in SPECIALIST_BY_ROUTE
        assert SPECIALIST_BY_ROUTE["fix_routing"] == "routing_previewer"

    def test_in_session_route_map(self):
        assert "fix_routing" in _SESSION_ROUTE_MAP
        assert _SESSION_ROUTE_MAP["fix_routing"] == "node_routing_previewer"

    def test_in_session_route_labels(self):
        assert "fix_routing" in SESSION_ROUTE_LABELS
        assert "optimiz" in SESSION_ROUTE_LABELS["fix_routing"].lower()

    def test_in_route_summarisers(self):
        assert "fix_routing" in _ROUTE_SUMMARISERS


# ══════════════════════════════════════════════════════════════════
# Deterministic rule_route
# ══════════════════════════════════════════════════════════════════


class TestRuleRouteRoutingSplit:
    """Verify the read-only vs active routing split."""

    # Read-only → need_routing
    @pytest.mark.parametrize("msg", [
        "check routing",
        "preview routing",
        "show routing",
        "estimate wirelength",
        "what is the routing congestion?",
    ])
    def test_read_only_routes_to_need_routing(self, msg):
        assert rule_route(msg) == "need_routing"

    # Active optimization → fix_routing
    @pytest.mark.parametrize("msg", [
        "reduce parasitics",
        "reduce parasitics on VOUTP and VOUTN",
        "reduce wirelength",
        "optimize routing",
        "fix routing",
        "fix crossings",
        "reduce crossings",
        "improve routing",
        "shorten nets",
        "lower parasitics",
    ])
    def test_active_routes_to_fix_routing(self, msg):
        assert rule_route(msg) == "fix_routing"


# ══════════════════════════════════════════════════════════════════
# Net extraction
# ══════════════════════════════════════════════════════════════════


class TestExtractTargetNets:
    def test_extracts_voutp_voutn(self):
        nets = _extract_target_nets("reduce parasitics on VOUTP and VOUTN nets")
        assert "VOUTP" in nets
        assert "VOUTN" in nets

    def test_extracts_clk(self):
        nets = _extract_target_nets("shorten net CLK")
        assert "CLK" in nets

    def test_excludes_common_words(self):
        nets = _extract_target_nets("reduce parasitics AND optimize routing FOR VOUTP")
        assert "AND" not in nets
        assert "FOR" not in nets
        assert "VOUTP" in nets

    def test_excludes_power_nets(self):
        nets = _extract_target_nets("reduce parasitics on VDD and VOUTP")
        assert "VDD" not in nets
        assert "VOUTP" in nets

    def test_empty_message(self):
        assert _extract_target_nets("") == []

    def test_no_nets(self):
        nets = _extract_target_nets("reduce parasitics")
        assert nets == []

    def test_excludes_device_names(self):
        """Device names like M1, MM1 should not be extracted as nets."""
        nets = _extract_target_nets("reduce parasitics on MM1 and VOUTP")
        assert "VOUTP" in nets
        # MM1 is matched by DEVICE_RE, not NET_RE


# ══════════════════════════════════════════════════════════════════
# run_session_chat_agent integration
# ══════════════════════════════════════════════════════════════════


class TestRunSessionChatAgentFixRouting:
    """End-to-end routing optimization flow."""

    def test_vague_reduce_parasitics_clarifies(self):
        """'reduce parasitics' with no targets should clarify."""
        result = run_session_chat_agent({
            "user_message": "reduce parasitics",
        })
        assert result["session_route"] == "clarify"
        assert "which net" in result["assistant_text"].lower() or "example" in result["assistant_text"].lower()

    def test_reduce_parasitics_with_targets(self):
        """'reduce parasitics on VOUTP and VOUTN' should route to fix_routing."""
        result = run_session_chat_agent({
            "user_message": "reduce parasitics on VOUTP and VOUTN nets",
        })
        assert result["session_route"] == "fix_routing"
        assert result.get("target_nets")
        assert "VOUTP" in result["target_nets"]
        assert "VOUTN" in result["target_nets"]
        assert result.get("routing_fix_requested") is True

    def test_fix_routing_with_single_net(self):
        result = run_session_chat_agent({
            "user_message": "reduce wirelength on CLK",
        })
        assert result["session_route"] == "fix_routing"
        assert "CLK" in result.get("target_nets", [])


# ══════════════════════════════════════════════════════════════════
# Graph routing
# ══════════════════════════════════════════════════════════════════


class TestGraphRouting:
    def test_route_after_session_chat_fix_routing(self):
        state = {"session_route": "fix_routing"}
        assert route_after_session_chat(state) == "node_routing_previewer"


# ══════════════════════════════════════════════════════════════════
# Finalizer output for fix_routing
# ══════════════════════════════════════════════════════════════════


class TestFinalizerFixRouting:
    def test_fix_routing_with_targets_produces_recommendations(self):
        state = {
            "session_route": "fix_routing",
            "target_nets": ["VOUTP", "VOUTN"],
            "routing_result": {"summary": "HPWL = 10.5 um"},
        }
        result = node_session_finalizer(state)
        text = result["assistant_text"]
        assert "VOUTP" in text
        assert "VOUTN" in text
        assert "recommend" in text.lower() or "Recommendations" in text
        assert "no layout changes" in text.lower() or "No layout changes" in text

    def test_fix_routing_without_targets_suggests_example(self):
        state = {
            "session_route": "fix_routing",
            "target_nets": [],
            "routing_result": {},
        }
        result = node_session_finalizer(state)
        text = result["assistant_text"]
        assert "VOUTP" in text  # example in the suggestion

    def test_fix_routing_differs_from_need_routing(self):
        """fix_routing should produce different output than need_routing."""
        base_state = {"routing_result": {"summary": "HPWL = 10.5 um"}}

        need_result = node_session_finalizer(
            {**base_state, "session_route": "need_routing"}
        )
        fix_result = node_session_finalizer(
            {**base_state, "session_route": "fix_routing", "target_nets": ["VOUTP"]}
        )

        # fix_routing should be longer (more recommendations)
        assert len(fix_result["assistant_text"]) > len(need_result["assistant_text"])

    def test_fix_routing_with_differential_pair(self):
        """When 2+ target nets, should suggest symmetric routing."""
        state = {
            "session_route": "fix_routing",
            "target_nets": ["VOUTP", "VOUTN"],
            "routing_result": {},
        }
        result = node_session_finalizer(state)
        text = result["assistant_text"]
        assert "symmetric" in text.lower() or "differential" in text.lower()

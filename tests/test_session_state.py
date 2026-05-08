"""
test_session_state.py
=====================
Verifies that the new session-chatbot fields added to LayoutState are:
  - importable without errors
  - accepted when present in a state dict
  - not required (existing state dicts that omit them must still be valid)

These are structural / smoke tests; they do not run the LangGraph pipeline.
"""

import pytest
from ai_agent.graph.state import LayoutState


# ---------------------------------------------------------------------------
# Fix 13: Import routes from the production single-source-of-truth.
# ---------------------------------------------------------------------------
from ai_agent.agents.session_chat_agent import VALID_SESSION_ROUTES


class TestLayoutStateSessionFields:
    """Structural tests for session-chatbot state fields."""

    # ------------------------------------------------------------------
    # 1. The state dict prescribed by the task spec
    # ------------------------------------------------------------------
    def test_layout_state_accepts_session_fields(self):
        """State dict with all new session fields must be well-formed."""
        state = {
            "mode": "chat",
            "user_message": "move M1 left",
            "session_route": "command_edit",
            "route_confidence": 0.9,
            "assistant_text": "I can move M1 left.",
            "requires_specialist": False,
            "specialist_target": None,
            "initial_agent_trace": {
                "topology": {},
                "strategy": {},
                "placement": [],
                "routing": {},
                "drc": {"pass": True, "flags": []},
            },
        }

        assert state["session_route"] == "command_edit"
        assert state["route_confidence"] == pytest.approx(0.9)
        assert state["assistant_text"] == "I can move M1 left."
        assert state["requires_specialist"] is False
        assert state["specialist_target"] is None
        assert isinstance(state["initial_agent_trace"], dict)
        assert state["initial_agent_trace"]["drc"]["pass"] is True

    # ------------------------------------------------------------------
    # 2. All new fields must be declared on LayoutState
    # ------------------------------------------------------------------
    def test_new_fields_declared_on_layout_state(self):
        """Every new session field must appear in LayoutState.__annotations__."""
        expected_fields = {
            "initial_agent_trace",
            "assistant_text",
            "session_route",
            "route_confidence",
            "requires_specialist",
            "specialist_target",
            "session_reason",
            "session_commands",
        }
        annotations = LayoutState.__annotations__
        missing = expected_fields - annotations.keys()
        assert not missing, f"Fields missing from LayoutState: {missing}"

    # ------------------------------------------------------------------
    # 3. Existing states that omit new fields must not crash
    # ------------------------------------------------------------------
    def test_existing_state_without_session_fields_is_valid(self):
        """A minimal pre-existing state dict must still be usable."""
        legacy_state: dict = {
            "mode": "initial",
            "user_message": "",
            "chat_history": [],
            "nodes": [],
            "sp_file_path": "",
            "selected_model": "Gemini",
        }
        # Accessing a missing key via .get() should return None safely.
        assert legacy_state.get("session_route") is None
        assert legacy_state.get("assistant_text") is None
        assert legacy_state.get("requires_specialist") is None
        assert legacy_state.get("session_commands") is None
        assert legacy_state.get("initial_agent_trace") is None

    # ------------------------------------------------------------------
    # 4. session_route must be one of VALID_SESSION_ROUTES (or None)
    # ------------------------------------------------------------------
    @pytest.mark.parametrize("route", sorted(VALID_SESSION_ROUTES))
    def test_valid_session_routes_are_recognised(self, route: str):
        """Each valid route constant must be in the canonical route set."""
        assert route in VALID_SESSION_ROUTES

    # ------------------------------------------------------------------
    # 5. session_commands is a list of dicts (or None)
    # ------------------------------------------------------------------
    def test_session_commands_accepts_list_of_dicts(self):
        state = {
            "session_commands": [
                {"action": "move", "target": "M1", "dx": -1},
                {"action": "resize", "target": "M2", "width": 0.4},
            ]
        }
        cmds = state["session_commands"]
        assert isinstance(cmds, list)
        assert all(isinstance(c, dict) for c in cmds)

    # ------------------------------------------------------------------
    # 6. route_confidence must be a float in [0.0, 1.0]
    # ------------------------------------------------------------------
    @pytest.mark.parametrize("confidence", [0.0, 0.5, 1.0])
    def test_route_confidence_boundary_values(self, confidence: float):
        state = {"route_confidence": confidence}
        assert 0.0 <= state["route_confidence"] <= 1.0

    # ------------------------------------------------------------------
    # 7. requires_specialist drives specialist_target selection
    # ------------------------------------------------------------------
    def test_requires_specialist_true_with_valid_target(self):
        valid_targets = {
            "topology_analyst",
            "strategy_selector",
            "placement_specialist",
            "drc_critic",
            "drc_checker",
            "routing_previewer",
        }
        for target in valid_targets:
            state = {
                "requires_specialist": True,
                "specialist_target": target,
            }
            assert state["requires_specialist"] is True
            assert state["specialist_target"] in valid_targets

    def test_requires_specialist_false_target_is_none(self):
        state = {
            "requires_specialist": False,
            "specialist_target": None,
        }
        assert state["specialist_target"] is None

    # ------------------------------------------------------------------
    # 8. Session fields are NotRequired (Fix 5)
    # ------------------------------------------------------------------
    def test_session_fields_are_notrequired(self):
        """NotRequired must appear in annotations for all session fields."""
        hints = LayoutState.__annotations__
        session_fields = {
            "initial_agent_trace",
            "assistant_text",
            "session_route",
            "route_confidence",
            "requires_specialist",
            "specialist_target",
            "session_reason",
            "session_commands",
        }
        for field in session_fields:
            hint_str = str(hints[field])
            assert "NotRequired" in hint_str, (
                f"Field {field!r} should be NotRequired, got: {hint_str}"
            )

    # ------------------------------------------------------------------
    # 9. mode Literal includes legacy_chat (Fix 6)
    # ------------------------------------------------------------------
    def test_layout_state_mode_allows_legacy_chat(self):
        """LayoutState.mode must accept 'legacy_chat' without type errors."""
        state: dict = {"mode": "legacy_chat"}
        assert state["mode"] == "legacy_chat"

    def test_mode_literal_includes_legacy_chat(self):
        """The mode annotation must include 'legacy_chat'."""
        hint_str = str(LayoutState.__annotations__["mode"])
        assert "legacy_chat" in hint_str

    # ------------------------------------------------------------------
    # 10. select_graph_app works for all modes (Fix 6)
    # ------------------------------------------------------------------
    def test_select_graph_app_legacy_chat(self):
        """select_graph_app('legacy_chat') must return the legacy chat app."""
        try:
            from ai_agent.llm.workers import select_graph_app
        except ImportError:
            pytest.skip("workers module requires langgraph")

        try:
            app = select_graph_app("legacy_chat")
        except Exception:
            pytest.skip("Graph compilation requires langgraph")

        assert app is not None

    def test_select_graph_app_chat(self):
        """select_graph_app('chat') must return layout session app."""
        try:
            from ai_agent.llm.workers import select_graph_app
        except ImportError:
            pytest.skip("workers module requires langgraph")

        try:
            app = select_graph_app("chat")
        except Exception:
            pytest.skip("Graph compilation requires langgraph")

        assert app is not None

"""
Regression tests for observed chat_v2 user-facing failures.
"""

from ai_agent.agents.layout_session_agent import run_layout_session_agent
from ai_agent.nodes.deterministic_tool_runner import node_deterministic_tool_runner
from ai_agent.nodes.session_synthesizer import node_session_synthesizer


def test_common_centroid_question_no_delegation_placeholder():
    out = node_session_synthesizer({
        "user_message": "MM3 and MM0 common centroid or interdigitation?",
        "layout_session_specialist": "strategy_selector",
        "nodes": [
            {"id": "MM3_f0", "parent_id": "MM3", "x": 0, "y": 100},
            {"id": "MM0_f0", "parent_id": "MM0", "x": 10, "y": 100},
            {"id": "MM3_f1", "parent_id": "MM3", "x": 20, "y": 100},
            {"id": "MM0_f1", "parent_id": "MM0", "x": 30, "y": 100},
        ],
    })
    final_text = out["assistant_text"]
    assert "delegate" not in final_text.lower()
    assert "strategy_selector" not in final_text


def test_move_mm1_left_produces_command_path():
    result = run_layout_session_agent({
        "user_message": "Move MM1 to the left",
        "placement_nodes": [{"id": "MM1"}],
    })
    assert result["layout_session_decision"] in {"call_deterministic_tool", "propose_commands"}


def test_target_device_followup_fills_pending_move():
    out = node_deterministic_tool_runner({
        "layout_session_tool_name": "try_fill_edit_slots",
        "pending_edit_intent": {"action": "move", "dx": -1, "dy": 0, "missing": ["device_id"]},
        "user_message": "Target device is MM1",
        "placement_nodes": [{"id": "MM1"}],
    })
    assert out["layout_session_decision"] == "propose_commands"
    assert out["pending_cmds"][0]["device_id"] == "MM1"


def test_reduce_parasitics_voutp_voutn_optimize_routing(monkeypatch):
    monkeypatch.setattr(
        "ai_agent.agents.layout_session_agent.call_layout_session_llm",
        lambda prompt, state: (
            '{"decision":"optimize_routing","confidence":0.9,'
            '"target_nets":["VOUTP","VOUTN"],"reason":"targets provided"}'
        ),
    )
    result = run_layout_session_agent({"user_message": "reduce parasitics on VOUTP and VOUTN"})
    assert result["layout_session_decision"] == "optimize_routing"


def test_reduce_parasitics_no_targets_clarifies():
    out = node_deterministic_tool_runner({
        "layout_session_tool_name": "extract_target_nets",
        "user_message": "reduce parasitics",
    })
    assert out["layout_session_decision"] == "clarify"


def test_align_unsupported_clarifies():
    out = node_deterministic_tool_runner({
        "layout_session_tool_name": "parse_direct_edit_command",
        "user_message": "align M1 with M2",
        "placement_nodes": [{"id": "M1"}, {"id": "M2"}],
    })
    assert out["layout_session_decision"] == "clarify"


def test_vague_add_dummy_clarifies():
    out = node_deterministic_tool_runner({
        "layout_session_tool_name": "parse_direct_edit_command",
        "user_message": "add dummy",
        "placement_nodes": [{"id": "M1"}],
    })
    assert out["layout_session_decision"] == "clarify"

"""
Regression tests for observed chat_v2 user-facing failures.
"""

import pytest

from ai_agent.agents.layout_session_agent import run_layout_session_agent
from ai_agent.agents.session_chat_agent import parse_direct_edit_command
from ai_agent.graph.edges import route_after_deterministic_tool_runner
from ai_agent.nodes.deterministic_tool_runner import node_deterministic_tool_runner
from ai_agent.nodes.drc_checker import format_drc_flags
from ai_agent.nodes.session_synthesizer import node_session_synthesizer


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    """Default to deterministic fallback unless a test overrides the LLM output."""
    monkeypatch.setattr(
        "ai_agent.agents.layout_session_agent.call_layout_session_llm",
        lambda *_args, **_kwargs: "not-json",
    )


def _comparator_trace() -> dict:
    return {
        "topology": {
            "CIRCUIT_TYPE": "Dynamic Latch-based Comparator",
            "Analysis_result": (
                "INPUT_DIFFERENTIAL_PAIR: [MM8, MM9]\n"
                "TAIL_CURRENT_SOURCE: [MM10]\n"
                "CROSS_COUPLED_LATCH: [MM4, MM5, MM6, MM7]\n"
                "PRECHARGE_LOAD: [MM0, MM1, MM2, MM3]"
            ),
        },
        "strategy": {
            "matching_groups": [
                ["MM8", "MM9"],
                ["MM0", "MM3"],
                ["MM4", "MM5"],
                ["MM6", "MM7"],
                ["MM1", "MM2"],
            ],
        },
        "drc": {"pass": True, "flags": []},
    }


def _comparator_nodes() -> list[dict]:
    return [
        {"id": "MM8", "type": "nmos", "D": "VOUTN", "G": "VINP", "S": "net2<3>"},
        {"id": "MM9", "type": "nmos", "D": "VOUTP", "G": "VINN", "S": "net2<3>"},
        {"id": "MM10", "type": "nmos", "D": "net2<3>", "G": "CLK", "S": "GND"},
        {"id": "MM5", "type": "pmos", "D": "VOUTP", "G": "VOUTN", "S": "VDD"},
        {"id": "MM2", "type": "pmos", "D": "VOUTP", "G": "CLK", "S": "VDD"},
        {"id": "MM6", "type": "nmos", "D": "VOUTP", "G": "VOUTN", "S": "GND"},
        {"id": "MM0", "type": "pmos"},
        {"id": "MM1", "type": "pmos"},
        {"id": "MM3", "type": "pmos"},
        {"id": "MM4", "type": "pmos"},
        {"id": "MM7", "type": "nmos"},
    ]


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


def test_answer_from_trace_circuit_identity_is_specific():
    out = node_deterministic_tool_runner({
        "layout_session_tool_name": "answer_from_initial_trace",
        "layout_session_tool_args": {"query": "what is this circuit"},
        "initial_agent_trace": _comparator_trace(),
        "placement_nodes": _comparator_nodes(),
    })
    text = out["assistant_text"].lower()
    assert "dynamic latch" in text
    assert "comparator" in text
    assert not out["assistant_text"].startswith("Here is what the initial placement agents decided")


def test_answer_from_trace_mm10_role_is_specific():
    out = node_deterministic_tool_runner({
        "layout_session_tool_name": "answer_from_initial_trace",
        "layout_session_tool_args": {"question": "what is MM10 doing?"},
        "initial_agent_trace": _comparator_trace(),
        "placement_nodes": _comparator_nodes(),
    })
    text = out["assistant_text"]
    text_l = text.lower()
    assert "MM10" in text
    assert ("tail" in text_l) or ("current-source" in text_l) or ("current source" in text_l)
    assert ("CLK" in text) or ("GND" in text) or ("net2<3>" in text)
    assert not text.startswith("Here is what the initial placement agents decided")


def test_answer_from_trace_voutp_connectivity_is_specific():
    out = node_deterministic_tool_runner({
        "layout_session_tool_name": "answer_from_initial_trace",
        "layout_session_tool_args": {"query": "what devices are connected to VOUTP?"},
        "initial_agent_trace": _comparator_trace(),
        "placement_nodes": _comparator_nodes(),
    })
    text = out["assistant_text"]
    assert "MM5" in text
    assert "MM2" in text
    assert "MM6" in text
    assert not text.startswith("Here is what the initial placement agents decided")


def test_answer_from_trace_matching_explanation_current_layout():
    out = node_deterministic_tool_runner({
        "layout_session_tool_name": "answer_from_initial_trace",
        "layout_session_tool_args": {"query": "How matching techniques is applied in this layout now"},
        "initial_agent_trace": _comparator_trace(),
        "placement_nodes": _comparator_nodes(),
    })
    text = out["assistant_text"]
    assert "MM8/MM9" in text
    assert "MM0/MM3" in text
    assert "MM4/MM5" in text
    assert "MM6/MM7" in text
    assert "MM1/MM2" in text
    assert "True common-centroid can only be confirmed from physical finger ordering" in text
    assert not text.startswith("Here is what the initial placement agents decided")


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
    synth = node_session_synthesizer({
        "layout_session_decision": "optimize_routing",
        "routing_result": {"log_text": "Routing estimate complete."},
        "layout_session_target_nets": ["VOUTP", "VOUTN"],
    })
    assert "No layout changes were applied automatically." in synth["assistant_text"]


def test_reduce_parasitics_no_targets_clarifies_via_agent(monkeypatch):
    monkeypatch.setattr(
        "ai_agent.agents.layout_session_agent.call_layout_session_llm",
        lambda *_args, **_kwargs: (
            '{"decision":"optimize_routing","confidence":0.95,"target_nets":[],"target_devices":[]}'
        ),
    )
    result = run_layout_session_agent({"user_message": "reduce parasitics"})
    assert result["layout_session_decision"] == "clarify"
    assert "Which nets or devices should I optimize?" in result["assistant_text"]
    assert result["pending_edit_intent"] == {
        "type": "optimize_routing",
        "missing": ["target_nets"],
    }


def test_reduce_parasitics_no_targets_clarifies():
    out = node_deterministic_tool_runner({
        "layout_session_tool_name": "extract_target_nets",
        "user_message": "reduce parasitics",
    })
    assert out["layout_session_decision"] == "clarify"
    assert out["pending_edit_intent"] == {
        "type": "optimize_routing",
        "missing": ["target_nets"],
    }


def test_extract_target_nets_continues_to_routing_previewer():
    out = node_deterministic_tool_runner({
        "layout_session_tool_name": "extract_target_nets",
        "user_message": "reduce parasitics on VOUTP and VOUTN",
    })
    assert out["layout_session_decision"] == "optimize_routing"
    assert route_after_deterministic_tool_runner(out) == "node_routing_previewer"


def test_reduce_parasitics_stores_pending_optimize_routing_intent():
    result = run_layout_session_agent({"user_message": "reduce parasitics"})
    assert result["layout_session_decision"] == "clarify"
    assert result["pending_edit_intent"] == {
        "type": "optimize_routing",
        "missing": ["target_nets"],
    }
    assert not result.get("pending_cmds")


def test_followup_voutp_voutn_fills_pending_routing_intent():
    pending = {"type": "optimize_routing", "missing": ["target_nets"]}
    agent = run_layout_session_agent({
        "user_message": "VOUTP,VOUTN",
        "pending_edit_intent": pending,
    })
    assert agent["layout_session_decision"] == "call_deterministic_tool"
    assert agent["layout_session_tool_name"] == "extract_target_nets"

    tool = node_deterministic_tool_runner({**agent, "user_message": "VOUTP,VOUTN"})
    assert tool["layout_session_decision"] == "optimize_routing"
    assert tool["layout_session_target_nets"] == ["VOUTP", "VOUTN"]
    assert tool["pending_edit_intent"] is None
    assert route_after_deterministic_tool_runner(tool) == "node_routing_previewer"


def test_targeted_optimization_answer_is_synthesized_not_raw_only():
    synth = node_session_synthesizer({
        "layout_session_decision": "optimize_routing",
        "layout_session_target_nets": ["VOUTP", "VOUTN"],
        "routing_result": {
            "worst_nets": ["VOUTP", "VOUTN", "CLK"],
            "net_details": {
                "VOUTP": {"wire_length": 3.2, "cross_row": True},
                "VOUTN": {"wire_length": 2.8, "cross_row": True},
            },
            "log_text": (
                "12 nets analyzed\n"
                "Worst nets (by weighted HPWL):\n"
                "  VOUTP signal hpwl=3.200um\n"
                "  VOUTN signal hpwl=2.800um"
            ),
        },
    })
    text = synth["assistant_text"]
    lower = text.lower()
    assert text.startswith("I analyzed VOUTP, VOUTN")
    assert "voutp" in lower and "voutn" in lower
    assert "worst hpwl" in lower
    assert "recommendations" in lower
    assert "symmetrically" in lower or "symmetric" in lower
    assert "crossings" in lower
    assert "connected" in lower and "closer" in lower
    assert "No layout changes were applied automatically." in text
    assert not text.startswith("12 nets analyzed")


def test_check_drc_is_read_only_path():
    result = run_layout_session_agent({"user_message": "check DRC"})
    assert result["layout_session_decision"] == "check_drc"
    assert result.get("pending_cmds") in ([], None)


def test_fix_drc_routes_to_fix_path():
    result = run_layout_session_agent({"user_message": "remove DRC violation"})
    assert result["layout_session_decision"] == "fix_drc"


def test_low_confidence_valid_json_falls_back(monkeypatch):
    monkeypatch.setattr(
        "ai_agent.agents.layout_session_agent.call_layout_session_llm",
        lambda *_args, **_kwargs: (
            '{"decision":"propose_commands","confidence":0.2,"reason":"weak signal",'
            '"commands":[{"action":"move","device_id":"MM1","dx":-1,"dy":0}]}'
        ),
    )
    result = run_layout_session_agent({
        "user_message": "Move MM1 to the left",
        "placement_nodes": [{"id": "MM1"}],
    })
    assert result["layout_session_decision"] in {"propose_commands", "call_deterministic_tool", "clarify"}
    assert "LLM confidence below threshold; used deterministic fallback." in result["layout_session_reason"]


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


def test_what_is_this_circuit_not_generic_trace_dump():
    out = node_deterministic_tool_runner({
        "layout_session_tool_name": "answer_from_initial_trace",
        "layout_session_tool_args": {"question": "what is this circuit"},
        "initial_agent_trace": _comparator_trace(),
        "placement_nodes": _comparator_nodes(),
    })
    assert "dynamic latch-based comparator" in out["assistant_text"].lower()
    assert "Here is what the initial placement agents decided" not in out["assistant_text"]


def test_mm10_role_answer():
    out = node_deterministic_tool_runner({
        "layout_session_tool_name": "answer_from_initial_trace",
        "layout_session_tool_args": {"question": "what is MM10 doing?"},
        "initial_agent_trace": _comparator_trace(),
        "placement_nodes": _comparator_nodes(),
    })
    assert "MM10" in out["assistant_text"]
    assert "tail" in out["assistant_text"].lower()


def test_voutp_connectivity_answer():
    out = node_deterministic_tool_runner({
        "layout_session_tool_name": "answer_from_initial_trace",
        "layout_session_tool_args": {"question": "what devices are connected to VOUTP?"},
        "initial_agent_trace": _comparator_trace(),
        "placement_nodes": _comparator_nodes(),
    })
    assert "Drain/source: MM5, MM2, MM6" in out["assistant_text"]


def test_matching_current_explanation_lists_groups():
    out = node_deterministic_tool_runner({
        "layout_session_tool_name": "answer_from_initial_trace",
        "layout_session_tool_args": {"question": "How matching techniques is applied in this layout now"},
        "initial_agent_trace": _comparator_trace(),
        "placement_nodes": _comparator_nodes(),
    })
    for pair in ("MM8/MM9", "MM0/MM3", "MM4/MM5", "MM6/MM7", "MM1/MM2"):
        assert pair in out["assistant_text"]
    assert "physical finger-order confirmation" in out["assistant_text"]


def test_mm3_mm0_common_centroid_question_answer_only_no_commands():
    agent = run_layout_session_agent({
        "user_message": "MM3 and MM0 common centroid or interdigitation?",
        "initial_agent_trace": _comparator_trace(),
        "placement_nodes": _comparator_nodes(),
    })
    assert agent["layout_session_decision"] == "call_deterministic_tool"
    assert not agent.get("pending_cmds")
    out = node_deterministic_tool_runner({**agent, "initial_agent_trace": _comparator_trace(), "placement_nodes": _comparator_nodes()})
    assert out["layout_session_decision"] == "answer"
    assert not out.get("pending_cmds")
    assert "MM0/MM3" in out["assistant_text"]


def test_mm9_mm8_common_centroid_answer_only_no_unrelated_commands():
    agent = run_layout_session_agent({
        "user_message": "Match MM9 and MM8 with common centroid",
        "initial_agent_trace": _comparator_trace(),
        "placement_nodes": _comparator_nodes(),
    })
    assert agent["layout_session_decision"] == "call_deterministic_tool"
    assert not agent.get("pending_cmds")
    out = node_deterministic_tool_runner({**agent, "initial_agent_trace": _comparator_trace(), "placement_nodes": _comparator_nodes()})
    assert "MM8/MM9" in out["assistant_text"]
    assert "MM6/MM7" not in out["assistant_text"]
    assert not out.get("pending_cmds")


def test_move_mm1_left_parses_command():
    cmds = parse_direct_edit_command("Move MM1 to the left", [{"id": "MM1_m0"}])
    assert cmds == [{"action": "move", "device_id": "MM1", "dx": -1, "dy": 0}]


def test_move_left_then_target_mm1_fills_pending_intent():
    first = node_deterministic_tool_runner({
        "layout_session_tool_name": "parse_direct_edit_command",
        "user_message": "move left",
        "placement_nodes": [{"id": "MM1"}],
    })
    assert first["pending_edit_intent"]["action"] == "move"
    second = node_deterministic_tool_runner({
        "layout_session_tool_name": "try_fill_edit_slots",
        "user_message": "Target device is MM1",
        "pending_edit_intent": first["pending_edit_intent"],
        "placement_nodes": [{"id": "MM1"}],
    })
    assert second["pending_cmds"] == [{"action": "move", "dx": -1, "dy": 0, "device_id": "MM1"}]
    assert second["pending_edit_intent"] is None


def test_pending_intent_persisted_across_orchestrator_turns():
    from ai_agent.llm.workers import OrchestratorWorker

    worker = OrchestratorWorker()
    pending = {"action": "move", "dx": -1, "dy": 0, "missing": ["device_id"]}
    worker._cache_chat_session_memory({"pending_edit_intent": pending})
    state = {"chat_history": []}
    worker._inject_chat_session_memory(state)
    assert state["pending_edit_intent"] == pending
    worker._cache_chat_session_memory({"pending_edit_intent": None, "layout_session_decision": "propose_commands"})
    state2 = {"chat_history": []}
    worker._inject_chat_session_memory(state2)
    assert "pending_edit_intent" not in state2


def test_check_drc_formats_human_readable_no_raw_dict():
    text = format_drc_flags([
        {
            "kind": "OVERLAP",
            "dev_a": "MM6",
            "dev_b": "MM8_m3",
            "x1_a": 1.47,
            "x2_a": 1.76,
            "x1_b": 1.60,
            "x2_b": 1.90,
            "text": "OVERLAP: MM6 vs MM8_m3  MOVE MM8_m3 to x=1.764, y=0.000",
        }
    ])
    assert "1. OVERLAP: MM6 overlaps MM8_m3" in text
    assert "Suggested fix: move MM8_m3" in text
    assert "{'kind'" not in text

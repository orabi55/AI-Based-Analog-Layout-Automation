from types import SimpleNamespace
import json


def test_stage_placement_retries_on_non_object_json(monkeypatch):
    from ai_agent.ai_initial_placement import multi_agent_placer as mp

    calls = {"count": 0}

    def fake_call_llm(*args, **kwargs):
        calls["count"] += 1
        return "[]"

    monkeypatch.setattr(mp, "_call_llm", fake_call_llm)

    result = mp._stage_placement(
        nodes=[{"id": "MN0", "type": "nmos", "geometry": {"width": 0.294}}],
        edges=[],
        graph_data={},
        abutment_candidates=[],
        constraint_text="",
        selected_model="Gemini",
        task_weight="light",
    )

    assert calls["count"] == mp.MAX_RETRIES
    assert isinstance(result, list)
    assert result and result[0]["id"] == "MN0"


def test_multi_agent_generate_placement_preserves_input_schema(monkeypatch, tmp_path):
    from ai_agent.ai_initial_placement import multi_agent_placer as mp

    input_path = tmp_path / "input.json"
    output_path = tmp_path / "output.json"
    input_payload = {
        "nodes": [
            {
                "id": "MN0",
                "type": "nmos",
                "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.668},
                "electrical": {"nf": 1, "nfin": 2, "l": 1.4e-8},
            }
        ],
        "edges": [{"source": "MN0", "target": "MN0", "net": "N1"}],
        "terminal_nets": {"MN0": {"D": "N1", "G": "VG", "S": "VSS"}},
    }
    input_path.write_text(json.dumps(input_payload), encoding="utf-8")

    # Mock the LLM call to return a valid JSON array directly
    def fake_call_llm(*args, **kwargs):
        return '{"nmos_rows":[{"label":"nmos","devices":["MN0"]}],"pmos_rows":[]}'

    monkeypatch.setattr(mp, "_call_llm", fake_call_llm)

    mp.multi_agent_generate_placement(str(input_path), str(output_path), selected_model="Gemini")

    output_payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert "edges" in output_payload
    assert "terminal_nets" in output_payload
    assert output_payload["nodes"][0]["id"] == "MN0"


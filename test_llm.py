import pytest
import os
from unittest.mock import patch
from ai_agent.llm_worker import run_llm
from ai_agent.placement_specialist import build_placement_context

def test_run_llm_no_keys():
    # Mock os.environ to have no valid API keys
    with patch.dict(os.environ, {}, clear=True):
        result = run_llm([{"role": "user", "content": "hello"}], full_prompt="test")
        assert result == ""

# if you got an error related to the gemini api
# you can try to set the environment variable
# ( $env:GEMINI_API_KEY="AIzaSyApwhWPssGbI6L5siyrfn24AYQWe52NW2E" )
# then run from the terminal (python test_llm.py) 

if __name__ == "__main__":
    from ai_agent.gemini_placer import gemini_generate_placement

    gemini_generate_placement(
        "examples/std_cell/Std_Cell_layout_graph.json",
        "examples/std_cell/Std_Cell_initial_placement.json"
    )

    #from ai_agent.ollama_placer import ollama_generate_placement

    #ollama_generate_placement(
    #    "xor_layout_graph.json",
    #    "xor_initial_placement.json",
    #    model="llama3.2"
    #)


def test_build_system_prompt_nodes():
    nodes = [
        {"id": "MM1", "type": "nmos", "geometry": {"x": 0, "y": 0}},
        {"id": "MM2", "type": "pmos", "geometry": {"x": 0, "y": 0}}
    ]
    context = build_placement_context(nodes, "")
    assert "MM1" in context
    assert "MM2" in context

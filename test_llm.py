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

def test_build_system_prompt_nodes():
    nodes = [
        {"id": "MM1", "type": "nmos", "geometry": {"x": 0, "y": 0}},
        {"id": "MM2", "type": "pmos", "geometry": {"x": 0, "y": 0}}
    ]
    context = build_placement_context(nodes, "")
    assert "MM1" in context
    assert "MM2" in context
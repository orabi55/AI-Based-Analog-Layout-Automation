import pytest
import os
import copy
from ai_agent.drc_critic import run_drc_check
from ai_agent.tools import tool_validate_device_count
from ai_agent.orchestrator import _apply_cmds_to_nodes
from ai_agent.orchestrator import _extract_cmd_blocks

def test_drc_check_overlap():
    nodes = [
        {"id": "MM1", "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.668}},
        {"id": "MM2", "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.668}}
    ]
    result = run_drc_check(nodes, gap_px=0.0)
    assert result["pass"] is False
    assert "overlapped" in "\n".join(result["violations"]) or "overlap" in "\n".join(result["violations"]).lower()

def test_drc_check_pass():
    nodes = [
        {"id": "MM1", "geometry": {"x": 0.0, "y": 0.0, "width": 0.294, "height": 0.668}},
        {"id": "MM2", "geometry": {"x": 0.400, "y": 0.0, "width": 0.294, "height": 0.668}}
    ]
    result = run_drc_check(nodes, gap_px=0.0)
    assert result["pass"] is True

def test_tool_validate_device_count():
    orig_nodes = [{"id": "MM1"}, {"id": "MM2"}]
    new_nodes = [{"id": "MM1"}]
    result = tool_validate_device_count(orig_nodes, new_nodes)
    assert result["pass"] is False
    assert "missing" in str(result["missing"]).lower() or "missing" in str(result).lower()

def test_apply_cmds_to_nodes():
    nodes = [
        {"id": "MM1", "geometry": {"x": 0.0, "y": 0.0}},
        {"id": "MM2", "geometry": {"x": 1.0, "y": 0.0}}
    ]
    cmds = [{"action": "swap", "device_a": "MM1", "device_b": "MM2"}]
    new_nodes = _apply_cmds_to_nodes(nodes, cmds)
    assert new_nodes[0]["geometry"]["x"] == 1.0
    assert new_nodes[1]["geometry"]["x"] == 0.0

def test_extract_cmd_blocks():
    text = """Here is the command:
[CMD]{"action": "move", "device": "MM1", "x": 1.2}[/CMD]
    """
    cmds = _extract_cmd_blocks(text)
    assert len(cmds) == 1
    assert cmds[0]["action"] == "move"

def test_extract_cmd_blocks_malformed():
    text = """Here is the command:
[CMD]{"action": "move", "device": "MM1", "x": }[/CMD]
    """
    cmds = _extract_cmd_blocks(text)
    assert len(cmds) == 0

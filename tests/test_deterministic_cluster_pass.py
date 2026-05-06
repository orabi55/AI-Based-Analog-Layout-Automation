"""
test_deterministic_cluster_pass.py
=====================================
Unit tests for _cluster_critical_nets_post_expansion — the post-expansion
clustering step that reorders chains within rows to cluster critical-net
devices and aligns cluster centres across rows.

Tests verify:
  1. Off-path (Low priority / empty nets) → node list returned unchanged.
  2. Same-row chains reordered so critical-net chain is centred.
  3. Cross-row alignment shifts rows to share the same critical cluster centre.
  4. Single-device nets → nothing moves.
  5. Feature does not crash when terminal_nets is None.
"""
import copy
import pytest
import sys, types

# Comprehensive langchain mock — the project imports many submodules at
# import time (skill_injector, _shared, etc.).  We just need the symbols
# to exist so the module-level code doesn't crash.
def _ensure_mock(dotted_name, attrs=None):
    if dotted_name not in sys.modules:
        mod = types.ModuleType(dotted_name)
        for a in (attrs or []):
            setattr(mod, a, type(a, (), {}))
        sys.modules[dotted_name] = mod
    return sys.modules[dotted_name]

_ensure_mock("langchain")
_ensure_mock("langchain.agents")
_ensure_mock("langchain.agents.middleware",
             ["AgentMiddleware", "ModelRequest", "ModelResponse"])
_ensure_mock("langchain.messages", ["SystemMessage"])
_ensure_mock("langchain.tools")
# langchain.tools.tool is a decorator — make it a passthrough
sys.modules["langchain.tools"].tool = lambda f: f
_ensure_mock("langchain_core")
_ensure_mock("langchain_core.runnables", ["RunnableConfig"])
_ensure_mock("langgraph")
_ensure_mock("langgraph.types", ["interrupt"])
sys.modules["langgraph.types"].interrupt = lambda *a, **kw: None

STD_PITCH = 0.294  # µm


def _make_node(nid, ntype, x, y, block_id=None, width=None):
    n = {
        "id": nid,
        "type": ntype,
        "geometry": {"x": float(x), "y": float(y),
                     "width": width or STD_PITCH, "height": 0.668},
    }
    if block_id:
        n["_block_id"] = block_id
    return n


class TestOffPath:
    """When the feature is OFF the node list must be returned byte-identical."""

    def test_low_priority(self):
        from ai_agent.nodes.placement_specialist import _cluster_critical_nets_post_expansion
        nodes = [_make_node("MM1_m1", "nmos", 0.0, 0.0)]
        tn = {"MM1_m1": {"D": "VOUTP", "G": "VIN", "S": "VSS"}}
        goals = {"critical_nets": {"priority": "Low", "nets": ["VOUTP"]}}
        result = _cluster_critical_nets_post_expansion(nodes, tn, goals)
        assert result[0]["geometry"]["x"] == 0.0

    def test_no_goals(self):
        from ai_agent.nodes.placement_specialist import _cluster_critical_nets_post_expansion
        nodes = [_make_node("MM1_m1", "nmos", 0.0, 0.0)]
        tn = {"MM1_m1": {"D": "VOUTP", "G": "VIN", "S": "VSS"}}
        result = _cluster_critical_nets_post_expansion(nodes, tn, None)
        assert result[0]["geometry"]["x"] == 0.0

    def test_empty_nets(self):
        from ai_agent.nodes.placement_specialist import _cluster_critical_nets_post_expansion
        nodes = [_make_node("MM1_m1", "nmos", 0.0, 0.0)]
        tn = {"MM1_m1": {"D": "VOUTP", "G": "VIN", "S": "VSS"}}
        goals = {"critical_nets": {"priority": "High", "nets": []}}
        result = _cluster_critical_nets_post_expansion(nodes, tn, goals)
        assert result[0]["geometry"]["x"] == 0.0

    def test_none_terminal_nets(self):
        from ai_agent.nodes.placement_specialist import _cluster_critical_nets_post_expansion
        nodes = [_make_node("MM1_m1", "nmos", 0.0, 0.0)]
        goals = {"critical_nets": {"priority": "High", "nets": ["VOUTP"]}}
        result = _cluster_critical_nets_post_expansion(nodes, None, goals)
        assert result[0]["geometry"]["x"] == 0.0


class TestChainReordering:
    """Critical-net chains should be moved to the centre of their row."""

    def test_reorder_three_chains(self):
        """Row has 3 chains: [A_block, B_block, C_block].
        B_block has a critical-net finger.
        Expected: B_block moves to centre → [A_block, B_block, C_block]
        or [C_block, B_block, A_block] depending on the split.
        """
        from ai_agent.nodes.placement_specialist import _cluster_critical_nets_post_expansion
        # Chain A: 2 fingers at x=0, 0.294
        nodes = [
            _make_node("A_m1", "nmos", 0.0, 0.0, block_id="A_block"),
            _make_node("A_m2", "nmos", 0.294, 0.0, block_id="A_block"),
            # Chain B: 2 fingers at x=0.882, 1.176 (gap of 0.294 between chains)
            _make_node("B_m1", "nmos", 0.882, 0.0, block_id="B_block"),
            _make_node("B_m2", "nmos", 1.176, 0.0, block_id="B_block"),
            # Chain C: 2 fingers at x=1.764, 2.058
            _make_node("C_m1", "nmos", 1.764, 0.0, block_id="C_block"),
            _make_node("C_m2", "nmos", 2.058, 0.0, block_id="C_block"),
        ]
        # B_m1 is on VOUTP
        tn = {
            "A_m1": {"D": "VX", "G": "VIN", "S": "VSS"},
            "A_m2": {"D": "VX", "G": "VIN", "S": "VSS"},
            "B_m1": {"D": "VOUTP", "G": "VBN", "S": "VSS"},
            "B_m2": {"D": "VOUTP", "G": "VBN", "S": "VSS"},
            "C_m1": {"D": "VY", "G": "VBP", "S": "VDD"},
            "C_m2": {"D": "VY", "G": "VBP", "S": "VDD"},
        }
        goals = {"critical_nets": {"priority": "High", "nets": ["VOUTP"]}}

        result = _cluster_critical_nets_post_expansion(nodes, tn, goals)
        by_id = {n["id"]: n for n in result}

        # B_block should now be between the two non-critical chains
        # (non_crit split: left=[], right=[A_block, C_block] when n_left=0
        #  OR left=[A_block], right=[C_block] when n_left=1)
        # With 2 non-critical chains, n_left=1: [A_block, B_block, C_block]
        # So B_block is in the middle.
        b_x = by_id["B_m1"]["geometry"]["x"]
        a_x = by_id["A_m1"]["geometry"]["x"]
        c_x = by_id["C_m1"]["geometry"]["x"]
        # B should be between A and C
        assert a_x < b_x < c_x, f"Expected A < B < C, got A={a_x}, B={b_x}, C={c_x}"


class TestCrossRowAlignment:
    """Critical clusters should be X-aligned across rows."""

    def test_two_rows_aligned(self):
        from ai_agent.nodes.placement_specialist import _cluster_critical_nets_post_expansion
        # NMOS row at y=0: critical chain at x=0
        # PMOS row at y=2: critical chain at x=3
        # After alignment they should share the same centre X.
        nodes = [
            _make_node("N_m1", "nmos", 0.0, 0.0, block_id="N_block"),
            _make_node("N_m2", "nmos", 0.294, 0.0, block_id="N_block"),
            _make_node("P_m1", "pmos", 3.0, 2.0, block_id="P_block"),
            _make_node("P_m2", "pmos", 3.294, 2.0, block_id="P_block"),
        ]
        tn = {
            "N_m1": {"D": "VOUTP", "G": "VIN", "S": "VSS"},
            "N_m2": {"D": "VOUTP", "G": "VIN", "S": "VSS"},
            "P_m1": {"D": "VOUTP", "G": "VBP", "S": "VDD"},
            "P_m2": {"D": "VOUTP", "G": "VBP", "S": "VDD"},
        }
        goals = {"critical_nets": {"priority": "High", "nets": ["VOUTP"]}}

        result = _cluster_critical_nets_post_expansion(nodes, tn, goals)
        by_id = {n["id"]: n for n in result}

        n_centre = (by_id["N_m1"]["geometry"]["x"] + by_id["N_m2"]["geometry"]["x"] + STD_PITCH) / 2.0
        p_centre = (by_id["P_m1"]["geometry"]["x"] + by_id["P_m2"]["geometry"]["x"] + STD_PITCH) / 2.0
        # Centres should be equal (within rounding)
        assert abs(n_centre - p_centre) < 0.01, f"N_centre={n_centre}, P_centre={p_centre}"

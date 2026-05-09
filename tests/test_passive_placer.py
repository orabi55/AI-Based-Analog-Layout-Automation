"""
Tests for ai_agent/core/passive_placer.py and the dispatcher routes.
"""

import sys
import os
import math

_root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

import pytest
from ai_agent.core.passive_placer import (
    place_resistor,
    place_mom_cap,
    place_mos_cap,
    reshape_passive,
)
from ai_agent.core.interfaces import LayoutToolResult
from ai_agent.tools.dispatcher import dispatch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _bare_node(nid="R1", ntype="resistor"):
    return {"id": nid, "type": ntype, "geometry": {"x": 0.0, "y": 0.0}}


@pytest.fixture
def bare_node():
    return _bare_node()


@pytest.fixture
def layout_with_passive():
    """Two-node layout for dispatcher tests."""
    return [
        {"id": "R1", "type": "unknown", "geometry": {"x": 0.0, "y": 0.0}},
        {"id": "C1", "type": "unknown", "geometry": {"x": 1.0, "y": 0.0}},
    ]


# ---------------------------------------------------------------------------
# place_resistor
# ---------------------------------------------------------------------------

class TestPlaceResistor:
    def test_returns_layout_tool_result(self, bare_node):
        r = place_resistor(bare_node, area_um2=10.0)
        assert isinstance(r, LayoutToolResult)

    def test_success(self, bare_node):
        r = place_resistor(bare_node, area_um2=10.0)
        assert r.success
        assert r.changed

    def test_type_set_to_resistor(self, bare_node):
        r = place_resistor(bare_node, area_um2=10.0)
        assert r.nodes[0]["type"] == "resistor"

    def test_segments_present(self, bare_node):
        r = place_resistor(bare_node, area_um2=10.0)
        assert isinstance(r.nodes[0]["segments"], list)
        assert len(r.nodes[0]["segments"]) >= 1

    def test_area_conserved(self, bare_node):
        """Total segment area ≈ requested area."""
        area = 8.0
        r = place_resistor(bare_node, area_um2=area)
        assert r.success
        total_area = sum(s["width"] * s["height"] for s in r.nodes[0]["segments"])
        assert abs(total_area - area) < 1e-4

    def test_aspect_ratio_in_metrics(self, bare_node):
        r = place_resistor(bare_node, area_um2=10.0, aspect_ratio=6.0)
        assert abs(r.metrics["actual_resistance_ratio"] - 6.0) < 1e-9

    def test_actual_resistance_ratio_equals_aspect_ratio(self, bare_node):
        """Folding/parallel cannot change effective L/W."""
        for ar in (1.0, 4.0, 10.0, 20.0):
            r = place_resistor(bare_node, area_um2=5.0, aspect_ratio=ar)
            assert r.success
            assert abs(r.metrics["actual_resistance_ratio"] - ar) < 1e-9

    def test_series_folding_when_tall(self, bare_node):
        """aspect_ratio=100 with area=10 → height ≈ 31.6 µm → must fold."""
        r = place_resistor(bare_node, area_um2=10.0, aspect_ratio=100.0, allow_series=True)
        assert r.success
        assert r.metrics["n_series"] >= 2

    def test_no_series_when_disabled(self, bare_node):
        r = place_resistor(bare_node, area_um2=10.0, aspect_ratio=100.0, allow_series=False)
        assert r.success
        assert r.metrics["n_series"] == 1

    def test_geometry_width_height_set(self, bare_node):
        r = place_resistor(bare_node, area_um2=10.0, aspect_ratio=4.0)
        geo = r.nodes[0]["geometry"]
        assert geo["width"] > 0
        assert geo["height"] > 0

    def test_passive_metadata_stored(self, bare_node):
        r = place_resistor(bare_node, area_um2=10.0, aspect_ratio=4.0)
        pm = r.nodes[0]["_passive"]
        assert pm["area_um2"]    == 10.0
        assert pm["aspect_ratio"] == 4.0

    def test_zero_area_returns_failure(self, bare_node):
        r = place_resistor(bare_node, area_um2=0.0)
        assert not r.success

    def test_negative_area_returns_failure(self, bare_node):
        r = place_resistor(bare_node, area_um2=-5.0)
        assert not r.success

    def test_zero_aspect_ratio_returns_failure(self, bare_node):
        r = place_resistor(bare_node, area_um2=10.0, aspect_ratio=0.0)
        assert not r.success


# ---------------------------------------------------------------------------
# place_mom_cap
# ---------------------------------------------------------------------------

class TestPlaceMomCap:
    def test_success(self, bare_node):
        r = place_mom_cap(bare_node, area_um2=4.0)
        assert r.success
        assert r.changed

    def test_type_set_to_mom_cap(self, bare_node):
        r = place_mom_cap(bare_node, area_um2=4.0)
        assert r.nodes[0]["type"] == "mom_cap"

    def test_can_overlap_true_on_node(self, bare_node):
        """can_overlap must be True ON THE NODE (not just in metrics)."""
        r = place_mom_cap(bare_node, area_um2=4.0)
        assert r.nodes[0].get("can_overlap") is True

    def test_can_overlap_true_in_metrics(self, bare_node):
        r = place_mom_cap(bare_node, area_um2=4.0)
        assert r.metrics["can_overlap"] is True

    def test_finger_count_even(self, bare_node):
        """MOM caps must have A/B pairs — even finger count."""
        r = place_mom_cap(bare_node, area_um2=4.0)
        assert r.metrics["finger_count"] % 2 == 0

    def test_finger_count_positive(self, bare_node):
        r = place_mom_cap(bare_node, area_um2=4.0)
        assert r.metrics["finger_count"] >= 2

    def test_fingers_have_alternating_nets(self, bare_node):
        r = place_mom_cap(bare_node, area_um2=4.0)
        fingers = r.nodes[0]["fingers"]
        per_layer = [f for f in fingers if f["layer"] == fingers[0]["layer"]]
        nets = [f["net"] for f in per_layer]
        for i in range(len(nets) - 1):
            assert nets[i] != nets[i + 1], "Adjacent fingers must alternate A/B"

    def test_custom_layers(self, bare_node):
        layers = ["M3", "M4", "M5", "M6"]
        r = place_mom_cap(bare_node, area_um2=4.0, layers=layers)
        assert r.success
        assert r.metrics["n_layers"] == 4
        used_layers = {f["layer"] for f in r.nodes[0]["fingers"]}
        assert used_layers == set(layers)

    def test_default_layers_used_when_none(self, bare_node):
        r = place_mom_cap(bare_node, area_um2=4.0, layers=None)
        assert r.success
        assert r.metrics["n_layers"] == 3

    def test_geometry_set(self, bare_node):
        r = place_mom_cap(bare_node, area_um2=4.0)
        geo = r.nodes[0]["geometry"]
        assert geo["width"] > 0
        assert geo["height"] > 0

    def test_passive_metadata_stored(self, bare_node):
        r = place_mom_cap(bare_node, area_um2=4.0, layers=["M2", "M3"])
        pm = r.nodes[0]["_passive"]
        assert pm["area_um2"] == 4.0
        assert pm["layers"]   == ["M2", "M3"]

    def test_zero_area_returns_failure(self, bare_node):
        r = place_mom_cap(bare_node, area_um2=0.0)
        assert not r.success

    def test_empty_layers_returns_failure(self, bare_node):
        r = place_mom_cap(bare_node, area_um2=4.0, layers=[])
        assert not r.success


# ---------------------------------------------------------------------------
# place_mos_cap
# ---------------------------------------------------------------------------

class TestPlaceMosCap:
    def test_success(self, bare_node):
        r = place_mos_cap(bare_node, nf=4, width_um=1.0)
        assert r.success
        assert r.changed

    def test_type_set_to_mos_cap(self, bare_node):
        r = place_mos_cap(bare_node, nf=4, width_um=1.0)
        assert r.nodes[0]["type"] == "mos_cap"

    def test_gate_drain_tied(self, bare_node):
        r = place_mos_cap(bare_node, nf=4, width_um=1.0)
        elec = r.nodes[0].get("electrical", {})
        assert elec.get("gate_drain_tied") is True

    def test_geometry_width_proportional_to_nf(self, bare_node):
        r2 = place_mos_cap(bare_node, nf=2,  width_um=1.0)
        r4 = place_mos_cap(bare_node, nf=4,  width_um=1.0)
        r8 = place_mos_cap(bare_node, nf=8,  width_um=1.0)
        w2 = r2.nodes[0]["geometry"]["width"]
        w4 = r4.nodes[0]["geometry"]["width"]
        w8 = r8.nodes[0]["geometry"]["width"]
        assert abs(w4 / w2 - 2.0) < 1e-9
        assert abs(w8 / w2 - 4.0) < 1e-9

    def test_height_is_transistor_height(self, bare_node):
        r = place_mos_cap(bare_node, nf=4, width_um=1.0)
        assert abs(r.nodes[0]["geometry"]["height"] - 0.568) < 1e-9

    def test_metrics_nf(self, bare_node):
        r = place_mos_cap(bare_node, nf=6, width_um=2.0)
        assert r.metrics["nf"] == 6

    def test_passive_metadata_stored(self, bare_node):
        r = place_mos_cap(bare_node, nf=4, width_um=1.5)
        pm = r.nodes[0]["_passive"]
        assert pm["nf"]       == 4
        assert abs(pm["width_um"] - 1.5) < 1e-9

    def test_zero_nf_returns_failure(self, bare_node):
        r = place_mos_cap(bare_node, nf=0, width_um=1.0)
        assert not r.success

    def test_zero_width_returns_failure(self, bare_node):
        r = place_mos_cap(bare_node, nf=4, width_um=0.0)
        assert not r.success

    def test_negative_nf_returns_failure(self, bare_node):
        r = place_mos_cap(bare_node, nf=-1, width_um=1.0)
        assert not r.success


# ---------------------------------------------------------------------------
# reshape_passive
# ---------------------------------------------------------------------------

class TestReshapePassive:
    def test_reshape_resistor_doubles_area(self, bare_node):
        r1 = place_resistor(bare_node, area_um2=10.0, aspect_ratio=4.0)
        node1 = r1.nodes[0]
        r2    = reshape_passive(node1, new_area_um2=20.0)
        assert r2.success
        assert r2.metrics["area_um2"] == 20.0

    def test_reshape_resistor_preserves_aspect_ratio(self, bare_node):
        r1 = place_resistor(bare_node, area_um2=10.0, aspect_ratio=5.0)
        r2 = reshape_passive(r1.nodes[0], new_area_um2=40.0)
        assert r2.success
        assert abs(r2.metrics["actual_resistance_ratio"] - 5.0) < 1e-9

    def test_reshape_resistor_preserves_type(self, bare_node):
        r1 = place_resistor(bare_node, area_um2=10.0)
        r2 = reshape_passive(r1.nodes[0], new_area_um2=5.0)
        assert r2.nodes[0]["type"] == "resistor"

    def test_reshape_resistor_round_trip(self, bare_node):
        """Apply, then reshape back: dimensions should match original."""
        area = 8.0
        ar   = 4.0
        r1   = place_resistor(bare_node, area_um2=area, aspect_ratio=ar)
        r2   = reshape_passive(r1.nodes[0], new_area_um2=20.0)
        r3   = reshape_passive(r2.nodes[0], new_area_um2=area)
        # Width and height after round-trip should match original
        orig_w = r1.metrics["width_um"]
        orig_h = r1.metrics["height_um"]
        back_w = r3.metrics["width_um"]
        back_h = r3.metrics["height_um"]
        assert abs(back_w - orig_w) < 1e-6
        assert abs(back_h - orig_h) < 1e-6

    def test_reshape_mom_cap_doubles_area(self, bare_node):
        r1 = place_mom_cap(bare_node, area_um2=4.0)
        r2 = reshape_passive(r1.nodes[0], new_area_um2=8.0)
        assert r2.success
        assert r2.metrics["area_um2"] == 8.0

    def test_reshape_mom_cap_preserves_layers(self, bare_node):
        layers = ["M3", "M5"]
        r1     = place_mom_cap(bare_node, area_um2=4.0, layers=layers)
        r2     = reshape_passive(r1.nodes[0], new_area_um2=8.0)
        assert r2.success
        assert r2.metrics["n_layers"] == 2
        used = {f["layer"] for f in r2.nodes[0]["fingers"]}
        assert used == set(layers)

    def test_reshape_mom_cap_preserves_type(self, bare_node):
        r1 = place_mom_cap(bare_node, area_um2=4.0)
        r2 = reshape_passive(r1.nodes[0], new_area_um2=8.0)
        assert r2.nodes[0]["type"] == "mom_cap"

    def test_reshape_mos_cap_scales_nf(self, bare_node):
        r1 = place_mos_cap(bare_node, nf=4, width_um=1.0)
        r2 = reshape_passive(r1.nodes[0], new_area_um2=r1.metrics["area_um2"] * 2)
        assert r2.success
        assert r2.metrics["nf"] > r1.metrics["nf"]

    def test_reshape_mos_cap_preserves_type(self, bare_node):
        r1 = place_mos_cap(bare_node, nf=4, width_um=1.0)
        r2 = reshape_passive(r1.nodes[0], new_area_um2=2.0)
        assert r2.nodes[0]["type"] == "mos_cap"

    def test_reshape_mos_cap_preserves_width_um(self, bare_node):
        r1 = place_mos_cap(bare_node, nf=4, width_um=1.5)
        r2 = reshape_passive(r1.nodes[0], new_area_um2=2.0)
        assert abs(r2.metrics["width_um"] - 1.5) < 1e-9

    def test_reshape_unsupported_type_returns_failure(self):
        node = {"id": "X1", "type": "unknown", "geometry": {}}
        r = reshape_passive(node, new_area_um2=5.0)
        assert not r.success
        assert "unsupported" in r.message.lower()

    def test_reshape_zero_area_returns_failure(self, bare_node):
        r1 = place_resistor(bare_node, area_um2=10.0)
        r2 = reshape_passive(r1.nodes[0], new_area_um2=0.0)
        assert not r2.success

    def test_reshape_mom_cap_can_overlap_preserved(self, bare_node):
        r1 = place_mom_cap(bare_node, area_um2=4.0)
        r2 = reshape_passive(r1.nodes[0], new_area_um2=8.0)
        assert r2.nodes[0].get("can_overlap") is True


# ---------------------------------------------------------------------------
# Dispatcher routes
# ---------------------------------------------------------------------------

class TestDispatcherPassive:
    def test_place_resistor_route(self, layout_with_passive):
        r = dispatch("place_resistor",
                     {"node_id": "R1", "area_um2": 10.0, "aspect_ratio": 4.0},
                     layout_with_passive)
        assert r.success
        r1_node = next(n for n in r.nodes if n["id"] == "R1")
        assert r1_node["type"] == "resistor"

    def test_place_resistor_preserves_other_nodes(self, layout_with_passive):
        r = dispatch("place_resistor",
                     {"node_id": "R1", "area_um2": 10.0},
                     layout_with_passive)
        assert any(n["id"] == "C1" for n in r.nodes)

    def test_place_mom_cap_route(self, layout_with_passive):
        r = dispatch("place_mom_cap",
                     {"node_id": "C1", "area_um2": 4.0},
                     layout_with_passive)
        assert r.success
        c1_node = next(n for n in r.nodes if n["id"] == "C1")
        assert c1_node["type"] == "mom_cap"
        assert c1_node.get("can_overlap") is True

    def test_place_mom_cap_can_overlap_flag(self, layout_with_passive):
        """can_overlap must be True on the node in the returned full layout."""
        r = dispatch("place_mom_cap",
                     {"node_id": "C1", "area_um2": 4.0},
                     layout_with_passive)
        c1 = next(n for n in r.nodes if n["id"] == "C1")
        assert c1.get("can_overlap") is True

    def test_place_mos_cap_route(self, layout_with_passive):
        r = dispatch("place_mos_cap",
                     {"node_id": "R1", "nf": 4, "width_um": 1.0},
                     layout_with_passive)
        assert r.success
        r1_node = next(n for n in r.nodes if n["id"] == "R1")
        assert r1_node["type"] == "mos_cap"

    def test_reshape_passive_route(self, layout_with_passive):
        # First configure R1 as a resistor
        r1 = dispatch("place_resistor",
                      {"node_id": "R1", "area_um2": 10.0, "aspect_ratio": 4.0},
                      layout_with_passive)
        # Then reshape it
        r2 = dispatch("reshape_passive",
                      {"node_id": "R1", "new_area_um2": 20.0},
                      r1.nodes)
        assert r2.success
        assert r2.metrics["area_um2"] == 20.0

    def test_passive_node_not_found_returns_failure(self, layout_with_passive):
        r = dispatch("place_resistor",
                     {"node_id": "GHOST", "area_um2": 10.0},
                     layout_with_passive)
        assert not r.success
        assert "GHOST" in r.message

    def test_full_layout_returned_after_passive_op(self, layout_with_passive):
        r = dispatch("place_resistor",
                     {"node_id": "R1", "area_um2": 10.0},
                     layout_with_passive)
        ids = {n["id"] for n in r.nodes}
        assert ids == {"R1", "C1"}

    def test_result_is_layout_tool_result(self, layout_with_passive):
        for tool, args in [
            ("place_resistor",  {"node_id": "R1", "area_um2": 5.0}),
            ("place_mom_cap",   {"node_id": "C1", "area_um2": 4.0}),
            ("place_mos_cap",   {"node_id": "R1", "nf": 2, "width_um": 1.0}),
        ]:
            r = dispatch(tool, args, layout_with_passive)
            assert isinstance(r, LayoutToolResult), f"{tool} did not return LayoutToolResult"

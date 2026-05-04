# tests/schematic/test_render.py
"""
Schematic regression tests.

Run with:
    QT_QPA_PLATFORM=offscreen pytest tests/schematic/ -m gui -v

Skipped automatically when Qt offscreen platform is unavailable.
"""
import os
import sys
import time
import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _group_kinds(nodes, terminal_nets):
    """Run detect_groups and return a list of group kind strings."""
    sys.path.insert(0, os.path.join(_ROOT, "symbolic_editor"))
    from schematic_layout import detect_groups
    groups = detect_groups(nodes, terminal_nets)
    return [g.kind for g in groups]


def _build_and_measure(nodes, terminal_nets, qt_app):
    """
    Call SchematicCanvas.build_schematic() and return
    (scene_bounding_rect, elapsed_ms).
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, os.path.join(_ROOT, "symbolic_editor"))
    from schematic_view import SchematicCanvas

    canvas = SchematicCanvas()
    t0 = time.perf_counter()
    canvas.build_schematic(nodes, terminal_nets)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    rect = canvas.scene().itemsBoundingRect()
    return rect, elapsed_ms


# ---------------------------------------------------------------------------
# Mark
# ---------------------------------------------------------------------------
pytestmark = pytest.mark.gui


# ---------------------------------------------------------------------------
# Test 1 — Dynamic comparator topology detection
# ---------------------------------------------------------------------------
class TestComparatorTopology:
    def test_has_diff_pair(self, comparator_data):
        nodes, tnets = comparator_data
        kinds = _group_kinds(nodes, tnets)
        assert "diff_pair" in kinds, f"Expected diff_pair in groups, got: {kinds}"

    def test_has_cross_coupled_latch(self, comparator_data):
        nodes, tnets = comparator_data
        kinds = _group_kinds(nodes, tnets)
        assert "cross_coupled_latch" in kinds, (
            f"Expected cross_coupled_latch in groups, got: {kinds}"
        )

    def test_has_tail(self, comparator_data):
        nodes, tnets = comparator_data
        kinds = _group_kinds(nodes, tnets)
        # tail or single are both acceptable for the CLK-gated tail device
        assert "tail" in kinds or "single" in kinds

    def test_render_performance(self, comparator_data, qt_app):
        nodes, tnets = comparator_data
        _, elapsed_ms = _build_and_measure(nodes, tnets, qt_app)
        assert elapsed_ms < 2000, f"Render took {elapsed_ms:.1f} ms (limit 2000 ms)"

    def test_bounding_box_reasonable(self, comparator_data, qt_app):
        nodes, tnets = comparator_data
        rect, _ = _build_and_measure(nodes, tnets, qt_app)
        # Should produce a non-degenerate scene
        assert rect.width() > 100, f"Scene too narrow: {rect.width()}"
        assert rect.height() > 100, f"Scene too flat: {rect.height()}"


# ---------------------------------------------------------------------------
# Test 2 — 5T OTA topology detection
# ---------------------------------------------------------------------------
class TestOTATopology:
    def test_has_diff_pair(self, ota_data):
        nodes, tnets = ota_data
        kinds = _group_kinds(nodes, tnets)
        assert "diff_pair" in kinds, f"Expected diff_pair, got: {kinds}"

    def test_has_current_mirror(self, ota_data):
        nodes, tnets = ota_data
        kinds = _group_kinds(nodes, tnets)
        assert "current_mirror" in kinds, f"Expected current_mirror, got: {kinds}"

    def test_render_performance(self, ota_data, qt_app):
        nodes, tnets = ota_data
        _, elapsed_ms = _build_and_measure(nodes, tnets, qt_app)
        assert elapsed_ms < 2000

    def test_bounding_box_reasonable(self, ota_data, qt_app):
        nodes, tnets = ota_data
        rect, _ = _build_and_measure(nodes, tnets, qt_app)
        assert rect.width() > 50
        assert rect.height() > 50


# ---------------------------------------------------------------------------
# Test 3 — Basic current mirror
# ---------------------------------------------------------------------------
class TestCurrentMirror:
    def test_has_current_mirror(self, mirror_data):
        nodes, tnets = mirror_data
        kinds = _group_kinds(nodes, tnets)
        assert "current_mirror" in kinds, f"Expected current_mirror, got: {kinds}"

    def test_exactly_two_devices(self, mirror_data):
        nodes, tnets = mirror_data
        assert len(nodes) == 2

    def test_render_does_not_crash(self, mirror_data, qt_app):
        nodes, tnets = mirror_data
        rect, elapsed_ms = _build_and_measure(nodes, tnets, qt_app)
        assert elapsed_ms < 2000

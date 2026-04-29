"""
Placement Quality Benchmark Tests
===================================
Tests for the quality_metrics module.

Usage:
    python -m pytest tests/test_quality_metrics.py -v
    python tests/test_quality_metrics.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest

# ── Shared test helpers ───────────────────────────────────────────────────────

FINGER_PITCH = 0.070    # um between consecutive fingers (abutment pitch)
STD_PITCH    = 0.294    # um width of a single finger cell
ROW_H        = 0.568    # um height
NMOS_Y       = 0.0
PMOS_Y       = 0.668


def _make_finger(dev_id: str, finger_idx: int, x: float, y: float,
                 dev_type: str = "nmos",
                 technique: str = None,
                 match_owner: str = None,
                 block_id: str = None) -> dict:
    """Create a single finger node."""
    n = {
        "id":   f"{dev_id}_f{finger_idx}",
        "type": dev_type,
        "geometry": {
            "x":     x,
            "y":     y,
            "width": STD_PITCH,
            "height": ROW_H,
        },
    }
    if technique:
        n["_technique"] = technique
    if match_owner:
        n["_match_owner"] = match_owner
    if block_id:
        n["_block_id"] = block_id
    return n


def _abba_row(nf: int, x0: float, y: float,
              technique: str = "ABBA_diff_pair",
              block_id: str = "blk1") -> list:
    """Build a perfect ABBA interdigitated row of 2*nf fingers."""
    pattern = []
    for i in range(nf // 2):
        pattern += ["A", "B", "B", "A"]
    pattern = pattern[:nf * 2]
    nodes = []
    counters = {"A": 0, "B": 0}
    for i, owner in enumerate(pattern):
        x = x0 + i * FINGER_PITCH
        dev = "MM1" if owner == "A" else "MM2"
        nodes.append(_make_finger(dev, counters[owner], x, y,
                                  technique=technique,
                                  match_owner=owner,
                                  block_id=block_id))
        counters[owner] += 1
    return nodes


# ── Test: perfect layout (ABBA) ───────────────────────────────────────────────

class TestPerfectABBALayout(unittest.TestCase):
    """Perfect ABBA layout — Y-structure, X-sym, interdigitation all should be high."""

    def setUp(self):
        self.nmos_nodes = _abba_row(nf=4, x0=0.0, y=NMOS_Y)
        # PMOS pair — same row, mirror symmetry
        self.pmos_nodes = [
            _make_finger("MM3", 0, 0.0,         PMOS_Y, "pmos"),
            _make_finger("MM3", 1, FINGER_PITCH, PMOS_Y, "pmos"),
            _make_finger("MM4", 0, 2*FINGER_PITCH, PMOS_Y, "pmos"),
            _make_finger("MM4", 1, 3*FINGER_PITCH, PMOS_Y, "pmos"),
        ]
        self.nodes = self.nmos_nodes + self.pmos_nodes
        self.matching_info = {"matched_pairs": [("MM1", "MM2"), ("MM3", "MM4")]}

    def test_composite_high(self):
        from ai_agent.placement.quality_metrics import score_placement
        report = score_placement(self.nodes, self.matching_info, verbose=True)
        print("\n" + report["summary"])
        self.assertGreaterEqual(report["composite_score"], 0.75)

    def test_layout_y_structure_perfect(self):
        from ai_agent.placement.quality_metrics import _layout_y_symmetry
        score, detail = _layout_y_symmetry(
            self.nodes, [("MM1", "MM2"), ("MM3", "MM4")])
        print(f"  Layout Y: {score:.2f}\n{detail}")
        self.assertAlmostEqual(score, 1.0, places=2)

    def test_layout_y_structure_broken(self):
        from ai_agent.placement.quality_metrics import _layout_y_symmetry
        # MM1 at NMOS_Y, MM2 at PMOS_Y (wrong rows)
        broken = [
            _make_finger("MM1", 0, 0.0,         NMOS_Y, "nmos"),
            _make_finger("MM1", 1, FINGER_PITCH, NMOS_Y, "nmos"),
            _make_finger("MM2", 0, 0.0,         PMOS_Y, "nmos"),   # wrong Y
            _make_finger("MM2", 1, FINGER_PITCH, PMOS_Y, "nmos"),
        ]
        score, detail = _layout_y_symmetry(broken, [("MM1", "MM2")])
        print(f"  Layout Y (broken rows): {score:.2f}\n{detail}")
        self.assertAlmostEqual(score, 0.5, places=1)   # sep=1.0 (single type) + pair not same row=0.0 -> 0.5*1+0.5*0=0.5

    def test_interdigitation_detected(self):
        from ai_agent.placement.quality_metrics import _interdigitation_pattern
        score, detail = _interdigitation_pattern(self.nmos_nodes)
        print(f"  Interdig: {score}\n{detail}")
        self.assertIsNotNone(score, "ABBA technique nodes -> score should not be N/A")
        self.assertGreaterEqual(score, 0.9)

    def test_no_overlaps(self):
        from ai_agent.placement.quality_metrics import _drc_overlap
        score, detail = _drc_overlap(self.nodes)
        print(f"  DRC: {score:.2f}\n{detail}")
        self.assertAlmostEqual(score, 1.0, places=2)

    def test_summary_contains_layout_y(self):
        from ai_agent.placement.quality_metrics import score_placement
        report = score_placement(self.nodes, self.matching_info)
        self.assertIn("Layout Y Symmetry", report["summary"])

    def test_summary_contains_interdig(self):
        from ai_agent.placement.quality_metrics import score_placement
        report = score_placement(self.nodes, self.matching_info)
        self.assertIn("Interdigitation", report["summary"])


# ── Test: common centroid layout ─────────────────────────────────────────────

class TestCommonCentroidLayout(unittest.TestCase):
    """2D common-centroid layout -- centroid score non-N/A, interdig N/A."""

    def setUp(self):
        # 3-device palindromic pattern in 2 rows: MM0 MM1 MM2 MM2 MM1 MM0
        cc_seq = ["MM0", "MM1", "MM2", "MM2", "MM1", "MM0"]
        self.nodes = []
        for row_y in [NMOS_Y, NMOS_Y + ROW_H]:
            x = 0.0
            cnt = {}
            for dev in cc_seq:
                cnt[dev] = cnt.get(dev, 0) + 1
                self.nodes.append(_make_finger(dev, cnt[dev], x, row_y, "nmos"))
                x += STD_PITCH   # use STD_PITCH so nodes don't overlap

    def test_cc_score_not_na(self):
        from ai_agent.placement.quality_metrics import _common_centroid_accuracy
        score, detail = _common_centroid_accuracy(self.nodes)
        print(f"  CC score: {score}\n{detail}")
        self.assertIsNotNone(score, "palindromic 2-row -> CC score must not be N/A")
        self.assertGreaterEqual(score, 0.8)

    def test_interdig_is_na_for_cc_only(self):
        """3-device palindromic rows are CC, not ABBA/ABAB -> interdig N/A."""
        from ai_agent.placement.quality_metrics import _interdigitation_pattern
        score, detail = _interdigitation_pattern(self.nodes)
        print(f"  Interdig (CC-only layout): {score}\n{detail}")
        self.assertIsNone(score, "3-device rows -> interdig should be N/A")


# ── Test: no applicable groups ───────────────────────────────────────────────

class TestNoMatchedGroups(unittest.TestCase):
    """Layout with no interdigitation or CC -> both metrics are N/A."""

    def setUp(self):
        # Plain unmatched devices -- each row has only 1 device type
        self.nodes = [
            _make_finger("MM1", 0, 0.0,           NMOS_Y, "nmos"),
            _make_finger("MM1", 1, 2*FINGER_PITCH, NMOS_Y, "nmos"),
            _make_finger("MM2", 0, 0.0,           PMOS_Y, "pmos"),
            _make_finger("MM2", 1, 2*FINGER_PITCH, PMOS_Y, "pmos"),
        ]

    def test_interdig_na(self):
        from ai_agent.placement.quality_metrics import _interdigitation_pattern
        score, detail = _interdigitation_pattern(self.nodes)
        # Each row has only 1 device type -> no 2-device ABBA rows -> N/A
        self.assertIsNone(score)

    def test_cc_na(self):
        from ai_agent.placement.quality_metrics import _common_centroid_accuracy
        score, detail = _common_centroid_accuracy(self.nodes)
        self.assertIsNone(score)

    def test_composite_uses_only_available_metrics(self):
        from ai_agent.placement.quality_metrics import score_placement
        report = score_placement(self.nodes, {"matched_pairs": []})
        # Composite should still be a valid float even with N/A sub-metrics
        self.assertIsInstance(report["composite_score"], float)
        self.assertGreaterEqual(report["composite_score"], 0.0)
        self.assertLessEqual(report["composite_score"], 1.0)


# ── Test: overlapping layout ──────────────────────────────────────────────────

class TestOverlaps(unittest.TestCase):
    def test_overlap_detected(self):
        from ai_agent.placement.quality_metrics import _drc_overlap
        nodes = [
            _make_finger("A", 0, 0.0,  NMOS_Y),
            _make_finger("B", 0, 0.03, NMOS_Y),  # pitch 0.03 < MIN_PITCH=0.065
        ]
        score, detail = _drc_overlap(nodes)
        print(f"  DRC (overlap): {score:.2f}\n{detail}")
        self.assertLess(score, 1.0)


# ── Test: format_report ───────────────────────────────────────────────────────

class TestFormatReport(unittest.TestCase):
    def test_format_with_details(self):
        from ai_agent.placement.quality_metrics import score_placement, format_report
        nodes = [
            _make_finger("MM1", 0, 0.0, NMOS_Y),
            _make_finger("MM2", 0, 0.5, NMOS_Y)
        ]
        report = score_placement(nodes, {"matched_pairs": [("MM1", "MM2")]}, verbose=True)
        full = format_report(report, show_details=True)
        print(full[:300])
        self.assertIn("Per-Metric Details", full)


# ── Quick standalone run ──────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 65)
    print("  PLACEMENT QUALITY METRICS -- TEST SUITE")
    print("=" * 65 + "\n")
    unittest.main(verbosity=2)

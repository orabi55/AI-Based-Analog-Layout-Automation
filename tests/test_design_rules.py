"""
Unit tests for config/design_rules.py — verifies PDK constant consistency.
"""

import sys
import os
import pytest

_project_root = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from config.design_rules import (
    PITCH_UM, ROW_PITCH, ROW_HEIGHT_UM, FINGER_PITCH,
    PMOS_Y, NMOS_Y, PIXELS_PER_UM, BLOCK_GAP_UM, PASSIVE_ROW_GAP_UM,
)


class TestDesignRules:
    """Verify PDK constants are self-consistent."""

    def test_row_pitch_equals_row_height(self):
        assert ROW_PITCH == ROW_HEIGHT_UM

    def test_pmos_above_nmos(self):
        assert PMOS_Y > NMOS_Y

    def test_pmos_y_is_multiple_of_row_pitch(self):
        assert PMOS_Y % ROW_PITCH == pytest.approx(0.0)

    def test_block_gap_is_two_pitches(self):
        assert BLOCK_GAP_UM == PITCH_UM * 2

    def test_passive_gap_equals_pitch(self):
        assert PASSIVE_ROW_GAP_UM == PITCH_UM

    def test_finger_pitch_smaller_than_std_pitch(self):
        assert FINGER_PITCH < PITCH_UM

    def test_pixels_per_um_positive(self):
        assert PIXELS_PER_UM > 0

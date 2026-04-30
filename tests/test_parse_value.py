"""
Unit tests for parser/netlist_reader.py — focuses on the parse_value() fix
for the 'meg' suffix bug and comprehensive SPICE value parsing coverage.
"""

import sys
import os
import pytest

# Ensure project root is on path
_project_root = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from parser.netlist_reader import parse_value


class TestParseValue:
    """Tests for the SPICE value parser."""

    # ── Basic suffixes ──────────────────────────────────────────────
    def test_femto(self):
        assert parse_value("1f") == pytest.approx(1e-15)

    def test_pico(self):
        assert parse_value("60p") == pytest.approx(60e-12)

    def test_nano(self):
        assert parse_value("100n") == pytest.approx(100e-9)

    def test_micro(self):
        assert parse_value("1u") == pytest.approx(1e-6)

    def test_milli(self):
        assert parse_value("500m") == pytest.approx(0.5)

    def test_kilo(self):
        assert parse_value("10k") == pytest.approx(10e3)

    def test_giga(self):
        assert parse_value("1g") == pytest.approx(1e9)

    # ── 'meg' suffix — the bug that was fixed ───────────────────────
    def test_meg(self):
        """Regression test: '1meg' must parse as 1e6, not raise ValueError."""
        assert parse_value("1meg") == pytest.approx(1e6)

    def test_meg_multi(self):
        assert parse_value("5meg") == pytest.approx(5e6)

    def test_meg_decimal(self):
        assert parse_value("2.5meg") == pytest.approx(2.5e6)

    def test_meg_uppercase(self):
        """SPICE is case-insensitive; 'MEG' must work."""
        assert parse_value("1MEG") == pytest.approx(1e6)

    # ── 'm' vs 'meg' disambiguation ─────────────────────────────────
    def test_m_is_milli_not_mega(self):
        """'1m' is 1 milli, not 1 mega."""
        assert parse_value("1m") == pytest.approx(1e-3)

    def test_100m_is_milli(self):
        assert parse_value("100m") == pytest.approx(0.1)

    # ── Pure numbers ────────────────────────────────────────────────
    def test_pure_integer(self):
        assert parse_value("42") == pytest.approx(42.0)

    def test_pure_float(self):
        assert parse_value("3.14") == pytest.approx(3.14)

    def test_negative(self):
        assert parse_value("-1.5u") == pytest.approx(-1.5e-6)

    # ── Whitespace handling ─────────────────────────────────────────
    def test_leading_trailing_spaces(self):
        assert parse_value("  1k  ") == pytest.approx(1e3)

    # ── Edge cases ──────────────────────────────────────────────────
    def test_zero(self):
        assert parse_value("0") == pytest.approx(0.0)

    def test_zero_with_suffix(self):
        assert parse_value("0n") == pytest.approx(0.0)

    def test_small_value(self):
        assert parse_value("0.1p") == pytest.approx(0.1e-12)

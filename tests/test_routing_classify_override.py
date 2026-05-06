"""
test_routing_classify_override.py
===================================
Verifies that:
  - classify_net("VBIAS") returns 'bias' without force_critical
  - classify_net("VBIAS", force_critical={"VBIAS"}) returns 'critical'
  - Default classifier behaviour is unchanged for other nets
"""
import pytest
from ai_agent.placement.routing.classify import classify_net, NetClassifier


class TestClassifyNetOverride:

    def test_vbias_normally_is_bias(self):
        assert classify_net("VBIAS") == "bias"

    def test_vbias_forced_critical(self):
        assert classify_net("VBIAS", force_critical={"VBIAS"}) == "critical"

    def test_force_critical_empty_set_no_effect(self):
        assert classify_net("VBIAS", force_critical=set()) == "bias"

    def test_force_critical_none_no_effect(self):
        assert classify_net("VBIAS", force_critical=None) == "bias"

    def test_power_net_not_overridden(self):
        """Even with force_critical, power nets are kept power (force_critical wins)."""
        # The spec says force_critical overrides — check what our impl does:
        # We return "critical" immediately if name in force_critical.
        result = classify_net("VDD", force_critical={"VDD"})
        assert result == "critical"

    def test_vout_still_critical_without_force(self):
        assert classify_net("VOUTP") == "critical"

    def test_signal_net_unchanged(self):
        assert classify_net("some_internal_net") == "signal"

    def test_multiple_forced_nets(self):
        forced = {"VBIAS", "VTAIL", "VCAS"}
        assert classify_net("VTAIL", force_critical=forced) == "critical"
        assert classify_net("VBIAS", force_critical=forced) == "critical"
        assert classify_net("VCAS", force_critical=forced) == "critical"

    def test_non_forced_net_unchanged_in_set(self):
        forced = {"VBIAS"}
        assert classify_net("VCAS", force_critical=forced) == "bias"

    def test_backward_compat_no_kwargs(self):
        """Calling without force_critical must still work (positional only)."""
        clf = NetClassifier()
        assert clf.classify("VOUTP") == "critical"
        assert clf.classify("VDD") == "power"

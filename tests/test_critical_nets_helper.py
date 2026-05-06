"""
test_critical_nets_helper.py
============================
Unit tests for ai_agent.placement.critical_nets.

Covers: empty state, Low priority, supplies-only, duplicates,
        >10 nets, High priority, mixed supply names, devices_for_critical_nets.
"""
import pytest
from ai_agent.placement.critical_nets import (
    get_user_critical_nets,
    devices_for_critical_nets,
    PRIORITY_WEIGHTS,
    SUPPLY_NETS,
)


def _state(priority="Low", nets=None):
    """Build a minimal fake state dict for testing."""
    cfg: dict = {"priority": priority}
    if nets is not None:
        cfg["nets"] = nets
    return {"placement_goals": {"critical_nets": cfg}}


# ---------------------------------------------------------------------------
# get_user_critical_nets
# ---------------------------------------------------------------------------

class TestGetUserCriticalNets:

    def test_empty_state_returns_off(self):
        assert get_user_critical_nets({}) == ([], 0)

    def test_none_placement_goals_returns_off(self):
        assert get_user_critical_nets({"placement_goals": None}) == ([], 0)

    def test_low_priority_is_off(self):
        nets, weight = get_user_critical_nets(_state("Low", ["VOUTP"]))
        assert nets == []
        assert weight == 0

    def test_missing_priority_defaults_to_low(self):
        state = {"placement_goals": {"critical_nets": {"nets": ["VOUTP"]}}}
        nets, weight = get_user_critical_nets(state)
        assert nets == []
        assert weight == 0

    def test_supplies_only_returns_off(self):
        nets, weight = get_user_critical_nets(
            _state("High", ["VDD", "VSS", "GND", "AVDD"])
        )
        assert nets == []
        assert weight == 0

    def test_duplicates_are_deduplicated(self):
        nets, weight = get_user_critical_nets(
            _state("High", ["VOUTP", "voutp", "VOUTP", "VOUTN"])
        )
        assert len(nets) == 2
        assert weight == 10

    def test_cap_at_10_nets(self):
        many = [f"NET{i}" for i in range(20)]
        nets, weight = get_user_critical_nets(_state("High", many))
        assert len(nets) == 10
        assert weight == 10

    def test_high_priority_correct_weight(self):
        nets, weight = get_user_critical_nets(_state("High", ["VOUTP"]))
        assert weight == PRIORITY_WEIGHTS["High"] == 10
        assert "VOUTP" in nets

    def test_medium_priority_correct_weight(self):
        nets, weight = get_user_critical_nets(_state("Medium", ["VOUTP"]))
        assert weight == PRIORITY_WEIGHTS["Medium"] == 5

    def test_mixed_supply_names_dropped(self):
        nets, weight = get_user_critical_nets(
            _state("High", ["VOUTP", "VDD", "VSS", "VOUTN", "VDDA"])
        )
        assert "VDD" not in nets
        assert "VSS" not in nets
        assert "VDDA" not in nets
        assert sorted(nets) == ["VOUTN", "VOUTP"]

    def test_empty_nets_list_returns_off(self):
        assert get_user_critical_nets(_state("High", [])) == ([], 0)

    def test_unknown_priority_returns_off(self):
        state = {"placement_goals": {"critical_nets": {"priority": "Ultra", "nets": ["X"]}}}
        assert get_user_critical_nets(state) == ([], 0)


# ---------------------------------------------------------------------------
# devices_for_critical_nets
# ---------------------------------------------------------------------------

class TestDevicesForCriticalNets:

    _TERMINAL_NETS = {
        "MM1": {"D": "VOUTP", "G": "VIN",  "S": "VTAIL"},
        "MM2": {"D": "VOUTN", "G": "VIN",  "S": "VTAIL"},
        "MM3": {"D": "VOUTP", "G": "VBIAS","S": "VDD"},
        "MM4": {"D": "VDD",   "G": "VDD",  "S": "VTAIL"},
    }

    def test_basic_mapping(self):
        result = devices_for_critical_nets(self._TERMINAL_NETS, ["VOUTP"])
        assert "VOUTP" in result
        assert sorted(result["VOUTP"]) == ["MM1", "MM3"]

    def test_multi_net_mapping(self):
        result = devices_for_critical_nets(
            self._TERMINAL_NETS, ["VOUTP", "VOUTN", "VTAIL"]
        )
        assert sorted(result["VOUTP"]) == ["MM1", "MM3"]
        assert result["VOUTN"] == ["MM2"]
        assert sorted(result["VTAIL"]) == ["MM1", "MM2", "MM4"]

    def test_case_insensitive_match(self):
        result = devices_for_critical_nets(self._TERMINAL_NETS, ["voutp"])
        assert "voutp" in result
        assert sorted(result["voutp"]) == ["MM1", "MM3"]

    def test_net_with_no_devices_omitted(self):
        result = devices_for_critical_nets(self._TERMINAL_NETS, ["NONEXISTENT"])
        assert "NONEXISTENT" not in result

    def test_empty_nets_returns_empty(self):
        assert devices_for_critical_nets(self._TERMINAL_NETS, []) == {}

    def test_empty_terminal_nets_returns_empty(self):
        assert devices_for_critical_nets({}, ["VOUTP"]) == {}

    def test_device_listed_only_once_per_net(self):
        # MM1 has VTAIL on both G? No — but let's make a tricky case.
        tn = {"X": {"D": "NET1", "G": "NET1", "S": "VSS"}}
        result = devices_for_critical_nets(tn, ["NET1"])
        assert result["NET1"] == ["X"]  # not duplicated

import copy
import json
from pathlib import Path

from ai_agent.agents.routing_previewer import build_routing_report
from ai_agent.placement.critical_signal_flow import optimize_critical_signal_flow
from ai_agent.placement.quality_metrics import score_placement


def _load_comparator_placement():
    path = Path("examples/comparator/comparator_initial_placement.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _critical_goals():
    return {
        "critical_nets": {
            "priority": "High",
            "nets": ["VOUTN", "VOUTP"],
        }
    }


def _net_hpwl(report, net_name):
    return next(n.hpwl for n in report.nets if n.name == net_name)


def test_general_path_is_unchanged_when_critical_nets_are_off():
    data = _load_comparator_placement()
    nodes = data["nodes"]

    result = optimize_critical_signal_flow(
        copy.deepcopy(nodes),
        data["terminal_nets"],
        placement_goals=None,
    )

    assert result == nodes


def test_vout_critical_signal_flow_improves_routing_without_matching_loss():
    data = _load_comparator_placement()
    nodes = data["nodes"]
    terminal_nets = data["terminal_nets"]
    crit = {"VOUTN", "VOUTP"}

    before = build_routing_report(nodes, [], terminal_nets, user_critical_nets=crit)
    before_quality = score_placement(nodes, matching_info=None, verbose=False)
    result = optimize_critical_signal_flow(
        copy.deepcopy(nodes),
        terminal_nets,
        _critical_goals(),
    )
    after = build_routing_report(result, [], terminal_nets, user_critical_nets=crit)
    quality = score_placement(result, matching_info=None, verbose=False)

    assert _net_hpwl(after, "VOUTN") < _net_hpwl(before, "VOUTN")
    assert _net_hpwl(after, "VOUTP") < _net_hpwl(before, "VOUTP")
    assert after.weighted_cost < before.weighted_cost
    assert after.estimated_crossings <= before.estimated_crossings
    assert quality["composite_score"] >= before_quality["composite_score"]


"""Critical-signal physical optimization for initial placement.

This module is intentionally deterministic and gated by
placement_goals["critical_nets"].  When the feature is off it returns the
input placement unchanged, preserving the general initial-placement path.
"""

from __future__ import annotations

import copy
import re
from collections import defaultdict
from typing import Iterable


_FINGER_SUFFIX_RE = re.compile(r"^(.+?)(?:_[mf]\d+)+$", re.IGNORECASE)
_DUMMY_PREFIXES = ("FILLER_DUMMY_", "EDGE_DUMMY", "DUMMY_matrix_", "DUMMY_MATRIX_")


def _node_id(node: dict) -> str:
    return str(node.get("id", ""))


def _is_dummy(node: dict) -> bool:
    nid = _node_id(node)
    return bool(node.get("is_dummy") or nid.startswith(_DUMMY_PREFIXES))


def _logical_id(device_id: str) -> str:
    m = _FINGER_SUFFIX_RE.match(str(device_id))
    return m.group(1) if m else str(device_id)


def _logical_candidates(device_id: str) -> set[str]:
    did = str(device_id or "")
    out = {did, _logical_id(did)}
    out.add(re.sub(r"_f\d+$", "", did))
    out.add(re.sub(r"_m\d+$", "", did))
    out.add(re.sub(r"_\d+$", "", did))
    return {x for x in out if x}


def _dev_type(node: dict) -> str:
    dtype = str(node.get("type", "nmos")).lower()
    return "pmos" if "pmos" in dtype or "p_mos" in dtype else "nmos"


def _row_key(node: dict) -> tuple[str, float]:
    geo = node.get("geometry") or {}
    return _dev_type(node), round(float(geo.get("y", 0.0)), 6)


def _nets_for_candidates(candidates: Iterable[str], terminal_nets: dict) -> set[str]:
    nets: set[str] = set()
    for cand in candidates:
        pins = terminal_nets.get(cand)
        if not isinstance(pins, dict):
            continue
        for net_name in pins.values():
            if isinstance(net_name, str) and net_name.strip():
                nets.add(net_name.strip().lower())
    return nets


def _critical_hpwl(report, crit_lower: set[str]) -> float:
    return sum(
        float(n.hpwl)
        for n in getattr(report, "nets", [])
        if str(getattr(n, "name", "")).lower() in crit_lower
    )


def _routing_report(nodes: list, terminal_nets: dict, crit_nets: list[str]):
    from ai_agent.agents.routing_previewer import build_routing_report

    return build_routing_report(
        nodes,
        [],
        terminal_nets or {},
        user_critical_nets=set(crit_nets),
    )


def _row_order_towards_nmos(
    pmos_rows: list[tuple[str, float]],
    critical_rows: set[tuple[str, float]],
    nmos_anchor_y: float,
    weight: int,
) -> list[tuple[str, float]]:
    """Return a minimally perturbed PMOS row order that pulls critical rows toward NMOS.

    Uses adjacent swaps only (stable/local), so the output remains close to the
    general initial-placement structure.
    """
    if len(pmos_rows) < 2 or not critical_rows:
        return list(pmos_rows)

    order = list(pmos_rows)
    index = {row: i for i, row in enumerate(order)}
    pmos_mean = sum(y for _, y in pmos_rows) / float(len(pmos_rows))

    # If NMOS anchor lies below PMOS stack, move critical rows toward lower Y slots.
    step = -1 if nmos_anchor_y <= pmos_mean else 1
    max_swaps_per_row = 1 if weight <= 5 else len(pmos_rows)

    # Pull rows in proximity priority: farthest critical rows move first.
    critical_sorted = sorted(
        critical_rows,
        key=lambda row: abs(row[1] - nmos_anchor_y),
        reverse=True,
    )
    for row in critical_sorted:
        if row not in index:
            continue
        swaps = 0
        while swaps < max_swaps_per_row:
            i = index[row]
            j = i + step
            if j < 0 or j >= len(order):
                break
            neighbor = order[j]
            if neighbor in critical_rows:
                break
            order[i], order[j] = order[j], order[i]
            index[order[i]] = i
            index[order[j]] = j
            swaps += 1
    return order


def _apply_pmos_row_order(
    nodes: list[dict],
    rows: dict[tuple[str, float], list[dict]],
    pmos_rows: list[tuple[str, float]],
    desired_order: list[tuple[str, float]],
) -> list[dict]:
    """Clone nodes and remap PMOS row Y-values according to desired order."""
    if desired_order == pmos_rows:
        return copy.deepcopy(nodes)

    working = copy.deepcopy(nodes)
    work_rows: dict[tuple[str, float], list[dict]] = defaultdict(list)
    for node in working:
        work_rows[_row_key(node)].append(node)

    y_slots = [key[1] for key in pmos_rows]
    old_to_new_y = {old_key[1]: y_slots[idx] for idx, old_key in enumerate(desired_order)}
    for key in pmos_rows:
        old_y = key[1]
        new_y = old_to_new_y.get(old_y, old_y)
        if abs(new_y - old_y) < 1e-9:
            continue
        for node in work_rows[key]:
            geo = node.setdefault("geometry", {})
            geo["y"] = round(new_y, 6)
    return working


def optimize_critical_signal_flow(
    nodes: list,
    terminal_nets: dict | None,
    placement_goals: dict | None,
) -> list:
    """Reorder whole PMOS rows for selected critical output nets.

    The comparator case exposes the main reason this exists: VOUTP/VOUTN can
    remain vertically long when noncritical PMOS rows sit between the NMOS latch
    row and output-critical PMOS rows.  Moving entire PMOS rows keeps internal
    matching untouched while improving signal flow.
    """
    try:
        from ai_agent.placement.critical_nets import get_user_critical_nets
    except ImportError:
        return nodes

    if not nodes or not terminal_nets:
        return nodes

    crit_nets, weight = get_user_critical_nets({"placement_goals": placement_goals or {}})
    if not crit_nets or weight <= 0:
        return nodes

    crit_lower = {n.lower() for n in crit_nets}
    original = copy.deepcopy(nodes)
    base = copy.deepcopy(nodes)

    rows: dict[tuple[str, float], list[dict]] = defaultdict(list)
    for node in base:
        rows[_row_key(node)].append(node)

    nmos_ys = [key[1] for key in rows if key[0] == "nmos"]
    pmos_rows = sorted(
        [key for key in rows if key[0] == "pmos"],
        key=lambda key: key[1],
    )
    if len(pmos_rows) < 2 or not nmos_ys:
        return nodes

    def row_is_critical(row_nodes: list[dict]) -> bool:
        for node in row_nodes:
            if _is_dummy(node):
                continue
            candidates = _logical_candidates(_node_id(node))
            nets = _nets_for_candidates(candidates, terminal_nets)
            if nets & crit_lower:
                return True
        return False

    critical_rows = [key for key in pmos_rows if row_is_critical(rows[key])]
    critical_set = set(critical_rows)
    noncritical_rows = [key for key in pmos_rows if key not in critical_set]
    if not critical_rows or not noncritical_rows:
        return nodes

    nmos_anchor_y = sum(nmos_ys) / float(len(nmos_ys))
    local_order = _row_order_towards_nmos(
        pmos_rows=pmos_rows,
        critical_rows=critical_set,
        nmos_anchor_y=nmos_anchor_y,
        weight=weight,
    )
    global_order = critical_rows + noncritical_rows

    def _order_distance(order: list[tuple[str, float]]) -> int:
        idx_old = {row: i for i, row in enumerate(pmos_rows)}
        return sum(abs(i - idx_old[row]) for i, row in enumerate(order))

    candidate_orders: list[list[tuple[str, float]]] = []
    candidate_pool = [local_order]
    # High priority may use the stronger global regrouping fallback.
    if weight > 5:
        candidate_pool.append(global_order)
    for order in candidate_pool:
        if order != pmos_rows and order not in candidate_orders:
            candidate_orders.append(order)
    if not candidate_orders:
        return nodes

    candidate_nodes: list[tuple[str, list[tuple[str, float]], list[dict], int]] = []
    for order in candidate_orders:
        tag = "local" if order == local_order else "global"
        candidate_nodes.append(
            (tag, order, _apply_pmos_row_order(base, rows, pmos_rows, order), _order_distance(order))
        )

    try:
        before = _routing_report(original, terminal_nets, crit_nets)
        before_crit = _critical_hpwl(before, crit_lower)
        before_cost = float(getattr(before, "weighted_cost", 0.0))
        before_cross = int(getattr(before, "estimated_crossings", 0))

        accepted: list[tuple[float, float, int, int, str, list[dict], str]] = []
        for tag, order, cand_nodes, order_dist in candidate_nodes:
            report = _routing_report(cand_nodes, terminal_nets, crit_nets)
            after_crit = _critical_hpwl(report, crit_lower)
            after_cost = float(getattr(report, "weighted_cost", 0.0))
            after_cross = int(getattr(report, "estimated_crossings", 0))

            improves_critical = after_crit < before_crit - 1e-6
            if weight <= 5:
                max_cost_ratio = 1.03
                max_cross_delta = 1
            else:
                max_cost_ratio = 1.05
                max_cross_delta = 2
            bounded_global = (
                after_cost <= before_cost * max_cost_ratio + 1e-9 and
                after_cross <= before_cross + max_cross_delta
            )
            print(
                "[critical_signal_flow] candidate "
                f"{tag}: crit_hpwl {before_crit:.3f}->{after_crit:.3f}, "
                f"cost {before_cost:.1f}->{after_cost:.1f}, "
                f"cross {before_cross}->{after_cross}, "
                f"row_shift={order_dist}"
            )
            if improves_critical and bounded_global:
                accepted.append(
                    (after_crit, after_cost, after_cross, order_dist, tag, cand_nodes, str(order))
                )

        if not accepted:
            print("[critical_signal_flow] rejected: no accepted candidate")
            return nodes

        # Best critical HPWL first; tie-break by smaller total cost, fewer crossings,
        # and minimal row displacement to preserve general-placement shape.
        accepted.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
        best = accepted[0]
        print(
            "[critical_signal_flow] accepted: "
            f"type={best[4]}, crit_hpwl={best[0]:.3f}, "
            f"cost={best[1]:.1f}, cross={best[2]}, row_shift={best[3]}"
        )
        return best[5]
    except Exception as exc:
        print(f"[critical_signal_flow] skipped quality check: {exc}")
        return nodes

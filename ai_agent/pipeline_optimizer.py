"""
ai_agent/pipeline_optimizer.py
==============================

Deterministic placement optimizer layer.

Responsibilities:
    1. Row-level cost-driven ordering (greedy O(n³))
    2. Deterministic symmetry enforcement for diff pairs
    3. Exclude dummy devices from optimization
"""

import copy
import re
from collections import defaultdict
from ai_agent.routing_previewer import score_routing


# -----------------------------------------------------------------------------
# Public Entry
# -----------------------------------------------------------------------------
def apply_deterministic_optimizations(
    working_nodes,
    constraint_text,
    terminal_nets,
    edges,
    pitch=0.294,
    placement_mode: str = "auto",
):
    """
    Apply deterministic row optimization and symmetry enforcement.

    Args:
        working_nodes:   List of device node dicts.
        constraint_text: Topology constraint string from Stage 1.
        terminal_nets:   Dict of terminal net names → net objects.
        edges:           List of edge dicts (used by routing scorer).
        pitch:           Device pitch in µm (default 0.294).
        placement_mode:  "interdigitated" | "common_centroid" | "auto" —
                         passed through for callers that need it; the
                         deterministic optimizer is mode-agnostic.

    Returns a NEW node list (does not mutate original input).
    """
    if not working_nodes:
        return working_nodes

    nodes = copy.deepcopy(working_nodes)

    # 1️⃣ Optimize rows (excluding dummies)
    nodes = _optimize_rows(nodes, terminal_nets, edges, pitch)

    # 2️⃣ Enforce symmetry
    nodes = _enforce_symmetry(nodes, constraint_text, pitch)

    return nodes


# -----------------------------------------------------------------------------
# Row Optimization
# -----------------------------------------------------------------------------
def _optimize_rows(nodes, terminal_nets, edges, pitch):
    """
    Greedy cost-minimizing row optimizer.
    Excludes dummy devices from ordering.
    """
    rows = defaultdict(list)

    for n in nodes:
        if not n.get("is_dummy"):
            y = round(float(n["geometry"]["y"]), 4)
            rows[y].append(n)

    for y_val, row_nodes in rows.items():
        if len(row_nodes) <= 2:
            continue

        row_ids = [n["id"] for n in row_nodes]
        # Evaluate baseline cost
        _apply_row_order(nodes, y_val, row_ids, pitch)
        best_cost = score_routing(nodes, edges, terminal_nets)["placement_cost"]
        best_order = row_ids.copy()

        for seed_id in row_ids:
            remaining = [r for r in row_ids if r != seed_id]
            order = [seed_id]

            while remaining:
                best_candidate = None
                best_candidate_cost = float("inf")

                for cand in remaining:
                    trial_order = order + [cand]
                    
                    # Temporarily apply trial order
                    _apply_row_order(nodes, y_val, trial_order, pitch)

                    cost = score_routing(
                        nodes, edges, terminal_nets
                    )["placement_cost"]

                    if cost < best_candidate_cost:
                        best_candidate_cost = cost
                        best_candidate = cand

                if best_candidate is not None:
                    order.append(best_candidate)
                    remaining.remove(best_candidate)
                else:
                    break

            if not remaining:
                # Evaluate final order
                _apply_row_order(nodes, y_val, order, pitch)
                final_cost = score_routing(
                    nodes, edges, terminal_nets
                )["placement_cost"]

                if final_cost < best_cost:
                    best_cost = final_cost
                    best_order = order.copy()

        # Apply best order permanently
        if best_order is not None:
            _apply_row_order(nodes, y_val, best_order, pitch)

    return nodes


def _apply_row_order(nodes, y_val, ordered_ids, pitch):
    """
    Assign X positions for ordered_ids on row y_val.
    """
    x_start = 0.0
    id_map = {n["id"]: n for n in nodes}

    for i, dev_id in enumerate(ordered_ids):
        node = id_map[dev_id]
        node["geometry"]["x"] = x_start + i * pitch


# -----------------------------------------------------------------------------
# Symmetry Enforcement
# -----------------------------------------------------------------------------
def _enforce_symmetry(nodes, constraint_text, pitch):
    """
    Detect diff-pair patterns from topology constraints and
    enforce symmetric placement about row midpoint.
    """
    id_map = {n["id"]: n for n in nodes}

    # Extract diff-pair candidates using arrow notation
    pairs = []
    for line in constraint_text.splitlines():
        if "DIFF" in line.upper():
            matches = re.findall(r'(\w+)\s*↔\s*(\w+)', line)
            pairs.extend(matches)

    if not pairs:
        return nodes

    # Compute row midpoints
    row_bounds = defaultdict(lambda: {"min": float("inf"), "max": -float("inf")})

    for n in nodes:
        y = round(float(n["geometry"]["y"]), 4)
        x = float(n["geometry"]["x"])
        row_bounds[y]["min"] = min(row_bounds[y]["min"], x)
        row_bounds[y]["max"] = max(row_bounds[y]["max"], x)

    row_midpoints = {
        y: (b["min"] + b["max"]) / 2.0
        for y, b in row_bounds.items()
    }

    for a, b in pairs:
        if a in id_map and b in id_map:
            node_a = id_map[a]
            node_b = id_map[b]

            y_val = round(float(node_a["geometry"]["y"]), 4)
            center = row_midpoints.get(y_val, 0.0)

            node_a["geometry"]["x"] = center - pitch
            node_b["geometry"]["x"] = center + pitch

    return nodes
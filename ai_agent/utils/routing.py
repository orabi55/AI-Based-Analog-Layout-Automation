"""
Routing Utilities
=================
Provides helper functions for analyzing and optimizing routing in the layout.

Functions:
- generate_targeted_swaps: Generates swap commands to reduce wire length on critical nets.
  - Inputs: nodes (list), worst_nets (list), terminal_nets (dict)
  - Outputs: list of swap command dicts.
"""

from typing import List, Dict

def generate_targeted_swaps(
    nodes: List[dict],
    worst_nets: List[str],
    terminal_nets: Dict[str, dict]
) -> List[dict]:
    """
    Generate swap commands targeting highest-cost nets.

    For each worst net:
      - Find leftmost and rightmost devices on that net
      - Propose swapping each with its immediate neighbor (if not on same net)
      - Goal: shorten wire span

    Args:
        nodes: Current node list
        worst_nets: List of net names with highest routing cost
        terminal_nets: Device terminal connections

    Returns:
        List of swap command dicts
    """
    if not worst_nets or not nodes:
        return []

    # Build net → devices mapping
    net_to_devices = {}
    for node in nodes:
        dev_id = node["id"]
        nets = terminal_nets.get(dev_id, {})
        for net in nets.values():
            net_to_devices.setdefault(net, []).append(node)

    # Sort nodes by row and X for neighbor lookup
    sorted_nodes = sorted(
        nodes,
        key=lambda n: (
            round(float(n["geometry"]["y"]), 2),
            float(n["geometry"]["x"])
        )
    )
    index_map = {n["id"]: i for i, n in enumerate(sorted_nodes)}

    swap_cmds = []
    seen_pairs = set()

    for net in worst_nets:
        net_devices = net_to_devices.get(net, [])
        if len(net_devices) < 2:
            continue

        net_devices_sorted = sorted(
            net_devices,
            key=lambda n: float(n["geometry"]["x"])
        )

        left_node  = net_devices_sorted[0]
        right_node = net_devices_sorted[-1]
        net_ids    = {d["id"] for d in net_devices}

        left_idx  = index_map.get(left_node["id"],  -1)
        right_idx = index_map.get(right_node["id"], -1)

        # Try swapping left device with its right neighbor
        if 0 <= left_idx < len(sorted_nodes) - 1:
            neighbor = sorted_nodes[left_idx + 1]
            n_id = neighbor["id"]
            pair = tuple(sorted([left_node["id"], n_id]))

            if n_id not in net_ids and pair not in seen_pairs:
                swap_cmds.append({
                    "action":   "swap",
                    "device_a": left_node["id"],
                    "device_b": n_id,
                })
                seen_pairs.add(pair)

        # Try swapping right device with its left neighbor
        if right_idx > 0:
            neighbor = sorted_nodes[right_idx - 1]
            n_id = neighbor["id"]
            pair = tuple(sorted([right_node["id"], n_id]))

            if n_id not in net_ids and pair not in seen_pairs:
                swap_cmds.append({
                    "action":   "swap",
                    "device_a": right_node["id"],
                    "device_b": n_id,
                })
                seen_pairs.add(pair)

    return swap_cmds

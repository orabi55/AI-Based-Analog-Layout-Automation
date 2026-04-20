"""
sa_optimizer.py
================
Lightweight Simulated Annealing (SA) post-optimization for AI-generated
transistor placements.

Takes an AI-generated placement and optimises device ordering within each
row to minimise total Half-Perimeter Wire Length (HPWL).  Abutment chains
are never broken — only standalone (non-chained) devices are eligible for
position swaps.

Typical runtime: 1–3 seconds for circuits up to ~100 devices.
"""

import math
import random
import copy
from collections import defaultdict


# ---------------------------------------------------------------------------
#  HPWL evaluation
# ---------------------------------------------------------------------------
_POWER_NETS = frozenset({"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS"})


def _compute_hpwl(nodes: list, edges: list) -> float:
    """
    Compute total Half-Perimeter Wire Length (HPWL) across all signal nets.

    Parameters
    ----------
    nodes : list
        List of node dictionaries containing geometry (x, y) coordinates.
    edges : list
        List of edge dictionaries describing source/target connections.

    Returns
    -------
    float
        The total computed wire length in micrometers.
    """
    pos: dict[str, tuple[float, float]] = {}
    for n in nodes:
        if not isinstance(n, dict):
            continue
        nid = n.get("id", "")
        geo = n.get("geometry", {})
        pos[nid] = (geo.get("x", 0.0), geo.get("y", 0.0))

    net_devices: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        net = e.get("net", "")
        if not net or net.upper() in _POWER_NETS:
            continue
        src = e.get("source", "")
        tgt = e.get("target", "")
        if src:
            net_devices[net].add(src)
        if tgt:
            net_devices[net].add(tgt)

    total = 0.0
    for devs in net_devices.values():
        xs, ys = [], []
        for d in devs:
            if d in pos:
                xs.append(pos[d][0])
                ys.append(pos[d][1])
        if len(xs) >= 2:
            total += (max(xs) - min(xs)) + (max(ys) - min(ys))
    return total


# ---------------------------------------------------------------------------
#  Row re-packing helper
# ---------------------------------------------------------------------------
_ABUT_SPACING = 0.070
_STD_PITCH = 0.294


def _repack_row(row_nodes: list[dict],
                abut_right_ids: set[str],
                abut_left_ids: set[str]) -> None:
    """
    Re-assign exact X coordinates for devices in a single row based on their list order.

    Iterates through the provided ordered list of devices. If two adjacent
    devices form a valid abutment pair, they are placed exactly 0.070µm apart.
    Otherwise, they are spaced by the width of the preceding device (default 0.294µm).

    Parameters
    ----------
    row_nodes : list[dict]
        List of node dictionaries in the desired geometric order.
        Geometries will be mutated in place.
    abut_right_ids : set[str]
        Set of device IDs that require right-side abutment.
    abut_left_ids : set[str]
        Set of device IDs that require left-side abutment.

    Returns
    -------
    None
        Modifies *row_nodes* in place.
    """
    if not row_nodes:
        return
    cursor = row_nodes[0].get("geometry", {}).get("x", 0.0)
    for i, dev in enumerate(row_nodes):
        geo = dev.setdefault("geometry", {})
        geo["x"] = round(cursor, 6)
        if i < len(row_nodes) - 1:
            nxt = row_nodes[i + 1]
            is_abutted = (dev.get("id", "") in abut_right_ids
                          and nxt.get("id", "") in abut_left_ids)
            if is_abutted:
                cursor = round(cursor + _ABUT_SPACING, 6)
            else:
                dev_w = geo.get("width", _STD_PITCH)
                cursor = round(cursor + dev_w, 6)


# ---------------------------------------------------------------------------
#  Main SA optimiser
# ---------------------------------------------------------------------------
def optimize_placement(nodes: list,
                       edges: list,
                       abutment_candidates: list | None = None,
                       iterations: int = 5000,
                       initial_temp: float = 1.0,
                       cooling_rate: float = 0.995) -> list:
    """Simulated Annealing optimiser for transistor placement.

    Swaps pairs of devices **within the same row** to minimise HPWL.
    After every swap the row is deterministically re-packed to maintain
    correct spacing.  Abutment chains are kept intact — only standalone
    devices are eligible for swapping.

    Parameters
    ----------
    nodes               : list of placed-node dicts (with geometry).
    edges               : list of edge dicts (with net, source, target).
    abutment_candidates : list of ``{dev_a, dev_b, ...}`` dicts.
    iterations          : total SA iterations (higher = slower but better).
    initial_temp        : starting temperature.
    cooling_rate        : multiplicative cooling factor per iteration.

    Returns
    -------
    list — a deep-copied, optimised version of *nodes*.
    """
    if not nodes or len(nodes) < 2:
        return nodes

    working = copy.deepcopy(nodes)

    # ── Build abutment sets ─────────────────────────────────────────────
    abut_right_ids: set[str] = set()
    abut_left_ids: set[str] = set()
    if abutment_candidates:
        for c in abutment_candidates:
            abut_right_ids.add(c["dev_a"])
            abut_left_ids.add(c["dev_b"])
    for n in working:
        abut = n.get("abutment", {})
        if abut.get("abut_right"):
            abut_right_ids.add(n.get("id", ""))
        if abut.get("abut_left"):
            abut_left_ids.add(n.get("id", ""))

    chain_member_ids = abut_right_ids | abut_left_ids

    # ── Group into rows (by Y coordinate) ───────────────────────────────
    row_buckets: dict[float, list[dict]] = defaultdict(list)
    for n in working:
        if not isinstance(n, dict):
            continue
        y = round(float(n.get("geometry", {}).get("y", 0.0)), 3)
        row_buckets[y].append(n)

    # Sort each row by current X
    for y_key in row_buckets:
        row_buckets[y_key].sort(
            key=lambda n: n.get("geometry", {}).get("x", 0.0)
        )

    # ── Identify swappable indices per row ──────────────────────────────
    swappable: dict[float, list[int]] = {}
    for y_key, row_nodes in row_buckets.items():
        idxs = [
            i for i, n in enumerate(row_nodes)
            if n.get("id", "") not in chain_member_ids
        ]
        if len(idxs) >= 2:
            swappable[y_key] = idxs

    if not swappable:
        print("[SA] No swappable devices (all in abutment chains). Skipping.")
        return working

    active_rows = list(swappable.keys())

    # ── Initial HPWL ────────────────────────────────────────────────────
    initial_hpwl = _compute_hpwl(working, edges)
    current_hpwl = initial_hpwl
    temperature = initial_temp
    accepted = 0

    print(f"[SA] Starting: HPWL = {initial_hpwl:.4f} µm, "
          f"{iterations} iterations, {len(active_rows)} active row(s)")

    # ── Main loop ───────────────────────────────────────────────────────
    for _ in range(iterations):
        y_key = random.choice(active_rows)
        row = row_buckets[y_key]
        idxs = swappable[y_key]

        # Pick two random swappable positions
        i_idx, j_idx = random.sample(idxs, 2)

        # Swap the two devices in the row list
        row[i_idx], row[j_idx] = row[j_idx], row[i_idx]

        # Re-pack the row to get valid X coordinates
        _repack_row(row, abut_right_ids, abut_left_ids)

        new_hpwl = _compute_hpwl(working, edges)
        delta = new_hpwl - current_hpwl

        if delta < 0 or random.random() < math.exp(-delta / max(temperature, 1e-10)):
            current_hpwl = new_hpwl
            accepted += 1
        else:
            # Revert
            row[i_idx], row[j_idx] = row[j_idx], row[i_idx]
            _repack_row(row, abut_right_ids, abut_left_ids)

        temperature *= cooling_rate

    final_hpwl = _compute_hpwl(working, edges)
    improvement = ((initial_hpwl - final_hpwl) / max(initial_hpwl, 1e-10)) * 100

    print(f"[SA] Done: HPWL {initial_hpwl:.4f} → {final_hpwl:.4f} µm "
          f"({improvement:+.1f}%), accepted {accepted}/{iterations}")

    return working

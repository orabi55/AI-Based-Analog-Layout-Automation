"""Comparator chat-state fixture for backend chatbot tests."""

from __future__ import annotations

import copy


def _comparator_nodes() -> list[dict]:
    return [
        {"id": "MM0", "type": "pmos", "geometry": {"x": -2.0, "y": 4.0, "width": 0.294, "height": 0.668}, "D": "VX", "G": "VINP", "S": "VDD"},
        {"id": "MM1", "type": "pmos", "geometry": {"x": -1.0, "y": 4.0, "width": 0.294, "height": 0.668}, "D": "VOUTN", "G": "CLK", "S": "VDD"},
        {"id": "MM2", "type": "pmos", "geometry": {"x": 1.0, "y": 4.0, "width": 0.294, "height": 0.668}, "D": "VOUTP", "G": "CLK", "S": "VDD"},
        {"id": "MM3", "type": "pmos", "geometry": {"x": 2.0, "y": 4.0, "width": 0.294, "height": 0.668}, "D": "VY", "G": "VINN", "S": "VDD"},
        {"id": "MM4", "type": "pmos", "geometry": {"x": -0.5, "y": 3.0, "width": 0.294, "height": 0.668}, "D": "VOUTN", "G": "VOUTP", "S": "VDD"},
        {"id": "MM5", "type": "pmos", "geometry": {"x": 0.5, "y": 3.0, "width": 0.294, "height": 0.668}, "D": "VOUTP", "G": "VOUTN", "S": "VDD"},
        {"id": "MM6", "type": "nmos", "geometry": {"x": 0.5, "y": 1.0, "width": 0.294, "height": 0.668}, "D": "VOUTP", "G": "VOUTN", "S": "VY"},
        {"id": "MM7", "type": "nmos", "geometry": {"x": -0.5, "y": 1.0, "width": 0.294, "height": 0.668}, "D": "VOUTN", "G": "VOUTP", "S": "VX"},
        {"id": "MM8", "type": "nmos", "geometry": {"x": -0.8, "y": 0.0, "width": 0.294, "height": 0.668}, "D": "VX", "G": "VINP", "S": "net2<3>"},
        {"id": "MM9", "type": "nmos", "geometry": {"x": 0.8, "y": 0.0, "width": 0.294, "height": 0.668}, "D": "VY", "G": "VINN", "S": "net2<3>"},
        {"id": "MM10", "type": "nmos", "geometry": {"x": 0.0, "y": -1.0, "width": 0.294, "height": 0.668}, "D": "net2<3>", "G": "CLK", "S": "GND"},
    ]


def _terminal_nets() -> dict:
    return {
        "MM0": {"D": "VX", "G": "VINP", "S": "VDD"},
        "MM1": {"D": "VOUTN", "G": "CLK", "S": "VDD"},
        "MM2": {"D": "VOUTP", "G": "CLK", "S": "VDD"},
        "MM3": {"D": "VY", "G": "VINN", "S": "VDD"},
        "MM4": {"D": "VOUTN", "G": "VOUTP", "S": "VDD"},
        "MM5": {"D": "VOUTP", "G": "VOUTN", "S": "VDD"},
        "MM6": {"D": "VOUTP", "G": "VOUTN", "S": "VY"},
        "MM7": {"D": "VOUTN", "G": "VOUTP", "S": "VX"},
        "MM8": {"D": "VX", "G": "VINP", "S": "net2<3>"},
        "MM9": {"D": "VY", "G": "VINN", "S": "net2<3>"},
        "MM10": {"D": "net2<3>", "G": "CLK", "S": "GND"},
    }


def _edges() -> list[dict]:
    return [
        {"src": "MM8:D", "dst": "VX"},
        {"src": "MM9:D", "dst": "VY"},
        {"src": "MM10:D", "dst": "net2<3>"},
        {"src": "MM5:D", "dst": "VOUTP"},
        {"src": "MM2:D", "dst": "VOUTP"},
        {"src": "MM6:D", "dst": "VOUTP"},
        {"src": "MM4:D", "dst": "VOUTN"},
        {"src": "MM1:D", "dst": "VOUTN"},
        {"src": "MM7:D", "dst": "VOUTN"},
    ]


def _initial_agent_trace() -> dict:
    return {
        "topology": {
            "CIRCUIT_TYPE": "Dynamic latch-based comparator",
            "summary": (
                "TAIL_CURRENT_SOURCE MM10; "
                "INPUT_DIFFERENTIAL_PAIR MM8 MM9; "
                "PRECHARGE_LOAD MM0 MM3 MM1 MM2; "
                "CROSS_COUPLED_LATCH MM4 MM5 MM6 MM7"
            ),
        },
        "strategy": {
            "matching_groups": [
                ["MM8", "MM9"],
                ["MM0", "MM3"],
                ["MM4", "MM5"],
                ["MM6", "MM7"],
                ["MM1", "MM2"],
            ],
            "notes": "Common-centroid-style intent for critical pairs with interdigitated fingers where feasible.",
        },
        "drc": {"pass": True, "flags": []},
        "routing": {
            "pass": True,
            "log_text": "Routing preview: critical nets connected with low crossing count.",
        },
    }


def make_comparator_chat_state(user_message: str) -> dict:
    """Build a representative comparator session state for chatbot tests."""
    nodes = _comparator_nodes()
    state = {
        "mode": "chat",
        "user_message": user_message,
        "selected_model": "Gemini",
        "chat_history": [],
        "nodes": copy.deepcopy(nodes),
        "placement_nodes": copy.deepcopy(nodes),
        "edges": copy.deepcopy(_edges()),
        "terminal_nets": copy.deepcopy(_terminal_nets()),
        "initial_agent_trace": copy.deepcopy(_initial_agent_trace()),
        "pending_cmds": [],
        "session_commands": [],
    }
    return state


# ---------------------------------------------------------------------------
# Finger-level fixture (for device resolver / matching tests)
# ---------------------------------------------------------------------------

def _finger_nodes() -> list[dict]:
    """Build placement nodes with physical fingers.

    Finger counts:
    - MM8, MM9: 8 fingers each (input diff pair), ABAB ordering
    - MM10: 4 fingers (tail current source)
    - MM0, MM3: 4 fingers each (precharge load), ABAB ordering
    - MM1, MM2: 4 fingers each (output precharge), ABAB ordering
    - MM4, MM5: 4 fingers each (PMOS latch), ABBA ordering
    - MM6, MM7: 1 finger each (NMOS latch, single-finger)
    """
    logical = _comparator_nodes()
    logical_map = {n["id"]: n for n in logical}
    finger_nodes: list[dict] = []

    def _expand(dev_id: str, n_fingers: int, base_x: float, base_y: float,
                dx: float = 0.3) -> list[dict]:
        parent = logical_map[dev_id]
        fingers = []
        for i in range(n_fingers):
            fingers.append({
                "id": f"{dev_id}_m{i + 1}",
                "parent_id": dev_id,
                "type": parent["type"],
                "geometry": {"x": base_x + i * dx, "y": base_y,
                             "width": 0.294, "height": 0.668},
                "D": parent["D"], "G": parent["G"], "S": parent["S"],
            })
        return fingers

    def _interleave_abab(dev_a: str, dev_b: str, n_each: int,
                          base_x: float, base_y: float, dx: float = 0.3):
        """ABAB: A1 B1 A2 B2 A3 B3 ..."""
        pa, pb = logical_map[dev_a], logical_map[dev_b]
        nodes_out = []
        ai, bi = 1, 1
        for i in range(n_each * 2):
            if i % 2 == 0:
                nodes_out.append({
                    "id": f"{dev_a}_m{ai}", "parent_id": dev_a,
                    "type": pa["type"],
                    "geometry": {"x": base_x + i * dx, "y": base_y,
                                 "width": 0.294, "height": 0.668},
                    "D": pa["D"], "G": pa["G"], "S": pa["S"],
                })
                ai += 1
            else:
                nodes_out.append({
                    "id": f"{dev_b}_m{bi}", "parent_id": dev_b,
                    "type": pb["type"],
                    "geometry": {"x": base_x + i * dx, "y": base_y,
                                 "width": 0.294, "height": 0.668},
                    "D": pb["D"], "G": pb["G"], "S": pb["S"],
                })
                bi += 1
        return nodes_out

    def _interleave_abba(dev_a: str, dev_b: str, n_each: int,
                          base_x: float, base_y: float, dx: float = 0.3):
        """ABBA: A1 B1 B2 A2 (symmetric)."""
        pa, pb = logical_map[dev_a], logical_map[dev_b]
        # Build order: for n_each=4 -> ABBAABBA -> A1 B1 B2 A2 A3 B3 B4 A4
        pattern = []
        for i in range(n_each):
            if i % 2 == 0:
                pattern.append(dev_a)
                pattern.append(dev_b)
            else:
                pattern.append(dev_b)
                pattern.append(dev_a)
        nodes_out = []
        counters = {dev_a: 1, dev_b: 1}
        for idx, dev in enumerate(pattern):
            p = logical_map[dev]
            fid = f"{dev}_m{counters[dev]}"
            counters[dev] += 1
            nodes_out.append({
                "id": fid, "parent_id": dev,
                "type": p["type"],
                "geometry": {"x": base_x + idx * dx, "y": base_y,
                             "width": 0.294, "height": 0.668},
                "D": p["D"], "G": p["G"], "S": p["S"],
            })
        return nodes_out

    # MM8/MM9: 8 fingers each, ABAB, row y=0
    finger_nodes.extend(_interleave_abab("MM8", "MM9", 8, base_x=-2.4, base_y=0.0))

    # MM10: 4 fingers, row y=-1
    finger_nodes.extend(_expand("MM10", 4, base_x=-0.6, base_y=-1.0))

    # MM0/MM3: 4 fingers each, ABAB, row y=4
    finger_nodes.extend(_interleave_abab("MM0", "MM3", 4, base_x=-2.0, base_y=4.0))

    # MM1/MM2: 4 fingers each, ABAB, row y=4 (right side)
    finger_nodes.extend(_interleave_abab("MM1", "MM2", 4, base_x=1.0, base_y=4.0))

    # MM4/MM5: 4 fingers each, ABBA, row y=3
    finger_nodes.extend(_interleave_abba("MM4", "MM5", 4, base_x=-1.2, base_y=3.0))

    # MM6/MM7: single finger
    finger_nodes.append({
        "id": "MM6", "type": "nmos",
        "geometry": {"x": 0.5, "y": 1.0, "width": 0.294, "height": 0.668},
        "D": "VOUTP", "G": "VOUTN", "S": "VY",
    })
    finger_nodes.append({
        "id": "MM7", "type": "nmos",
        "geometry": {"x": -0.5, "y": 1.0, "width": 0.294, "height": 0.668},
        "D": "VOUTN", "G": "VOUTP", "S": "VX",
    })

    return finger_nodes


def _matched_blocks() -> dict:
    """Matched-block metadata for the finger-level fixture."""
    return {
        "MM8_MM9_matched": {
            "devices": ["MM8", "MM9"],
            "technique": "ABAB_diff_pair",
            "description": "input differential pair",
        },
        "MM3_MM0_matched": {
            "devices": ["MM3", "MM0"],
            "technique": "ABAB_load_pair",
            "description": "PMOS input/precharge load pair",
        },
        "MM2_MM1_matched": {
            "devices": ["MM2", "MM1"],
            "technique": "ABAB",
            "description": "output precharge pair",
        },
        "MM5_MM4_matched": {
            "devices": ["MM5", "MM4"],
            "technique": "symmetric_cross_coupled",
            "description": "PMOS latch pair",
        },
    }


def make_comparator_finger_state(user_message: str) -> dict:
    """Build a finger-level comparator state for device resolver tests.

    Includes physical finger nodes (MM8_m1..MM8_m8, etc.) and matched-block
    metadata in the initial_agent_trace.
    """
    fnodes = _finger_nodes()
    trace = copy.deepcopy(_initial_agent_trace())
    trace["strategy"]["matched_blocks"] = copy.deepcopy(_matched_blocks())

    # Build finger-level terminal nets
    tnets = {}
    for n in fnodes:
        nid = n["id"]
        tnets[nid] = {"D": n.get("D", ""), "G": n.get("G", ""), "S": n.get("S", "")}

    state = {
        "mode": "chat",
        "user_message": user_message,
        "selected_model": "Gemini",
        "chat_history": [],
        "nodes": copy.deepcopy(fnodes),
        "placement_nodes": copy.deepcopy(fnodes),
        "edges": copy.deepcopy(_edges()),
        "terminal_nets": tnets,
        "initial_agent_trace": trace,
        "pending_cmds": [],
        "session_commands": [],
    }
    return state


"""
File Description:
This module implements Node 4 of the LangGraph pipeline: Finger Expansion. It ensures that transistor-level logical groups are expanded back into individual physical finger nodes with precise coordinates, while resolving any horizontal row overlaps and validating finger integrity.

Functions:
- node_finger_expansion:
    - Role: Performs the expansion of logical placement groups into physical finger nodes and resolves row overlaps using a deterministic resolver.
    - Inputs: 
        - state (dict): The current LangGraph state containing 'placement_nodes' and 'nodes'.
    - Outputs: (dict) A state update containing expanded 'placement_nodes' and a 'deterministic_snapshot'.
"""

import copy
import time
from ai_agent.placement.finger_grouper import expand_logical_to_fingers, _resolve_row_overlaps
from ai_agent.placement.validators import validate_finger_integrity
from ai_agent.tools.overlap_resolver import resolve_overlaps
from ai_agent.nodes._shared import vprint, ip_step
from ai_agent.utils.logging import (
    log_section, log_detail, log_device_positions, stage_start,
)


def node_finger_expansion(state):
    t0 = time.time()
    stage_start(4, "Finger Expansion")

    placement_nodes = state.get("placement_nodes", [])
    original_nodes = state.get("nodes", [])
    log_detail(f"Input: {len(placement_nodes)} placement nodes, {len(original_nodes)} original nodes")

    # The placement specialist already expands logical groups to physical fingers.
    # Only re-expand if placement_nodes contain logical (aggregated) nodes
    # that still need expansion (detected by _is_logical flag).
    has_logical = any(n.get("_is_logical") for n in placement_nodes)

    if has_logical:
        physical_nodes = expand_logical_to_fingers(placement_nodes, original_nodes)
        log_detail(f"Expanded {len(placement_nodes)} logical → {len(physical_nodes)} physical nodes")
    else:
        # Even if already physical, we must run the row overlap resolver to generate filler dummies
        # and pack the layout to the correct standard grid.
        no_abutment_flag = state.get("no_abutment", False)
        physical_nodes = _resolve_row_overlaps(placement_nodes, no_abutment_flag)
        log_detail(f"Nodes already physical — skipped expansion, but regenerated fillers ({len(physical_nodes)} devices)")

    # Run overlap resolution on expanded nodes
    log_section("Post-expansion overlap resolution")
    moved_ids = resolve_overlaps(physical_nodes)
    if moved_ids:
        log_detail(f"Resolved {len(moved_ids)} overlapping device(s)")
    else:
        log_detail("No overlaps detected")

    # Integrity check
    log_section("Finger integrity validation")
    integrity = validate_finger_integrity(original_nodes, physical_nodes)
    if integrity["pass"]:
        log_detail(f"Integrity OK: all {integrity['original_count']} devices preserved")
    else:
        log_detail(f"INTEGRITY FAILED: {integrity['summary']}")
        if integrity.get("missing"):
            log_detail(f"  Missing: {integrity['missing'][:10]}")
        if integrity.get("extra"):
            log_detail(f"  Extra: {integrity['extra'][:10]}")

    # Show position summary
    log_device_positions(physical_nodes, "Finger Expansion Output Positions")

    elapsed = time.time() - t0
    ip_step("4/5 Finger Expansion", f"{len(physical_nodes)} device(s) ({elapsed:.1f}s)")

    return {
        "placement_nodes": physical_nodes,
        "deterministic_snapshot": copy.deepcopy(physical_nodes),
    }

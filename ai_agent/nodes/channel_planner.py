"""
Channel Planner Node
======================
Reads the routing channel density estimates produced by pass 1 of the Routing Pre-Viewer
and deterministically shifts device rows vertically to make room for routing tracks.

This physically applies the recommended channel widths by modifying the Y coordinates
of the placement nodes, then snaps them to the manufacturing grid and resolves any
mechanical overlaps.

This node is a PURE-PYTHON MUTATOR (no LLM).
"""

import time
from ai_agent.nodes._shared import ip_step
from ai_agent.utils.logging import log_section, log_detail
from ai_agent.tools.overlap_resolver import resolve_overlaps
from ai_agent.placement.finger_grouper import legalize_vertical_rows
from config.design_rules import ROW_HEIGHT_UM


def node_channel_planner(state: dict) -> dict:
    t0 = time.time()

    nodes = state.get("placement_nodes", [])
    routing_result = state.get("routing_result", {})
    channels = routing_result.get("channels", [])

    if not nodes or not channels:
        elapsed = time.time() - t0
        ip_step("5.6/5 Channel Planner", f"skipped (no channels) ({elapsed:.1f}s)")
        return {"channel_plan": [], "last_agent": "channel_planner"}

    log_section("Channel Planner")
    
    # ── Sort channels from bottom to top ─────────────────────────────
    # We apply shifts from the lowest Y to highest Y so we can accumulate
    # the shifts for all rows above the widened channel.
    sorted_channels = sorted(channels, key=lambda c: c.get("y_boundary", 0.0))
    
    accumulated_shifts = []
    
    # Copy nodes to avoid mutating the current state directly
    import copy
    new_nodes = copy.deepcopy(nodes)
    
    for ch in sorted_channels:
        y_boundary = ch.get("y_boundary", 0.0)
        current_w = ch.get("current_width_um", 0.0)
        target_w = ch.get("recommended_width_um", 0.0)
        
        # Only shift if we need more room than currently exists
        if target_w > current_w:
            shift_amount = target_w - current_w
            
            # Snap shift amount to the row height grid
            # This ensures devices always land on valid Y pitches
            shift_amount = round(shift_amount / ROW_HEIGHT_UM) * ROW_HEIGHT_UM
            if shift_amount <= 0:
                continue
                
            log_detail(f"Widening band at Y ≈ {y_boundary:.3f} by +{shift_amount:.3f}µm")
            accumulated_shifts.append({
                "y_boundary": y_boundary,
                "shift": shift_amount,
                "band_index": ch.get("band_index", -1)
            })
            
            # Apply shift to all nodes strictly ABOVE the boundary
            for n in new_nodes:
                geo = n.get("geometry", {})
                if "y" in geo:
                    # If the device is above the boundary, shift it up
                    if geo["y"] > y_boundary:
                        geo["y"] += shift_amount

    # ── Post-shift mechanical validation ─────────────────────────────
    # Re-run the overlap resolver to ensure no devices ended up intersecting,
    # and re-legalize vertical rows (which handles symmetry snapping).
    if accumulated_shifts:
        log_detail("Resolving mechanical overlaps post-shift...")
        resolve_overlaps(new_nodes)
        new_nodes = legalize_vertical_rows(new_nodes)

    elapsed = time.time() - t0
    ip_step(
        "5.6/5 Channel Planner",
        f"expanded {len(accumulated_shifts)} channels ({elapsed:.1f}s)"
    )

    return {
        "placement_nodes": new_nodes,
        "channel_plan": accumulated_shifts,
        "last_agent": "channel_planner",
    }

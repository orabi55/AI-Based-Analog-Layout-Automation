"""
Tool Dispatcher
===============
Single routing layer between tool-call arguments and the underlying core/ functions.

Usage:
    from ai_agent.tools.dispatcher import dispatch

    result = dispatch("move_device", {"device": "MM1", "x": 0.5, "y": 0.0}, nodes)
    if result.success:
        nodes = result.nodes

Contract:
- Never raises — every exception is caught and returned as
  LayoutToolResult(success=False, message=...).
- pdk defaults to load_pdk("saed14nm") when None.
- The returned LayoutToolResult.nodes is always the FULL updated layout
  (not just the subset touched by the tool).
"""

from __future__ import annotations

import copy
import json
import logging
import uuid
from typing import List

from ai_agent.core.interfaces import LayoutToolResult
from ai_agent.pdks.loader import load_pdk

# ── Core imports ─────────────────────────────────────────────────────────────
import ai_agent.core.drc                  as _drc
import ai_agent.core.physical_cells       as _pc
import ai_agent.core.common_centroid      as _cc
import ai_agent.core.passive_placer       as _pp
import ai_agent.core.circuit_detection    as _cd
import ai_agent.core.group_placer         as _gp
import ai_agent.core.circuit_orchestrator as _co
import ai_agent.core.layout_ops           as _lo

from ai_agent.tools.cmd_parser import apply_cmds_to_nodes
from ai_agent.placement.quality_metrics import score_placement, _transistor_key

logger = logging.getLogger("ai_agent")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_dummy(node: dict) -> bool:
    """True for any node that is a filler / dummy (not a structural dummy)."""
    if node.get("is_dummy"):
        return True
    nid = str(node.get("id", ""))
    return nid.startswith(("FILLER_DUMMY_", "DUMMY_matrix_", "EDGE_DUMMY", "FILLER_"))


def _merge_back(original_copy: list, updated_subset: list) -> list:
    """Replace nodes that appear in updated_subset back into the full layout copy.

    Nodes absent from updated_subset (untouched devices) are kept as-is.
    Nodes that are new in updated_subset (e.g. inserted dummies) are appended.
    """
    updated_map  = {n["id"]: n for n in updated_subset}
    merged       = [updated_map.get(n["id"], n) for n in original_copy]
    original_ids = {n["id"] for n in original_copy}
    new_nodes    = [n for n in updated_subset if n["id"] not in original_ids]
    return merged + new_nodes


# ---------------------------------------------------------------------------
# Public dispatch function
# ---------------------------------------------------------------------------

def dispatch(
    tool_name: str,
    arguments: dict,
    nodes: list,
    pdk: dict = None,
    terminal_nets: dict = None,
) -> LayoutToolResult:
    """Route a tool call to the appropriate core/ implementation.

    Args:
        tool_name:     Name matching a key in TOOL_REGISTRY.
        arguments:     Parsed argument dict from the tool-call payload.
        nodes:         Current layout node list.
        pdk:           PDK configuration dict; defaults to load_pdk("saed14nm").
        terminal_nets: Optional {device_id: {D, G, S}} mapping required by
                       circuit-detection / circuit-orchestrator tools.

    Returns:
        LayoutToolResult — never raises.
    """
    pdk           = pdk if pdk is not None else load_pdk("saed14nm")
    terminal_nets = terminal_nets if isinstance(terminal_nets, dict) else {}
    args          = arguments or {}

    try:
        return _route(tool_name, args, nodes, pdk, terminal_nets)
    except Exception as exc:
        logger.error(
            "[dispatch] %s raised %s: %s",
            tool_name, type(exc).__name__, exc,
            exc_info=True,
        )
        return LayoutToolResult(
            success=False,
            message=f"{tool_name} failed: {exc}",
            changed=False,
            nodes=list(nodes),
        )


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def _route(tool_name: str, args: dict, nodes: list, pdk: dict,
           terminal_nets: dict) -> LayoutToolResult:

    # ── Layout inspection ────────────────────────────────────────────────────

    if tool_name == "read_layout":
        return LayoutToolResult(
            success=True,
            message=f"Layout contains {len(nodes)} device(s)",
            changed=False,
            nodes=list(nodes),
            metrics={"device_count": len(nodes)},
        )

    if tool_name == "list_devices":
        device_list = [
            {"id": n.get("id", "?"), "type": n.get("type", "unknown")}
            for n in nodes
        ]
        return LayoutToolResult(
            success=True,
            message=f"Found {len(device_list)} device(s)",
            changed=False,
            nodes=list(nodes),
            metrics={"device_list": device_list},
        )

    if tool_name == "get_device_info":
        dev_id = args["device_id"]
        node   = next((n for n in nodes if n.get("id") == dev_id), None)
        if node is None:
            return LayoutToolResult(
                success=False,
                message=f"Device not found: {dev_id!r}",
                nodes=list(nodes),
            )
        return LayoutToolResult(
            success=True,
            message=(
                f"Device {dev_id}: type={node.get('type')}, "
                f"geometry={node.get('geometry')}"
            ),
            changed=False,
            nodes=list(nodes),
            metrics={"device": node},
        )

    # ── Device manipulation (routed through apply_cmds_to_nodes) ─────────────

    if tool_name == "move_device":
        cmd     = {"action": "move", "device": args["device"],
                   "x": args["x"], "y": args["y"]}
        updated = apply_cmds_to_nodes(nodes, [cmd])
        return LayoutToolResult(
            success=True,
            message=f"Moved {args['device']} to ({args['x']}, {args['y']})",
            changed=True,
            nodes=updated,
        )

    if tool_name == "swap_devices":
        cmd     = {"action": "swap",
                   "device_a": args["device_a"], "device_b": args["device_b"]}
        updated = apply_cmds_to_nodes(nodes, [cmd])
        return LayoutToolResult(
            success=True,
            message=f"Swapped {args['device_a']} ↔ {args['device_b']}",
            changed=True,
            nodes=updated,
        )

    if tool_name == "flip_device":
        axis    = str(args.get("axis", "h")).lower()
        action  = "flip_v" if axis == "v" else "flip_h"
        cmd     = {"action": action, "device": args["device"]}
        updated = apply_cmds_to_nodes(nodes, [cmd])
        return LayoutToolResult(
            success=True,
            message=f"Flipped {args['device']} ({axis}-axis)",
            changed=True,
            nodes=updated,
        )

    if tool_name == "add_dummy":
        dummy_id = f"FILLER_DUMMY_{uuid.uuid4().hex[:8]}"
        dummy = {
            "id":       dummy_id,
            "type":     args.get("type", "nmos"),
            "is_dummy": True,
            "geometry": {
                "x":      float(args["x"]),
                "y":      float(args["y"]),
                "width":  float(args.get("width",  0.294)),
                "height": float(args.get("height", 0.568)),
                "orientation": "R0",
            },
        }
        return LayoutToolResult(
            success=True,
            message=f"Added dummy {dummy_id}",
            changed=True,
            nodes=list(nodes) + [dummy],
            metrics={"added_id": dummy_id},
        )

    if tool_name == "remove_dummies":
        cleaned = [n for n in nodes if not _is_dummy(n)]
        removed = len(nodes) - len(cleaned)
        return LayoutToolResult(
            success=True,
            message=f"Removed {removed} dummy/filler node(s)",
            changed=removed > 0,
            nodes=cleaned,
            metrics={"removed_count": removed},
        )

    # ── DRC & legalisation ───────────────────────────────────────────────────

    if tool_name == "check_overlaps":
        gap_px     = float(args.get("gap_px", 0.0))
        drc_result = _drc.run_drc_check(nodes, gap_px=gap_px)
        return LayoutToolResult(
            success=True,
            message=drc_result["summary"],
            changed=False,
            nodes=list(nodes),
            metrics={
                "drc_pass":        drc_result["pass"],
                "violation_count": len(drc_result["violations"]),
                "violations":      drc_result["violations"],
            },
        )

    if tool_name == "run_legalizer":
        gap_px     = float(args.get("gap_px", 0.0))
        drc_result = _drc.run_drc_check(nodes, gap_px=gap_px)
        if drc_result["pass"]:
            return LayoutToolResult(
                success=True,
                message="DRC already clean — no fixes needed",
                changed=False,
                nodes=list(nodes),
                metrics={"fixes_applied": 0},
            )
        fixes   = _drc.compute_prescriptive_fixes(drc_result, gap_px=gap_px, nodes=nodes)
        updated = apply_cmds_to_nodes(nodes, fixes)
        return LayoutToolResult(
            success=True,
            message=f"Applied {len(fixes)} prescriptive fix(es)",
            changed=len(fixes) > 0,
            nodes=updated,
            metrics={"fixes_applied": len(fixes)},
        )

    # ── Physical cell insertion ───────────────────────────────────────────────

    if tool_name == "insert_endcaps":
        return _pc.insert_endcaps(nodes, pdk)

    if tool_name == "insert_taps":
        return _pc.insert_taps(nodes, pdk)

    if tool_name == "insert_fillers":
        return _pc.insert_fillers(nodes, pdk)

    if tool_name == "insert_all_physical_cells":
        return _pc.insert_all_physical_cells(nodes, pdk)

    # ── Common-centroid placement ─────────────────────────────────────────────

    if tool_name == "place_common_centroid":
        nodes_copy = copy.deepcopy(nodes)
        id_map     = {n["id"]: n for n in nodes_copy}
        group_a    = [id_map[did] for did in args["group_a_ids"] if did in id_map]
        group_b    = [id_map[did] for did in args["group_b_ids"] if did in id_map]

        result = _cc.place_common_centroid(
            group_a, group_b,
            start_x = float(args["start_x"]),
            row_y   = float(args["row_y"]),
            pdk     = pdk,
            pattern = args.get("pattern", "ABBA"),
        )
        # nodes_copy has the in-place-updated positions; return the full layout
        return LayoutToolResult(
            success  = result.success,
            message  = result.message,
            changed  = result.changed,
            nodes    = nodes_copy,
            metrics  = result.metrics,
            warnings = result.warnings,
        )

    if tool_name == "place_common_centroid_2d":
        nodes_copy    = copy.deepcopy(nodes)
        all_placed_ids: set = set()
        devices: List[dict] = []

        for spec in args["device_specs"]:
            dev_id    = spec["id"]
            dev_nodes = [
                n for n in nodes_copy
                if _transistor_key(str(n.get("id", ""))) == dev_id
            ]
            all_placed_ids.update(n["id"] for n in dev_nodes)
            devices.append({
                "id":      dev_id,
                "fingers": spec.get("fingers", len(dev_nodes)),
                "nodes":   dev_nodes,
            })

        result      = _cc.place_common_centroid_2d(
            devices,
            start_x = float(args["start_x"]),
            row_y   = float(args["row_y"]),
            pdk     = pdk,
        )
        # Rebuild full layout: non-placed nodes keep their (unchanged) positions,
        # placed nodes come from result.nodes (which may be a subset if some
        # device IDs were absent from the input).
        non_placed = [n for n in nodes_copy if n["id"] not in all_placed_ids]
        full_nodes = non_placed + (result.nodes if result.success else
                                   [n for n in nodes_copy if n["id"] in all_placed_ids])
        return LayoutToolResult(
            success  = result.success,
            message  = result.message,
            changed  = result.changed,
            nodes    = full_nodes,
            metrics  = result.metrics,
            warnings = result.warnings,
        )

    if tool_name == "insert_dummies_around_group":
        nodes_copy    = copy.deepcopy(nodes)
        id_map        = {n["id"]: n for n in nodes_copy}
        group_ids_set = set(args["group_node_ids"])
        group_nodes   = [id_map[did] for did in args["group_node_ids"] if did in id_map]

        result = _cc.insert_dummies_around_group(
            group_nodes,
            pdk,
            n_dummies = int(args.get("n_dummies", 1)),
        )
        # result.nodes = group_nodes + new structural dummies.
        # Reconstruct the full layout: non-group nodes + result.nodes
        non_group  = [n for n in nodes_copy if n["id"] not in group_ids_set]
        full_nodes = non_group + (result.nodes if result.success else group_nodes)
        return LayoutToolResult(
            success  = result.success,
            message  = result.message,
            changed  = result.changed,
            nodes    = full_nodes,
            metrics  = result.metrics,
            warnings = result.warnings,
        )

    # ── Passive device placement ─────────────────────────────────────────────

    if tool_name in ("place_resistor", "place_mom_cap", "place_mos_cap", "reshape_passive"):
        node_id = args.get("node_id")
        target  = next((n for n in nodes if n.get("id") == node_id), None)
        if target is None:
            return LayoutToolResult(
                success=False,
                message=f"{tool_name}: device not found: {node_id!r}",
                nodes=list(nodes),
            )
        node_copy = copy.deepcopy(target)

        if tool_name == "place_resistor":
            result = _pp.place_resistor(
                node_copy,
                area_um2     = float(args["area_um2"]),
                aspect_ratio = float(args.get("aspect_ratio", 4.0)),
                allow_series  = bool(args.get("allow_series",  True)),
                allow_parallel = bool(args.get("allow_parallel", True)),
            )
        elif tool_name == "place_mom_cap":
            result = _pp.place_mom_cap(
                node_copy,
                area_um2 = float(args["area_um2"]),
                layers   = args.get("layers") or None,
            )
        elif tool_name == "place_mos_cap":
            result = _pp.place_mos_cap(
                node_copy,
                nf       = int(args["nf"]),
                width_um = float(args["width_um"]),
            )
        else:  # reshape_passive
            result = _pp.reshape_passive(
                node_copy,
                new_area_um2 = float(args["new_area_um2"]),
            )

        if not result.success:
            return LayoutToolResult(
                success=False, message=result.message,
                nodes=list(nodes), warnings=result.warnings,
            )

        # Splice the updated node back into the full layout
        updated_node = result.nodes[0] if result.nodes else node_copy
        full_nodes   = [updated_node if n.get("id") == node_id else n
                        for n in nodes]
        return LayoutToolResult(
            success  = result.success,
            message  = result.message,
            changed  = result.changed,
            nodes    = full_nodes,
            metrics  = result.metrics,
            warnings = result.warnings,
        )

    # ── Quality scoring ──────────────────────────────────────────────────────

    if tool_name == "score_layout":
        report = score_placement(nodes)
        return LayoutToolResult(
            success  = True,
            message  = report.get("summary", ""),
            changed  = False,
            nodes    = list(nodes),
            metrics  = report,
        )

    # ── Persistence ──────────────────────────────────────────────────────────

    if tool_name == "save_layout":
        layout_json = json.dumps(nodes, indent=2)
        path        = args.get("path")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(layout_json)
            msg = f"Layout saved to {path} ({len(nodes)} devices)"
        else:
            msg = f"Layout serialized ({len(nodes)} devices)"
        return LayoutToolResult(
            success = True,
            message = msg,
            changed = False,
            nodes   = list(nodes),
            metrics = {"serialized": layout_json},
        )

    # ── GUI-parity layout operations ─────────────────────────────────────────

    if tool_name == "delete_device":
        return _lo.delete_device(nodes, args["device_id"])

    if tool_name == "align_devices":
        return _lo.align_devices(
            nodes,
            device_ids   = list(args.get("device_ids") or []),
            axis         = str(args.get("axis", "x")),
            mode         = str(args.get("mode", "mean")),
            reference_id = args.get("reference_id"),
        )

    if tool_name == "abut_devices":
        return _lo.abut_devices(nodes, args["device_a"], args["device_b"])

    if tool_name == "merge_shared_source":
        return _lo.merge_shared_source(nodes, args["device_a"], args["device_b"])

    if tool_name == "merge_shared_drain":
        return _lo.merge_shared_drain(nodes, args["device_a"], args["device_b"])

    if tool_name == "lock_device":
        return _lo.lock_device(nodes, args["device_id"])

    if tool_name == "unlock_device":
        return _lo.unlock_device(nodes, args["device_id"])

    if tool_name == "set_device_color":
        return _lo.set_device_color(nodes, args["device_id"], str(args.get("color", "#4a90d9")))

    if tool_name == "reset_device_color":
        return _lo.reset_device_color(nodes, args["device_id"])

    if tool_name == "get_layout_bounds":
        return _lo.get_layout_bounds(nodes)

    if tool_name == "create_group":
        device_ids = list(args.get("device_ids") or [])
        if len(device_ids) < 2:
            return LayoutToolResult(
                success=False,
                message="create_group: need at least 2 device Iids",
                nodes=list(nodes),
            )
        name = str(args.get("name") or "")
        # Return unchanged nodes but embed a GUI command the editor will execute
        return LayoutToolResult(
            success=True,
            message=f"Group created: {name or 'auto-named'} ({len(device_ids)} devices)",
            changed=True,   # triggers cmd_block emission in tool_runner
            nodes=list(nodes),
            metrics={
                "gui_commands": [
                    {"action": "create_group",
                     "name":   name,
                     "device_ids": device_ids}
                ]
            },
        )

    if tool_name == "match_devices":
        return _lo.match_devices(
            nodes,
            device_ids      = list(args.get("device_ids") or []),
            technique       = str(args.get("technique", "interdigitated")),
            custom_pattern  = args.get("custom_pattern"),
        )

    # ── Mid-level: circuit-pattern detection ─────────────────────────────────

    if tool_name == "detect_matched_pairs":
        return _cd.detect_matched_pairs(nodes)

    if tool_name == "detect_differential_pairs":
        return _cd.detect_differential_pairs(nodes, terminal_nets)

    if tool_name == "detect_current_mirrors":
        return _cd.detect_current_mirrors(nodes, terminal_nets)

    if tool_name == "detect_cross_coupled_pairs":
        return _cd.detect_cross_coupled_pairs(nodes, terminal_nets)

    # ── Mid-level: named placement ───────────────────────────────────────────

    if tool_name == "place_matched_pair":
        return _gp.place_matched_pair(
            nodes,
            device_a = args["device_a"],
            device_b = args["device_b"],
            pdk      = pdk,
            start_x  = args.get("start_x"),
            row_y    = args.get("row_y"),
        )

    if tool_name == "place_differential_pair":
        return _gp.place_differential_pair(
            nodes,
            device_a = args["device_a"],
            device_b = args["device_b"],
            pdk      = pdk,
            start_x  = args.get("start_x"),
            row_y    = args.get("row_y"),
        )

    if tool_name == "place_current_mirror":
        return _gp.place_current_mirror(
            nodes,
            device_ids = list(args.get("device_ids") or []),
            pdk        = pdk,
            start_x    = args.get("start_x"),
            row_y      = args.get("row_y"),
        )

    if tool_name == "add_dummy_group":
        return _gp.add_dummy_group(
            nodes,
            group_node_ids = list(args.get("group_node_ids") or []),
            pdk            = pdk,
            n_dummies      = int(args.get("n_dummies", 1)),
        )

    # ── Mid-level: validation ────────────────────────────────────────────────

    if tool_name == "validate_symmetry":
        return _cd.validate_symmetry(nodes)

    if tool_name == "validate_dummy_presence":
        return _cd.validate_dummy_presence(
            nodes,
            group_node_ids       = list(args.get("group_node_ids") or []),
            min_dummies_per_side = int(args.get("min_dummies_per_side", 1)),
        )

    # ── Advanced / circuit-level ─────────────────────────────────────────────

    if tool_name == "detect_circuit_type":
        return _cd.detect_circuit_type(nodes, terminal_nets)

    if tool_name == "place_comparator":
        return _co.place_comparator(nodes, terminal_nets, pdk)

    if tool_name == "place_tx_driver":
        return _co.place_tx_driver(nodes, terminal_nets, pdk)

    if tool_name == "run_full_layout_pipeline":
        return _co.run_full_layout_pipeline(nodes, terminal_nets, pdk)

    if tool_name == "optimize_layout_for_matching":
        return _co.optimize_layout_for_matching(nodes, terminal_nets, pdk)

    if tool_name == "optimize_layout_for_routing":
        return _co.optimize_layout_for_routing(
            nodes, pdk,
            gap_px = float(args.get("gap_px", 0.0)),
        )

    # ── Unknown tool ─────────────────────────────────────────────────────────

    return LayoutToolResult(
        success = False,
        message = f"Unknown tool: {tool_name!r}",
        nodes   = list(nodes),
    )

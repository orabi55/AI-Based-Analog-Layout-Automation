"""
File Description:
This module implements Node 3 of the LangGraph pipeline: Placement Specialist.
It calculates symmetrical matching groups and row assignments, invokes the
Placement Specialist agent (via ReAct + SkillMiddleware) to generate positioning
commands, and expands resulting groups into physical fingers while ensuring
device conservation.

Functions
---------
node_placement_specialist(state)
    Orchestrates placement for the primary pipeline path.
    Uses _compute_matching_and_rows for richer context and full finger-map.

node_placement_specialist_chatbot(state)
    Orchestrates placement for the chat/interactive path.
    Uses aggregate_to_logical_devices for lightweight grouping.

Both nodes share:
- The same module-level agent singleton (_PLACEMENT_SPECIALIST_AGENT)
- The same pre-built system prompt and tool list (_PLACEMENT_SYSTEM_PROMPT,
  _PLACEMENT_TOOLS) so middleware augmentation runs exactly once at import time.

Inputs  (state keys consumed)
------------------------------
nodes, constraint_text, user_message, chat_history, edges, terminal_nets,
strategy_result, selected_model, no_abutment, placement_nodes, pending_cmds

Outputs (state keys produced)
------------------------------
placement_nodes, pending_cmds, original_placement_cmds, chat_history
"""

import copy
import re as _re_mod
import time

from ai_agent.agents.placement_specialist import (
    PLACEMENT_SPECIALIST_PROMPT,
    PLACEMENT_SPECIALIST_PROMPT_CHATBOT,
    build_placement_context,
    build_placement_context_chatbot,
    create_placement_specialist_agent,
)
from ai_agent.knowledge.skill_injector import SkillMiddleware
from ai_agent.placement.finger_grouper import (
    aggregate_to_logical_devices, 
    legalize_vertical_rows,
    detect_abutment_intent
)
from ai_agent.tools.cmd_parser import extract_cmd_blocks, apply_cmds_to_nodes
from ai_agent.tools.overlap_resolver import resolve_overlaps
from ai_agent.nodes._shared import (
    SKILLS_DIR,
    _build_llm_messages,
    _invoke_with_retry,
    _invoke_react_agent_with_retry,
    _extract_agent_output_parts,
    _extract_agent_output_content,
    _strip_thinking_text,
    _update_and_save_chat_history,
    ip_step,
)
from ai_agent.utils.logging import (
    log_section, log_detail, log_device_positions, stage_start, vprint,
)
from ai_agent.tools.inventory import validate_device_count
from ai_agent.placement.symmetry import enforce_reflection_symmetry
from ai_agent.placement.quality_metrics import score_placement



# ── Module-level singletons ─────────────────────────────────────────────────
#
# Middleware augmentation (catalog injection + tool-dict construction) happens
# once at import time.  Both nodes share the same prompt and tool list so
# there is no per-call overhead and no risk of diverging configurations.

_PLACEMENT_SKILL_MIDDLEWARE = SkillMiddleware(SKILLS_DIR)

_PLACEMENT_SPECIALIST_AGENT = create_placement_specialist_agent(
    middlewares=[_PLACEMENT_SKILL_MIDDLEWARE]
)
# Build augmented prompt and collect tool dicts from all middlewares.
_PLACEMENT_SYSTEM_PROMPT: str = str(
    _PLACEMENT_SPECIALIST_AGENT.get("system_prompt", PLACEMENT_SPECIALIST_PROMPT)
)


# Priority level -> numeric weight (mirrors placement_goals_widget.PRIORITY_WEIGHTS)
_PRIORITY_NUM = {"Low": 1, "Medium": 5, "High": 10}

_ROW_HEIGHT = 0.668   # um per row


def _snap_orphan_dummies(nodes: list) -> list:
    """
    Post-placement safety pass: detects dummy devices that ended up at Y
    coordinates far from the rest of the layout (the "flying transistor" bug
    that occurs when matching=Low removes ABBA blocks that used to anchor them).

    For each orphan dummy:
      - Collect all valid Y values used by active (non-dummy) devices of the
        same PMOS/NMOS type.
      - Snap the dummy's Y to the closest valid Y.
      - Append it at the rightmost X of that row (after the last real device).

    A device is considered a "dummy" if its id starts with D, FILLER_DUMMY_,
    EDGE_DUMMY, or DUMMY_matrix_.
    """
    import statistics

    def _is_dummy(node):
        nid = str(node.get("id", ""))
        return (
            node.get("is_dummy")
            or nid.startswith(("FILLER_DUMMY_", "DUMMY_matrix_", "EDGE_DUMMY"))
            or (len(nid) >= 2 and nid[0] == "D" and nid[1:].isdigit())
        )

    def _dev_type(node):
        t = str(node.get("type", "")).lower()
        return "pmos" if "pmos" in t or "p_mos" in t else "nmos"

    # Gather valid Y values per type from non-dummy devices
    active_y: dict[str, list[float]] = {"pmos": [], "nmos": []}
    active_x_by_y: dict[tuple, list[float]] = {}   # (type, y_rounded) -> [x values]

    for n in nodes:
        if _is_dummy(n):
            continue
        g = n.get("geometry", {})
        x = g.get("x", 0.0)
        y = g.get("y", 0.0)
        dt = _dev_type(n)
        active_y[dt].append(y)
        key = (dt, round(y, 3))
        active_x_by_y.setdefault(key, []).append(x)

    if not any(active_y.values()):
        return nodes   # nothing to snap to

    # Compute median Y per type and the set of "valid" row Ys
    valid_ys: dict[str, list[float]] = {}
    for dt, ys in active_y.items():
        if ys:
            # Cluster: round to nearest _ROW_HEIGHT grid
            rounded = sorted({round(y / _ROW_HEIGHT) * _ROW_HEIGHT for y in ys})
            valid_ys[dt] = rounded

    # Identify orphan threshold: a dummy is orphan if its Y is > 1.5 row-heights
    # away from ALL valid rows of its type
    result = []
    for n in nodes:
        if not _is_dummy(n):
            result.append(n)
            continue

        g = n.get("geometry", {})
        dx = g.get("x", 0.0)
        dy = g.get("y", 0.0)
        w  = g.get("width", 0.07)
        dt = _dev_type(n)

        rows = valid_ys.get(dt, [])
        if not rows:
            result.append(n)
            continue

        # Find nearest valid row Y
        nearest_y = min(rows, key=lambda ry: abs(ry - dy))
        gap = abs(nearest_y - dy)

        if gap > _ROW_HEIGHT * 1.5:
            # Orphan detected - snap
            key = (dt, round(nearest_y, 3))
            taken_xs = active_x_by_y.get(key, [])
            new_x = (max(taken_xs) + w + 0.01) if taken_xs else 0.0
            # Update node in place (shallow copy geometry)
            n = dict(n)
            n["geometry"] = dict(g)
            n["geometry"]["x"] = round(new_x, 4)
            n["geometry"]["y"] = round(nearest_y, 4)
            # Register in active_x_by_y so multiple orphans don't overlap
            active_x_by_y.setdefault(key, []).append(new_x)
            log_detail(
                f"[snap_orphan] {n['id']}: y={dy:.3f} -> {nearest_y:.3f} (gap={gap:.3f}um)"
            )

        result.append(n)
    return result


def _goals_to_prompt(goals: dict) -> str:
    """
    Convert a placement_goals dict (from the UI) into a plain-English
    priority block prepended to the LLM context.

    Crucially, this section OVERRIDES conflicting rules in the system prompt
    so all pipeline stages (deterministic + LLM) stay in sync.

    Returns an empty string when no goals are set (panel was closed).
    """
    if not goals:
        return ""

    area_p   = goals.get("area_priority",    "Medium")
    match_p  = goals.get("matching_priority", "Medium")
    sym_p    = goals.get("symmetry_priority", "High")
    max_area = goals.get("max_area_um2")

    # Area instructions
    _AREA_INSTR = {
        "Low":    "Area is NOT a priority - rows may grow; focus on matching quality.",
        "Medium": "Aim for a compact layout; avoid unnecessary empty space.",
        "High":   "MINIMISE area: pack devices into the FEWEST possible rows. "
                  "Each row should be as full as DRC rules allow before starting a new row.",
    }
    # Matching instructions (also overrides system-prompt ABBA rules)
    _MATCH_INSTR = {
        "Low":    "Apply ABBA interdigitation for differential pairs and current mirrors only. "
                  "Cross-coupled and load pairs are placed individually without interdigitation.",
        "Medium": "Apply ABBA for differential pairs, current mirrors, cross-coupled pairs, "
                  "and load pairs (all standard matching tiers).",
        "High":   "MANDATORY: apply ABBA or common-centroid for EVERY transistor pair the "
                  "engine can match - including any additional pairs not in the standard tiers. "
                  "No matchable pair should be left uninterdigitated.",
    }
    # Symmetry instructions
    _SYM_INSTR = {
        "Low":    "Global mirror symmetry is DISABLED for this run. "
                  "Do NOT enforce a shared vertical axis for left/right halves. "
                  "This OVERRIDES the TWO-HALF symmetry rules in the system prompt. "
                  "Place devices for best area packing without mirroring constraints.",
        "Medium": "Apply reflection symmetry for matched pairs where it does not cost area.",
        "High":   "MANDATORY: every matched group must be placed mirror-symmetrically "
                  "about the layout centre line. Sacrificing area for symmetry is acceptable.",
    }

    lines = [
        "=" * 62,
        "  PLACEMENT GOALS  (user-set - these OVERRIDE all system-prompt rules below)",
        "=" * 62,
        f"  Area priority     : {area_p:6s}  -> {_AREA_INSTR[area_p]}",
        f"  Matching priority : {match_p:6s}  -> {_MATCH_INSTR[match_p]}",
        f"  Symmetry priority : {sym_p:6s}  -> {_SYM_INSTR[sym_p]}",
    ]
    if max_area is not None:
        lines.append(
            f"  Max area          : {max_area} um2  "
            f"-> The total bounding-box area MUST NOT exceed this value."
        )

    # Critical-net clustering directive for the LLM
    crit_cfg = goals.get("critical_nets") or {}
    crit_nets = crit_cfg.get("nets") or []
    crit_priority = crit_cfg.get("priority", "Low")
    if crit_nets and crit_priority != "Low":
        nets_str = ", ".join(crit_nets)
        lines.append(f"  Critical nets     : {nets_str}  priority={crit_priority}")
        lines.append(
            "  -> CRITICAL NET CLUSTERING (OVERRIDE): Place matched blocks that "
            "carry devices on these nets ADJACENT to each other within each row. "
            "Minimise the horizontal (X) spread of these nets. "
            "This overrides the default block ordering."
        )

    lines += ["=" * 62, ""]
    return "\n".join(lines) + "\n"


# ── Shared helper ───────────────────────────────────────────────────────────

def _sync_group_geometry_from_members(group_nodes, finger_map):
    """Align each logical group's geometry to the current finger placements.

    Sets group x to the minimum finger x, group y to the modal finger y,
    and copies orientation from the first finger that declares one.
    No-ops silently when group_nodes or finger_map are empty.
    """
    if not group_nodes or not finger_map:
        return
    from collections import Counter

    for group in group_nodes:
        gid = group.get("id", "")
        members = finger_map.get(gid, [])
        if not members:
            continue

        xs, ys = [], []
        orientation = None
        for member in members:
            geo = member.get("geometry", {})
            if not isinstance(geo, dict):
                continue
            try:
                xs.append(float(geo.get("x", 0.0)))
            except (TypeError, ValueError):
                pass
            try:
                ys.append(float(geo.get("y", 0.0)))
            except (TypeError, ValueError):
                pass
            if orientation is None:
                orientation = geo.get("orientation")

        if not xs and not ys and orientation is None:
            continue

        group_geo = group.setdefault("geometry", {})
        if xs:
            group_geo["x"] = round(min(xs), 6)
        if ys:
            group_geo["y"] = Counter([round(v, 6) for v in ys]).most_common(1)[0][0]
        if orientation:
            group_geo["orientation"] = orientation

def _chain_key(node: dict) -> str:
    """Determine which chain (matched block / device) a finger belongs to."""
    block_id = node.get("_block_id")
    if block_id:
        return block_id
    nid = node.get("id", "")
    m = _re_mod.match(r'^(.+?)(?:_[mf]\d+|_\d+)$', nid)
    return m.group(1) if m else nid


def _cluster_critical_nets_post_expansion(
    nodes: list,
    terminal_nets: dict | None,
    placement_goals: dict | None,
) -> list:
    """Post-expansion critical-net clustering (Step 3e.5).

    Operates on **physical finger nodes** after expand_to_fingers and
    resolve_overlaps have finalized positions.  This is the LAST pass that
    touches X coordinates before DRC, so its output actually survives.

    Algorithm
    ---------
    1. Find all finger IDs connected to each critical net via terminal_nets.
    2. Identify which *chains* (matched blocks or single devices) carry at
       least one critical-net finger.
    3. Per row:
       a. Reorder chains so critical-net chains are contiguous in the centre.
       b. Re-place all chains left-to-right preserving intra-chain spacing.
    4. Align critical-net cluster centres across rows (vertical alignment)
       so cross-row wires are as short as possible.

    Gated: returns nodes unchanged when the feature is OFF.
    """
    try:
        from ai_agent.placement.critical_nets import get_user_critical_nets
    except ImportError:
        return nodes

    # Snapshot for quality-guard rollback if critical clustering harms
    # global routing quality disproportionately.
    original_nodes = copy.deepcopy(nodes)

    _fake = {"placement_goals": placement_goals or {}}
    crit_nets, weight = get_user_critical_nets(_fake)
    print(f"[DIAG-CLUSTER] crit_nets={crit_nets}  weight={weight}  placement_goals={placement_goals}")
    if not crit_nets or weight == 0:
        print("[DIAG-CLUSTER] EARLY EXIT: feature OFF")
        return nodes  # feature OFF — byte-identical path

    if not terminal_nets:
        print("[DIAG-CLUSTER] EARLY EXIT: no terminal_nets")
        return nodes

    # ── 1. Find IDs on critical nets ─────────────────────────────────────
    # terminal_nets is often keyed by logical IDs (e.g. MM1) while nodes are
    # physical fingers (e.g. MM1_m2_f3). Build both:
    #   - critical_finger_ids  : exact keys found in terminal_nets
    #   - critical_logical_ids : logical parent IDs inferred from those keys
    crit_lower = {n.lower() for n in crit_nets}
    critical_finger_ids: set[str] = set()
    critical_logical_ids: set[str] = set()

    def _logical_candidates(dev_id: str) -> set[str]:
        did = str(dev_id or "")
        out = {did}
        # Common physical suffix forms:
        #   MM1_m2_f3 -> MM1_m2 -> MM1
        #   MM1_f3    -> MM1
        #   MM1_m2    -> MM1
        #   MM1_2     -> MM1
        m = _re_mod.match(r"^(.+)_m\d+_f\d+$", did)
        if m:
            out.add(m.group(1))
            out.add(_re_mod.sub(r"_m\d+$", "", m.group(1)))
            return {x for x in out if x}
        out.add(_re_mod.sub(r"_f\d+$", "", did))
        out.add(_re_mod.sub(r"_m\d+$", "", did))
        out.add(_re_mod.sub(r"_\d+$", "", did))
        return {x for x in out if x}

    for fid, pins in terminal_nets.items():
        if not isinstance(pins, dict):
            continue
        for pin_net in pins.values():
            if isinstance(pin_net, str) and pin_net.strip().lower() in crit_lower:
                critical_finger_ids.add(fid)
                critical_logical_ids.update(_logical_candidates(fid))
                break
    print(f"[DIAG-CLUSTER] critical_finger_ids ({len(critical_finger_ids)}): {sorted(critical_finger_ids)[:20]}")
    print(f"[DIAG-CLUSTER] critical_logical_ids ({len(critical_logical_ids)}): {sorted(critical_logical_ids)[:20]}")
    print(f"[DIAG-CLUSTER] terminal_nets keys sample: {sorted(terminal_nets.keys())[:10]}")
    print(f"[DIAG-CLUSTER] node IDs sample: {[n.get('id','?') for n in nodes[:10]]}")
    if not critical_finger_ids and not critical_logical_ids:
        print("[DIAG-CLUSTER] EARLY EXIT: no critical ids found")
        return nodes

    # Build logical-id -> connected net set and per-net fanout to detect
    # high-fanout "anchor" chains (e.g., CLK/tail chain) that should stay
    # near their original X to avoid global routing collapse.
    logical_net_map: dict[str, set[str]] = {}
    net_fanout: dict[str, int] = {}
    for did, pins in terminal_nets.items():
        if not isinstance(pins, dict):
            continue
        did_cands = _logical_candidates(str(did))
        for net_name in pins.values():
            if not isinstance(net_name, str):
                continue
            nn = net_name.strip()
            if not nn:
                continue
            nl = nn.lower()
            net_fanout[nl] = net_fanout.get(nl, 0) + 1
            for lc in did_cands:
                logical_net_map.setdefault(lc, set()).add(nl)

    supply_like = {"vdd", "vss", "gnd", "vcc", "vee", "avdd", "avss"}
    hot_nets = {
        n for n, deg in net_fanout.items()
        if deg >= 8 and n not in crit_lower and n not in supply_like
    }
    print(f"[DIAG-CLUSTER] hot_nets={sorted(hot_nets)}")

    # ── 2. Group nodes by row (Y, type) ──────────────────────────────────
    STD_PITCH = 0.294
    row_buckets: dict[tuple, list[dict]] = {}
    for n in nodes:
        geo = n.get("geometry") or {}
        y = round(float(geo.get("y", 0.0)), 3)
        ntype = str(n.get("type", "nmos")).lower()
        row_buckets.setdefault((y, ntype), []).append(n)

    # ── 3. Per-row chain reordering ──────────────────────────────────────
    #   Track the centre-X of the critical cluster in each row so we can
    #   align them across rows afterwards.
    critical_cluster_info: list[tuple] = []  # (row_key, centre_x, half_width)
    protected_rows: set[tuple] = set()

    for row_key, row_nodes in row_buckets.items():
        # --- build chains ------------------------------------------------
        chains: dict[str, list[dict]] = {}
        for n in row_nodes:
            ck = _chain_key(n)
            chains.setdefault(ck, []).append(n)
        for ck in chains:
            chains[ck].sort(key=lambda n: float((n.get("geometry") or {}).get("x", 0.0)))

        # --- classify chains as critical / non-critical ------------------
        critical_chain_keys: set[str] = set()
        for ck, chain_nodes in chains.items():
            is_critical = False
            for n in chain_nodes:
                nid = str(n.get("id", ""))
                if nid in critical_finger_ids:
                    is_critical = True
                    break
                cands = _logical_candidates(nid)
                cands.add(str(ck))
                if cands & critical_logical_ids:
                    is_critical = True
                    break
            if is_critical:
                critical_chain_keys.add(ck)
        print(f"[DIAG-CLUSTER] row={row_key} chains={list(chains.keys())} critical={critical_chain_keys}")
        if not critical_chain_keys:
            continue  # nothing to do in this row

        # --- compute chain envelopes (origin_x, total_width) -------------
        def _envelope(chain_nodes):
            xs = [float((n.get("geometry") or {}).get("x", 0.0)) for n in chain_nodes]
            ws = [float((n.get("geometry") or {}).get("width", STD_PITCH)) for n in chain_nodes]
            min_x = min(xs)
            max_x_end = max(x + w for x, w in zip(xs, ws))
            return min_x, max_x_end - min_x

        envelopes = {ck: _envelope(cn) for ck, cn in chains.items()}

        # --- sort chains by current X ------------------------------------
        sorted_keys = sorted(chains.keys(), key=lambda ck: envelopes[ck][0])

        crit_keys = [ck for ck in sorted_keys if ck in critical_chain_keys]
        non_crit_keys = [ck for ck in sorted_keys if ck not in critical_chain_keys]

        # Detect a protected non-critical anchor chain (high-fanout net
        # connectivity such as CLK). We keep its X nearly fixed.
        protected_noncrit: list[str] = []
        for ck in non_crit_keys:
            chain_nets: set[str] = set()
            for n in chains[ck]:
                nid = str(n.get("id", ""))
                cands = _logical_candidates(nid)
                cands.add(str(ck))
                for c in cands:
                    chain_nets |= logical_net_map.get(c, set())
            if chain_nets & hot_nets:
                protected_noncrit.append(ck)

        anchor_key = None
        if protected_noncrit:
            anchor_key = min(protected_noncrit, key=lambda k: envelopes[k][0])
            protected_rows.add(row_key)
        print(f"[DIAG-CLUSTER] row={row_key} protected_noncrit={protected_noncrit} anchor={anchor_key}")

        # Split non-critical chains into left-flank and right-flank.
        # Prefer putting half on each side so critical chains stay centred.
        if anchor_key and anchor_key in non_crit_keys:
            rest = [k for k in non_crit_keys if k != anchor_key]
            anchor_x = envelopes[anchor_key][0]
            left_keys = [k for k in rest if envelopes[k][0] < anchor_x]
            right_keys = [k for k in rest if envelopes[k][0] >= anchor_x]
            # Keep critical chains adjacent to the protected anchor so we
            # tighten target nets without dragging the anchor chain itself.
            new_order = left_keys + crit_keys + [anchor_key] + right_keys
        else:
            n_left = len(non_crit_keys) // 2
            left_keys = non_crit_keys[:n_left]
            right_keys = non_crit_keys[n_left:]
            new_order = left_keys + crit_keys + right_keys
        print(f"[DIAG-CLUSTER] row={row_key} sorted_keys={sorted_keys} new_order={new_order} crit={crit_keys} non_crit={non_crit_keys}")

        # --- re-place chains -----------------------------------------------
        if anchor_key and anchor_key in new_order:
            # Keep anchor X fixed; place left and right sides relative to it.
            a_idx = new_order.index(anchor_key)
            left_part = new_order[:a_idx]
            right_part = new_order[a_idx + 1:]
            anchor_origin = envelopes[anchor_key][0]
            anchor_width = envelopes[anchor_key][1]

            # Anchor remains at original origin
            for n in chains[anchor_key]:
                geo = n.setdefault("geometry", {})
                geo["x"] = round(float(geo.get("x", 0.0)), 6)

            # Left side (place outward from anchor)
            cursor_left = round(anchor_origin - STD_PITCH, 6)
            for ck in reversed(left_part):
                chain_nodes = chains[ck]
                old_origin = envelopes[ck][0]
                chain_width = envelopes[ck][1]
                new_origin = round(cursor_left - chain_width, 6)
                shift = new_origin - old_origin
                if abs(shift) > 1e-6:
                    for n in chain_nodes:
                        geo = n.setdefault("geometry", {})
                        geo["x"] = round(float(geo.get("x", 0.0)) + shift, 6)
                cursor_left = round(new_origin - STD_PITCH, 6)

            # Right side
            cursor_right = round(anchor_origin + anchor_width + STD_PITCH, 6)
            for ck in right_part:
                chain_nodes = chains[ck]
                old_origin = envelopes[ck][0]
                chain_width = envelopes[ck][1]
                new_origin = cursor_right
                shift = new_origin - old_origin
                if abs(shift) > 1e-6:
                    for n in chain_nodes:
                        geo = n.setdefault("geometry", {})
                        geo["x"] = round(float(geo.get("x", 0.0)) + shift, 6)
                cursor_right = round(new_origin + chain_width + STD_PITCH, 6)
        else:
            # Start from the leftmost position in the row.
            row_min_x = min(envelopes[ck][0] for ck in sorted_keys)
            cursor = row_min_x

            for ck in new_order:
                chain_nodes = chains[ck]
                old_origin = envelopes[ck][0]
                shift = cursor - old_origin
                if abs(shift) > 1e-6:
                    for n in chain_nodes:
                        geo = n.setdefault("geometry", {})
                        geo["x"] = round(float(geo.get("x", 0.0)) + shift, 6)
                # advance cursor past this chain + inter-chain gap
                chain_width = envelopes[ck][1]
                cursor = round(cursor + chain_width + STD_PITCH, 6)

        # --- record critical cluster centre for cross-row alignment ------
        crit_xs = []
        crit_ws = []
        for ck in crit_keys:
            for n in chains[ck]:
                geo = n.get("geometry") or {}
                crit_xs.append(float(geo.get("x", 0.0)))
                crit_ws.append(float(geo.get("width", STD_PITCH)))
        if crit_xs:
            crit_min = min(crit_xs)
            crit_max = max(x + w for x, w in zip(crit_xs, crit_ws))
            centre = (crit_min + crit_max) / 2.0
            half_w = (crit_max - crit_min) / 2.0
            critical_cluster_info.append((row_key, centre, half_w))

        vprint(
            f"[critical_nets_cluster] row={row_key} "
            f"reordered: {' | '.join(new_order)}  "
            f"(critical: {', '.join(crit_keys)})"
        )

    # ── 4. Cross-row vertical alignment ──────────────────────────────────
    if weight >= 5 and len(critical_cluster_info) > 1:
        centres = [c for _, c, _ in critical_cluster_info]
        median_centre = sorted(centres)[len(centres) // 2]

        for row_key, centre, _hw in critical_cluster_info:
            if row_key in protected_rows:
                # Preserve protected-anchor rows (e.g., CLK-heavy rows) to
                # prevent collateral degradation in non-target nets.
                continue
            shift = median_centre - centre
            if abs(shift) < 1e-4:
                continue
            # Shift ALL nodes in this row so relative chain positions stay valid
            for n in row_buckets[row_key]:
                geo = n.setdefault("geometry", {})
                geo["x"] = round(float(geo.get("x", 0.0)) + shift, 6)

        vprint(
            f"[critical_nets_cluster] cross-row alignment: "
            f"median_centre={median_centre:.4f}  "
            f"rows_aligned={len(critical_cluster_info)}"
        )

    # ── 5. Quality guard (multi-objective accept/reject) ─────────────────
    # Accept critical-net reshaping only when it does not degrade global
    # routing quality beyond a bounded margin.  If it over-optimises VOUT*
    # while blowing up CLK/net2 HPWL, roll back to original nodes.
    try:
        from ai_agent.agents.routing_previewer import build_routing_report

        crit_set = set(crit_nets)
        crit_lower2 = {n.lower() for n in crit_nets}

        before = build_routing_report(
            original_nodes, [], terminal_nets or {}, user_critical_nets=crit_set
        )
        after = build_routing_report(
            nodes, [], terminal_nets or {}, user_critical_nets=crit_set
        )

        def _crit_hpwl(rep) -> float:
            return sum(
                float(n.hpwl)
                for n in getattr(rep, "nets", [])
                if str(getattr(n, "name", "")).lower() in crit_lower2
            )

        before_crit = _crit_hpwl(before)
        after_crit = _crit_hpwl(after)

        before_cost = float(getattr(before, "weighted_cost", 0.0))
        after_cost = float(getattr(after, "weighted_cost", 0.0))

        before_cross = int(getattr(before, "estimated_crossings", 0))
        after_cross = int(getattr(after, "estimated_crossings", 0))

        # Priority-aware tolerances: High allows a little more tradeoff.
        if weight >= 10:  # High
            max_cost_regress = 0.10   # +10%
            max_cross_regress = 3     # +3 crossings
            min_crit_improve = 0.10   # -10% critical HPWL
        elif weight >= 5:  # Medium
            max_cost_regress = 0.06
            max_cross_regress = 2
            min_crit_improve = 0.06
        else:
            max_cost_regress = 0.03
            max_cross_regress = 1
            min_crit_improve = 0.03

        crit_improve_ratio = (
            (before_crit - after_crit) / max(before_crit, 1e-9)
            if before_crit > 1e-9 else 0.0
        )
        cost_regress_ratio = (
            (after_cost - before_cost) / max(before_cost, 1e-9)
            if before_cost > 1e-9 else 0.0
        )
        cross_regress = after_cross - before_cross

        # Always accept if global quality is not worse.
        global_not_worse = (
            after_cost <= before_cost + 1e-9 and
            after_cross <= before_cross
        )

        # Otherwise require meaningful critical improvement with bounded damage.
        bounded_tradeoff = (
            crit_improve_ratio >= min_crit_improve and
            cost_regress_ratio <= max_cost_regress and
            cross_regress <= max_cross_regress
        )

        if not (global_not_worse or bounded_tradeoff):
            vprint(
                "[critical_nets_cluster] rollback: "
                f"crit_hpwl {before_crit:.3f}->{after_crit:.3f} "
                f"(improve={crit_improve_ratio:.1%}), "
                f"cost {before_cost:.1f}->{after_cost:.1f} "
                f"(regress={cost_regress_ratio:.1%}), "
                f"cross {before_cross}->{after_cross}"
            )
            return original_nodes
    except Exception as _guard_exc:
        vprint(f"[critical_nets_cluster] quality-guard skipped: {_guard_exc}")

    return nodes


def node_placement_specialist(state):

    """Primary placement node.

    Uses ``_compute_matching_and_rows`` from the placement agent module to
    build a richer context that includes pre-computed row assignments and
    matching constraint strings.  The resulting ``finger_map`` and ``merged``
    block dict drive finger expansion and conservation checks.
    """
    t0 = time.time()
    stage_start(3, "Placement Specialist")

    nodes           = state.get("nodes", [])
    constraint_text = state.get("constraint_text", "")
    user_message    = state.get("user_message", "Optimize placement.")
    chat_history    = state.get("chat_history", [])
    edges           = state.get("edges", [])
    terminal_nets   = state.get("terminal_nets", {})
    strategy_result = state.get("strategy_result", "auto")
    selected_model  = state.get("selected_model", "Gemini")
    no_abutment_flag = state.get("no_abutment", False)

    working_nodes = state.get("placement_nodes", []) or copy.deepcopy(nodes)

    n_pmos = sum(1 for n in nodes if n.get("type") == "pmos")
    n_nmos = sum(1 for n in nodes if n.get("type") == "nmos")
    log_detail(f"Input: {len(nodes)} devices ({n_pmos} PMOS + {n_nmos} NMOS)")
    log_detail(f"Edges: {len(edges)} | Terminal nets: {len(terminal_nets)}")
    log_detail(f"Strategy: {strategy_result}")

    # ── Step 3a: Build context (matching + row assignment) ───────────────────
    log_section("Step 3a: Computing matching groups & row assignments")
    # placement_goals is None when the panel was collapsed -> original defaults
    raw_goals = state.get("placement_goals")   # None = panel not opened
    goals_for_context = raw_goals or {}        # {} -> all defaults in helpers
    goals_active = raw_goals is not None       # True only when panel was used

    if goals_active:
        match_priority = goals_for_context.get("matching_priority", "High")
        area_priority = goals_for_context.get("area_priority", "Medium")
        log_detail("Goals panel was OPEN - applying user priorities")
        log_detail(
            f"  Matching={match_priority}  Area={area_priority}  "
            f"Symmetry={goals_for_context.get('symmetry_priority','Medium')}"
        )
    else:
        match_priority = "High"
        area_priority = "Medium"
        log_detail("Goals panel was CLOSED - running with original pipeline defaults")

    context_text = build_placement_context(
        nodes,
        constraint_text,
        terminal_nets=terminal_nets,
        edges=edges,
        no_abutment=no_abutment_flag,
        placement_goals=goals_for_context if goals_active else None,
    )

    grp_nodes  = copy.deepcopy(nodes)
    finger_map = {}
    merged     = {}
    groups     = {}
    try:
        from ai_agent.agents.placement_specialist import _compute_matching_and_rows
        grp_nodes, finger_map, row_str, match_str, _, merged, groups, abutment_candidates = _compute_matching_and_rows(
            nodes, edges, terminal_nets,
            no_abutment=no_abutment_flag,
            matching_priority=match_priority,
            area_priority=area_priority,
        )
        log_detail(
            f"Finger grouping: {len(nodes)} fingers -> {len(grp_nodes)} logical groups"
        )
        log_detail(
            f"Matching priority={match_priority}  area_priority={area_priority}"
        )
        log_detail(
            f"Matched blocks: {len(merged)} "
            f"({', '.join(merged.keys()) if merged else 'none'})"
        )
        if groups:
            log_detail(f"Groups captured: {len(groups)}")
        if row_str:
            log_section("Pre-computed Row Assignments")
            for line in row_str.strip().split("\n"):
                log_detail(line.strip())
        if match_str:
            log_section("Matching Constraints")
            for line in match_str.strip().split("\n")[:20]:
                log_detail(line.strip())
    except Exception as exc:
        log_detail(f"WARNING: matching/row computation failed: {exc}")

    # ── Step 3b: Call LLM for placement commands ───────────────────────────
    log_section("Step 3b: Calling LLM for placement commands")

    goals = state.get("placement_goals") or {}
    goals_paragraph = _goals_to_prompt(goals)
    if goals_paragraph:
        log_detail(f"Goals injected: area={goals.get('area_priority','Medium')} "
                   f"matching={goals.get('matching_priority','Medium')} "
                   f"symmetry={goals.get('symmetry_priority','Medium')} "
                   f"max_area={goals.get('max_area_um2')}")

    placer_user = (
        f"{goals_paragraph}"
        f"User request: {user_message}\n\n"
        f"Selected Strategy: {strategy_result}\n\n"
        f"{context_text}"
    )

    chat_history = _update_and_save_chat_history(
        chat_history=chat_history,
        user_content="",
        node_role="System",
        node_content="Starting **Placement Specialist**...",
    )

    # _PLACEMENT_SYSTEM_PROMPT and _PLACEMENT_TOOLS are pre-built at import time.
    log_detail(f"Prompt size: {len(_PLACEMENT_SYSTEM_PROMPT)} chars (augmented)")

    placement_response_text = ""
    placement_text = ""
    stage2_cmds    = []
    try:
        llm_t0 = time.time()
        placement_msgs = _build_llm_messages(
            _PLACEMENT_SYSTEM_PROMPT,
            chat_history,
            placer_user,
        )
        placement_result = _invoke_with_retry(
            placement_msgs,
            selected_model,
            "heavy",
            "PLACEMENT",
        )
        llm_elapsed = time.time() - llm_t0

        placement_response_text, _ = _extract_agent_output_parts(placement_result)
        stage2_cmds, placement_text = extract_cmd_blocks(placement_response_text)

        log_detail(f"LLM responded in {llm_elapsed:.1f}s")
        log_detail(f"LLM produced {len(stage2_cmds)} CMD block(s)")
    except Exception as exc:
        log_detail(f"ERROR: LLM failed: {exc}")
        placement_response_text = "[PLACEMENT] LLM failed."
        placement_text = placement_response_text

    # ── Step 3c: Apply commands ──────────────────────────────────────────────
    log_section("Step 3c: Applying placement commands")
    if stage2_cmds:
        for i, cmd in enumerate(stage2_cmds):
            dev = cmd.get("device", cmd.get("device_id", cmd.get("id", "?")))
            log_detail(
                f"CMD[{i+1}]: {cmd.get('action', '?')} {dev} "
                f"→ x={cmd.get('x', '?')}, y={cmd.get('y', '?')}"
            )
    else:
        log_detail("No commands from LLM — using pre-computed positions")

    updated_chat_history = _update_and_save_chat_history(
        chat_history=chat_history,
        user_content=user_message,
        node_role="Placement Specialist Assistant",
        node_content=placement_text,
    )

    working_nodes = apply_cmds_to_nodes(grp_nodes, stage2_cmds)
    detect_abutment_intent(working_nodes, terminal_nets)
    working_nodes = enforce_reflection_symmetry(working_nodes)



    # ── Step 3d: Expand to physical fingers ──────────────────────────────────
    log_section("Step 3d: Expanding to physical fingers")
    if finger_map:
        from ai_agent.placement.finger_grouper import expand_to_fingers
        orig_lookup = {n["id"]: n for n in grp_nodes}
        log_detail(
            f"Expanding {len(working_nodes)} groups via finger_map "
            f"({len(finger_map)} entries)"
        )
        working_nodes = expand_to_fingers(
            working_nodes, finger_map,
            no_abutment=no_abutment_flag,
            original_group_nodes=orig_lookup,
        )
        log_detail(f"Expanded to {len(working_nodes)} physical devices")
    else:
        from ai_agent.placement.finger_grouper import expand_logical_to_fingers
        working_nodes = expand_logical_to_fingers(working_nodes, nodes)
        log_detail(f"Legacy expansion → {len(working_nodes)} devices")

    # ── Step 3e: Post-expansion overlap resolution ───────────────────────────
    log_section("Step 3e: Post-expansion overlap resolution")
    moved_ids = resolve_overlaps(working_nodes)
    log_detail(
        f"Fixed overlaps for {len(moved_ids)} device(s)" if moved_ids
        else "No overlaps detected after expansion"
    )
    working_nodes = legalize_vertical_rows(working_nodes)

    # ── Snap orphan dummies (flying-transistor fix) ─────────────────────
    # When matching=Low (skip_matching), dummy devices (D-prefixed) that were
    # previously anchored inside ABBA blocks may end up at isolated Y coords.
    # Move any such device to the nearest valid active-device row.
    working_nodes = _snap_orphan_dummies(working_nodes)

    # ── Step 3e.5: Critical-net signal-flow optimization ─────────────────
    # Move whole rows only, so row internals and matching stay untouched.
    # Gated: completely skipped when feature is off (byte-identical path).
    log_section("Step 3e.5: Critical-net signal-flow optimization")
    from ai_agent.placement.critical_signal_flow import optimize_critical_signal_flow
    before_signal_flow = copy.deepcopy(working_nodes)
    working_nodes = optimize_critical_signal_flow(
        working_nodes, terminal_nets, state.get("placement_goals")
    )
    _goals_cfg = (state.get("placement_goals") or {}).get("critical_nets") or {}
    if _goals_cfg.get("nets") and _goals_cfg.get("priority", "Low") != "Low":
        log_detail(
            f"Critical nets active: {_goals_cfg.get('nets')} "
            f"priority={_goals_cfg.get('priority')}"
        )
        if working_nodes != before_signal_flow:
            log_detail("Critical signal-flow row order accepted")
    else:
        log_detail("Critical nets: OFF (skipped)")

    # ── Step 3f: Validate device conservation ────────────────────────────
    log_section("Step 3f: Device conservation check")
    conservation = validate_device_count(nodes, working_nodes)
    if not conservation["pass"]:
        log_detail(f"CONSERVATION FAILURE: missing={conservation.get('missing', [])}")
        log_detail("Falling back to original positions")
        working_nodes = copy.deepcopy(nodes)
        stage2_cmds   = []
    else:
        log_detail(f"Conservation OK: all {conservation['original_count']} devices present")

    log_device_positions(working_nodes, "Final Placement Positions")

    # ── Step 3g: Quality benchmark ───────────────────────────────────────
    log_section("Step 3g: Placement Quality Benchmark")
    try:
        quality_report = score_placement(
            working_nodes,
            matching_info=merged if merged else None,
            finger_map=finger_map if finger_map else None,
            verbose=True,
        )
        log_detail(quality_report["summary"])
        if "details" in quality_report:
            for metric, detail_text in quality_report["details"].items():
                if detail_text:
                    log_detail(f"[{metric}]\n{detail_text}")
        composite = quality_report["composite_score"]

        def _fmt(v):
            return f"{v:.1%}" if v is not None else "N/A"

        log_detail(
            f"Quality: Y={_fmt(quality_report.get('layout_y_score'))}  "
            f"X={_fmt(quality_report.get('matching_x_score'))}  "
            f"Interdig={_fmt(quality_report.get('interdigitation_score'))}  "
            f"Centroid={_fmt(quality_report.get('centroid_score'))}  "
            f"DRC={_fmt(quality_report.get('drc_score'))}  "
            f"-> COMPOSITE={composite:.1%}"
        )
    except Exception as _q_exc:
        log_detail(f"WARNING: quality benchmark failed: {_q_exc}")
        quality_report = {}
        composite = 0.0

    elapsed = time.time() - t0
    cons = "ok" if conservation["pass"] else "FAILED"
    q_str = f", quality={composite:.1%}" if quality_report else ""
    ip_step(
        "3/5 Placement Specialist",
        f"{len(stage2_cmds)} cmd(s), {elapsed:.1f}s, conservation={cons}{q_str}",
    )

    return {
        "placement_nodes":         working_nodes,
        "pending_cmds":            state.get("pending_cmds", []) + stage2_cmds,
        "original_placement_cmds": state.get("pending_cmds", []) + stage2_cmds,
        "chat_history":            updated_chat_history,
        "placement_quality":       quality_report,
        "placement_text":          placement_text,
        "groups":                  groups,
        "abutment_candidates":     abutment_candidates if 'abutment_candidates' in locals() else [],
        "last_agent":              "placement_specialist",
    }


# ── Node: chatbot / interactive path ────────────────────────────────────────

def node_placement_specialist_chatbot(state):
    """Chat-mode placement node.

    Uses ``aggregate_to_logical_devices`` for lightweight grouping instead of
    the heavier ``_compute_matching_and_rows`` path.  Otherwise shares the
    same ReAct + SkillMiddleware flow as the primary node.
    """
    t0 = time.time()
    stage_start(3, "Placement Specialist (Chat)")

    nodes            = state.get("nodes", [])
    constraint_text  = state.get("constraint_text", "")
    user_message     = state.get("user_message", "Optimize placement.")
    chat_history     = state.get("chat_history", [])
    edges            = state.get("edges", [])
    terminal_nets    = state.get("terminal_nets", {})
    strategy_result  = state.get("strategy_result", "auto")
    selected_model   = state.get("selected_model", "Gemini")
    no_abutment_flag = state.get("no_abutment", False)
    drc_passed = state.get("drc_pass", False)
    drc_violations = state.get("drc_flags", [])

    working_nodes = state.get("placement_nodes", []) or copy.deepcopy(nodes)

    n_pmos = sum(1 for n in nodes if n.get("type") == "pmos")
    n_nmos = sum(1 for n in nodes if n.get("type") == "nmos")
    log_detail(f"Input: {len(nodes)} devices ({n_pmos} PMOS + {n_nmos} NMOS)")
    log_detail(f"Edges: {len(edges)} | Terminal nets: {len(terminal_nets)}")
    log_detail(f"Strategy: {strategy_result}")

    # ── Step 3a: Build placement context ─────────────────────────────────────
    log_section("Step 3a: Building placement context (chat mode)")
    context_text = build_placement_context_chatbot(
        nodes, constraint_text,
        terminal_nets=terminal_nets, edges=edges, no_abutment=no_abutment_flag,
    )

    vprint("Placement context")
    vprint(context_text)
    vprint("-" * 40)   

    # ── Step 3b: Call LLM via ReAct + SkillMiddleware ───────────────────────
    log_section("Step 3b: Calling LLM (ReAct + SkillMiddleware)")
    drc_note = ""
    if not drc_passed:
        violations_text = "\n".join(str(v) for v in drc_violations) if drc_violations else "(none provided)"
        drc_note = (
            "DRC failed in the previous step. Please fix these DRC violations:\n"
            f"{violations_text}\n\n"
        )
    placer_user = (
        f"User request: {user_message}\n\n"
        f"Selected Strategy: {strategy_result}\n\n"
        f"{drc_note}"
        f"{context_text}"
    )

    chat_history = _update_and_save_chat_history(
        chat_history=chat_history,
        user_content="",
        node_role="System",
        node_content="Starting **Placement Specialist**...",
    )

    placement_response_text = ""
    placement_text = ""
    stage2_cmds    = []
    try:
        llm_t0 = time.time()
        placement_result = _invoke_react_agent_with_retry(
            system_prompt=PLACEMENT_SPECIALIST_PROMPT_CHATBOT,
            chat_history=chat_history,
            user_prompt=placer_user,
            selected_model=selected_model,
            task_weight="heavy",
            stage_tag="PLACEMENT",
        )
        vprint("Raw LLM output:")
        vprint(placement_result)
        placement_response_text, thinking = _extract_agent_output_parts(placement_result)
        llm_elapsed = time.time() - llm_t0

        stage2_cmds, placement_text = extract_cmd_blocks(placement_response_text)

        log_detail(f"LLM responded in {llm_elapsed:.1f}s")
        log_detail(f"LLM produced {len(stage2_cmds)} CMD block(s)")
        log_detail(f"LLM Commands content:\n{stage2_cmds}")
        log_detail(f"LLM Placement text:\n{placement_text}")
    except Exception as exc:
        log_detail(f"ERROR: LLM failed: {exc}")
        placement_response_text = "[PLACEMENT] LLM failed."
        placement_text = placement_response_text

    # ── Step 3c: Apply commands ──────────────────────────────────────────────
    log_section("Step 3c: Applying placement commands")
    if stage2_cmds:
        for i, cmd in enumerate(stage2_cmds):
            dev = cmd.get("device", cmd.get("device_id", cmd.get("id", "?")))
            log_detail(
                f"CMD[{i+1}]: {cmd.get('action', '?')} {dev} "
                f"-> x={cmd.get('x', '?')}, y={cmd.get('y', '?')}"
            )
    else:
        log_detail("No commands from LLM - using current positions")

    updated_chat_history = _update_and_save_chat_history(
        chat_history=chat_history,
        user_content=user_message,
        node_role="Placement Specialist Assistant",
        node_content=placement_text,
    )

    working_nodes = apply_cmds_to_nodes(nodes, stage2_cmds)
    detect_abutment_intent(working_nodes, terminal_nets)
    working_nodes = enforce_reflection_symmetry(working_nodes)

    # ── Step 3e: Post-expansion overlap resolution ───────────────────────────
    log_section("Step 3e: Post-expansion overlap resolution")
    moved_ids = resolve_overlaps(working_nodes)
    log_detail(
        f"Fixed overlaps for {len(moved_ids)} device(s)" if moved_ids
        else "No overlaps detected after expansion"
    )
    working_nodes = legalize_vertical_rows(working_nodes)

    # ── Step 3f: Device conservation check ──────────────────────────────────
    log_section("Step 3f: Device conservation check")
    conservation = validate_device_count(nodes, working_nodes)
    if not conservation["pass"]:
        log_detail(f"CONSERVATION FAILURE: missing={conservation.get('missing', [])}")
        log_detail("Falling back to original positions")
        working_nodes = copy.deepcopy(nodes)
        stage2_cmds   = []
    else:
        log_detail(f"Conservation OK: all {conservation['original_count']} devices present")

    log_device_positions(working_nodes, "Final Placement Positions")

    elapsed = time.time() - t0
    cons = "ok" if conservation["pass"] else "FAILED"
    ip_step(
        "3/5 Placement Specialist",
        f"{len(stage2_cmds)} cmd(s), {elapsed:.1f}s, conservation={cons}",
    )

    return {
        "placement_nodes":         working_nodes,
        "pending_cmds":            stage2_cmds,
        "original_placement_cmds": stage2_cmds,
        "chat_history":            updated_chat_history,
        "placement_text":          placement_text,
        "last_agent":              "placement_specialist",
    }

"""
Command parsing and application helpers
"""
import re
import json
import copy
from typing import List
DEFAULT_MIN_DEVICE_SPACING_UM: float = 0.294
NMOS_ROW_Y_MIN: float = 0.0

def _log(msg: str):
    print(f"[CMD] {msg}")

def _device_is_nmos(node: dict) -> bool:
    dev_type = str(node.get("type", "")).lower()
    if dev_type.startswith("p"):
        return False
    return True


def _pmos_above_nmos(nodes: List[dict]) -> bool:
    """Return True when all PMOS are above all NMOS (PMOS y > NMOS y)."""
    pmos_ys = []
    nmos_ys = []
    for n in nodes:
        if "geometry" not in n:
            continue
        y = float(n["geometry"].get("y", 0.0))
        if _device_is_nmos(n):
            nmos_ys.append(y)
        else:
            pmos_ys.append(y)

    if not pmos_ys or not nmos_ys:
        return True

    return min(pmos_ys) > max(nmos_ys)

def _deduplicate_positions(
    nodes: List[dict],
    min_spacing: float = DEFAULT_MIN_DEVICE_SPACING_UM
):
    rows = {}
    for n in nodes:
        ry = round(float(n['geometry']['y']), 4)
        rows.setdefault(ry, []).append(n)

    for row_nodes in rows.values():
        row_nodes.sort(key=lambda n: float(n['geometry']['x']))
        for i in range(1, len(row_nodes)):
            prev = row_nodes[i - 1]
            curr = row_nodes[i]
            prev_end = float(prev['geometry']['x']) + float(prev['geometry']['width'])
            curr_x   = float(curr['geometry']['x'])
            if curr_x < prev_end - 0.001:
                snapped = round(prev_end / min_spacing) * min_spacing
                if snapped < prev_end - 0.001:
                    snapped += min_spacing
                curr['geometry']['x'] = snapped

def _extract_cmd_blocks(text: str) -> List[dict]:
    if not text:
        return []

    text = re.sub(r'```[a-zA-Z]*\n?', '', text)
    text = re.sub(r'```', '', text)
    text = text.replace('\uff3b', '[').replace('\uff3d', ']')
    text = text.replace('\u27e6', '[').replace('\u27e7', ']')
    text = re.sub(
        r'\[\s*/?\s*[Cc][Mm][Dd]\s*\]',
        lambda m: '[/CMD]' if '/' in m.group() else '[CMD]',
        text,
    )

    cmds: List[dict] = []
    pattern = re.compile(r'\[CMD\](.*?)\[/CMD\]', re.DOTALL | re.IGNORECASE)

    for match in pattern.finditer(text):
        raw = match.group(1).strip()
        raw = re.sub(r'```[a-zA-Z]*', '', raw).strip()
        if not raw:
            _log("Warning: empty CMD block skipped")
            continue
        try:
            cmds.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            repaired = re.sub(r',\s*}', '}', raw)
            repaired = re.sub(r',\s*\]', ']', repaired)
            repaired = repaired.replace("'", '"')
            try:
                cmds.append(json.loads(repaired))
                _log(f"Warning: CMD block auto-repaired: {raw[:80]!r}")
            except json.JSONDecodeError:
                # Handle escaped JSON bodies such as:
                # {"action": "move", "device": "MM28", ...}
                unescaped = repaired.replace('\"', '"')
                try:
                    cmds.append(json.loads(unescaped))
                except json.JSONDecodeError:
                    _log(f"Warning: skipping malformed CMD block: {raw[:80]!r} (error: {exc})")

    if not cmds:
        raw_markers = re.findall(r'(?i)\[/?cmd\]|［/?CMD］', text)
        if raw_markers:
            _log(f"⚠ Found {len(raw_markers)} CMD markers but parsed 0 blocks")

    return cmds

def _apply_cmds_to_nodes(nodes: List[dict], cmds: List[dict]) -> List[dict]:
    nodes  = copy.deepcopy(nodes)
    id_map = {n['id']: n for n in nodes}
    moved_y_before = {}
    moved_y_non_forced_ids = set()

    for cmd in cmds:
        action = cmd.get('action', '').lower()

        if action in ('swap', 'swap_devices'):
            a_id = cmd.get('device_a', cmd.get('a'))
            b_id = cmd.get('device_b', cmd.get('b'))
            if a_id in id_map and b_id in id_map:
                ga, gb = id_map[a_id]['geometry'], id_map[b_id]['geometry']
                ga['x'], gb['x'] = gb['x'], ga['x']
                ga['y'], gb['y'] = gb['y'], ga['y']
                ga['orientation'], gb['orientation'] = (
                    gb.get('orientation', 'R0'),
                    ga.get('orientation', 'R0')
                )

        elif action in ('move', 'move_device'):
            dev_id = cmd.get('device', cmd.get('device_id', cmd.get('id')))
            if dev_id in id_map:
                node = id_map[dev_id]
                if cmd.get('x') is not None:
                    node['geometry']['x'] = float(cmd['x'])
                if cmd.get('y') is not None:
                    if dev_id not in moved_y_before:
                        moved_y_before[dev_id] = float(node['geometry'].get('y', 0.0))
                    proposed_y = float(cmd['y'])
                    force_y    = bool(cmd.get('force_y', False))
                    node['geometry']['y'] = proposed_y
                    if not force_y:
                        moved_y_non_forced_ids.add(dev_id)
            else:
                _log(f"  MOVE: device not found: {dev_id!r}")

        elif action in ('flip', 'flip_h', 'flip_v'):
            dev_id = cmd.get('device', cmd.get('id'))
            if dev_id in id_map:
                cur      = id_map[dev_id]['geometry'].get('orientation', 'R0')
                flip_map = {'R0': 'R0_FH', 'R0_FH': 'R0', 'R0_FV': 'R0_FH_FV', 'R0_FH_FV': 'R0_FV'}
                id_map[dev_id]['geometry']['orientation'] = flip_map.get(cur, cur)

        elif action == 'delete':
            dev_id = cmd.get('device', cmd.get('id'))
            nodes  = [n for n in nodes if n['id'] != dev_id]
            id_map = {n['id']: n for n in nodes}

    # Enforce row ordering globally after all commands:
    # PMOS must remain above NMOS. If violated, rollback only non-forced Y moves.
    if moved_y_non_forced_ids and not _pmos_above_nmos(nodes):
        for dev_id in moved_y_non_forced_ids:
            if dev_id in id_map and dev_id in moved_y_before:
                id_map[dev_id]['geometry']['y'] = moved_y_before[dev_id]
                _log(
                    f"  ⚠ MOVE {dev_id}: reverted y-change "
                    f"(PMOS row must stay above NMOS row)"
                )

    _deduplicate_positions(nodes)
    return nodes
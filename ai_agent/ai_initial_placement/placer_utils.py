import json
import re
from collections import defaultdict
import copy

# ---------------------------------------------------------------------------
# Robust JSON sanitizer
# ---------------------------------------------------------------------------
def _repair_truncated_json(text: str) -> str:
    """
    Fix truncated JSON by closing any unclosed brackets and braces.
    Handles the common case where Gemini hits token limit mid-output.
    """
    text = text.rstrip()
    if text.endswith(","):
        text = text[:-1]

    open_braces = 0
    open_brackets = 0
    in_string = False
    escape = False

    for ch in text:
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            open_braces += 1
        elif ch == '}':
            open_braces -= 1
        elif ch == '[':
            open_brackets += 1
        elif ch == ']':
            open_brackets -= 1

    text = re.sub(r',\s*$', '', text)
    text += ']' * max(0, open_brackets)
    text += '}' * max(0, open_braces)
    return text

def sanitize_json(text: str) -> dict:
    """
    Extract and sanitize LLM output into strict JSON.
    Handles: markdown fences, trailing commas, comments, and truncated output.
    """
    if not text or len(text.strip()) == 0:
        raise ValueError("Empty response from LLM")

    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    cleaned = re.sub(r"```json\s*", "", text)
    cleaned = re.sub(r"```\s*", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    brace_pos = cleaned.find('{')
    if brace_pos == -1:
        raise ValueError(f"No JSON object found. Raw text:\n{text[:500]}")

    s = cleaned[brace_pos:]
    s = re.sub(r'//[^\n]*', '', s)
    s = re.sub(r',\s*([\]}])', r'\1', s)

    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    s_repaired = _repair_truncated_json(s)
    try:
        return json.loads(s_repaired)
    except json.JSONDecodeError:
        pass

    for trim in range(50, min(len(s), 2000), 50):
        attempt = _repair_truncated_json(s[:-trim])
        try:
            result = json.loads(attempt)
            if isinstance(result, dict) and "nodes" in result:
                print(f"[sanitize_json] Recovered JSON by trimming {trim} chars from end")
                return result
        except json.JSONDecodeError:
            continue

    raise ValueError(f"Could not parse LLM output as JSON. First 500 chars: {s[:500]}")

def _ensure_placement_dict(parsed) -> dict:
    """Normalise the result of sanitize_json() to always be a dict with a 'nodes' key."""
    if isinstance(parsed, list):
        return {"nodes": parsed}
    if isinstance(parsed, dict):
        if "nodes" not in parsed:
            for key in ("placement", "result", "layout", "devices", "placements"):
                if key in parsed and isinstance(parsed[key], list):
                    return {"nodes": parsed[key]}
        return parsed
    raise ValueError(f"Unexpected JSON type from LLM: {type(parsed).__name__}.")

# ---------------------------------------------------------------------------
# Pre-analysis helpers (pure Python - no LLM)
# ---------------------------------------------------------------------------
def _build_net_adjacency(nodes: list, edges: list) -> str:
    """Return a human-readable adjacency table for injection into the prompt."""
    _POWER = frozenset({"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS"})
    net_devs: dict = defaultdict(set)
    for e in edges:
        net = e.get("net", "")
        if net and net.upper() not in _POWER:
            net_devs[net].add(e.get("source", ""))
            net_devs[net].add(e.get("target", ""))

    if not net_devs:
        return "  (no signal nets found)"

    dev_type = {n["id"]: n.get("type", "nmos") for n in nodes}
    lines = []
    for net in sorted(net_devs):
        devs = sorted(d for d in net_devs[net] if d)
        pmos = [d for d in devs if dev_type.get(d) == "pmos"]
        nmos = [d for d in devs if dev_type.get(d) == "nmos"]
        cross = " [CROSS-ROW]" if pmos and nmos else ""
        lines.append(f"  {net:<12} -> {', '.join(devs)}{cross}")
    return "\n".join(lines)

def _build_device_inventory(nodes: list) -> str:
    """Return a structured device inventory string for the prompt."""
    pmos = [n for n in nodes if n.get("type") == "pmos"]
    nmos = [n for n in nodes if n.get("type") == "nmos"]
    lines = []
    lines.append(f"  TOTAL: {len(nodes)} devices ({len(pmos)} PMOS, {len(nmos)} NMOS)")
    lines.append("")
    lines.append("  PMOS devices (must all be placed in the PMOS row):")
    for n in sorted(pmos, key=lambda x: x["id"]):
        e = n.get("electrical", {})
        lines.append(f"    {n['id']:<10}  nfin={e.get('nfin',1)}  nf={e.get('nf',1)}  l={e.get('l')}")
    lines.append("")
    lines.append("  NMOS devices (must all be placed in the NMOS row):")
    for n in sorted(nmos, key=lambda x: x["id"]):
        e = n.get("electrical", {})
        lines.append(f"    {n['id']:<10}  nfin={e.get('nfin',1)}  nf={e.get('nf',1)}  l={e.get('l')}")
    return "\n".join(lines)

def _build_block_info(nodes: list, graph_data: dict) -> str:
    """Return a human-readable block grouping summary for the prompt."""
    blocks = graph_data.get("blocks", {})
    if not blocks:
        for n in nodes:
            b = n.get("block")
            if b:
                inst = b.get("instance", "")
                if inst and inst not in blocks:
                    blocks[inst] = {"subckt": b.get("subckt", "?"), "devices": []}
                if inst:
                    blocks[inst]["devices"].append(n.get("id", ""))
    if not blocks:
        return "  (no hierarchical blocks detected)"

    lines = []
    for inst, info in blocks.items():
        subckt = info.get("subckt", "?")
        devs = info.get("devices", [])
        lines.append(f"  {inst} ({subckt}): {', '.join(devs)}")
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Post-placement validation
# ---------------------------------------------------------------------------
def _validate_placement(original_nodes: list, result) -> list:
    """Run quick sanity checks on the returned placement. Returns list of errors."""
    errors = []
    orig_ids  = {n["id"] for n in original_nodes}
    orig_type = {n["id"]: n.get("type") for n in original_nodes}

    if isinstance(result, list):
        placed_nodes = result
    elif isinstance(result, dict):
        placed_nodes = result.get("nodes", [])
    else:
        errors.append(f"Unexpected placement result type: {type(result).__name__} — expected list or dict.")
        return errors

    if not placed_nodes:
        errors.append("Response has no 'nodes' array.")
        return errors

    placed_ids = {n.get("id") for n in placed_nodes if isinstance(n, dict) and n.get("id")}

    missing = orig_ids - placed_ids
    extra   = placed_ids - orig_ids
    if missing:
        errors.append(f"MISSING devices: {sorted(missing)}")
    if extra:
        errors.append(f"EXTRA (invented) devices: {sorted(extra)}")

    row_slots: dict = defaultdict(list)
    for n in placed_nodes:
        if not isinstance(n, dict):
            continue
        dev_id   = n.get("id", "?")
        dev_type = orig_type.get(dev_id, n.get("type", "?"))
        geo      = n.get("geometry", {})
        x        = geo.get("x")
        if x is None:
            errors.append(f"Device {dev_id} has no x-coordinate.")
            continue
        slot = round(float(x) / 0.294)
        row_slots[(dev_type, slot)].append(dev_id)

    for (dev_type, slot), ids in row_slots.items():
        if len(ids) > 1:
            errors.append(f"OVERLAP in {dev_type} row at x-slot {slot} (x~={slot*0.294:.4f}um): {ids}")

    for n in placed_nodes:
        if not isinstance(n, dict):
            continue
        dev_id   = n.get("id", "?")
        expected = orig_type.get(dev_id)
        actual   = n.get("type")
        if expected and actual and expected != actual:
            errors.append(f"Device {dev_id} changed type: was {expected}, now {actual}")

    return errors

# ---------------------------------------------------------------------------
# Coordinate normalisation
# ---------------------------------------------------------------------------
def _normalise_coords(nodes: list) -> tuple:
    """Shift all node Y-coordinates so that min(y) == 0 across all devices."""
    if not nodes:
        return nodes, 0.0

    all_ys = [n.get("geometry", {}).get("y", 0.0) for n in nodes if "geometry" in n]
    if not all_ys:
        return nodes, 0.0

    min_y    = min(all_ys)
    y_offset = -min_y

    if abs(y_offset) < 1e-9:
        return nodes, 0.0

    normalised = copy.deepcopy(nodes)
    for n in normalised:
        geo = n.get("geometry", {})
        if "y" in geo:
            geo["y"] = round(geo["y"] + y_offset, 6)

    return normalised, y_offset

def _restore_coords(placed_nodes: list, y_offset: float) -> list:
    """Un-shift Y coordinates back to the original frame."""
    if abs(y_offset) < 1e-9:
        return placed_nodes
    restored = copy.deepcopy(placed_nodes)
    for n in restored:
        geo = n.get("geometry", {})
        if "y" in geo:
            geo["y"] = round(geo["y"] - y_offset, 6)
    return restored

# ---------------------------------------------------------------------------
# Abutment candidate formatter
# ---------------------------------------------------------------------------
def _format_abutment_candidates(candidates: list) -> str:
    """Format abutment candidate list into a human-readable prompt section."""
    if not candidates:
        return ""
    lines = []
    for c in candidates:
        flip_note = " (flip needed — use R0_FH orientation)" if c.get("needs_flip") else ""
        lines.append(
            f"  - {c['dev_a']}.{c['term_a']} MUST abut {c['dev_b']}.{c['term_b']}"
            f"  [shared net: '{c['shared_net']}', type: {c['type']}]{flip_note}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core Structured Prompt
# ---------------------------------------------------------------------------
def generate_vlsi_prompt(prompt_graph, inventory_str, adjacency_str, block_str,
                         abutment_str: str = "") -> str:
    """Returns the perfectly structured core VLSI formatting string for LLMs."""

    abut_section = ""
    if abutment_str and abutment_str.strip() not in ("", "None detected."):
        abut_section = f"""
8. Transistor Abutment (Diffusion Sharing — HIGHEST PRIORITY):
The following transistor pairs MUST be placed directly adjacent (touching, no gap)
so their diffusion regions can be shared (leftAbut / rightAbut = 1).
This saves area and reduces parasitics. VIOLATION of these constraints is NOT acceptable.

ABUTMENT PAIRS:
{abutment_str}

Placement rules for abutment pairs:
- The two devices in each pair MUST be in the same row (same y-coordinate).
- They MUST be placed side-by-side with NO gap between them.
- If "flip needed" is noted: the second device must use orientation "R0_FH" (horizontal flip).
- Priority: abutment constraints override block grouping order when they conflict.
"""

    return f"""
You are an expert VLSI placement engineer.

Given this transistor-level graph:

{json.dumps(prompt_graph, indent=2)}

DEVICE INVENTORY:
{inventory_str}

NET ADJACENCY (Critical Routing Requirements):
{adjacency_str}

BLOCK GROUPING (Hierarchical Structure):
{block_str}

Generate an initial placement based on the following strict DRC and Floorplanning rules:

1. Device Types & Y-Axis Placement:
- Place NMOS devices exactly at y = 0.
- Place PMOS devices exactly at y = 0.668 (directly above NMOS row).

2. Fin Quantization & Grid:
- Placement coordinates must snap to a discrete Fin Grid.
- The Fin pitch is 0.014 um. Continuous (fractional) coordinate placement is strictly forbidden.

3. Spacing and Overlap Limits:
- Side-by-side overlap between any devices must not exceed 0.028 um.
- Vertical (up/down) overlap is strictly forbidden.
- Both devices in any pair must be aligned on the same boundary.

4. Voltage Domains & Isolation:
- Strictly isolate different voltage domains (0.8 V, 1.5 V, 1.8 V).
- Direct adjacency between 0.8 V and 1.8 V blocks is strictly forbidden.

5. Diffusion & Routing:
- Do not place blocks completely back-to-back.
- Reserve dedicated whitespace between blocks for diffusion breaks and dummy fill.
- Minimize net/wire crossings.

6. Block Grouping:
- Devices belonging to the same block MUST be placed adjacent to each other.
- Within each row (PMOS / NMOS / Passive), keep block members contiguous.
- Do not interleave devices from different blocks.

7. Passive Devices (Resistors & Capacitors):
- Devices with type="res" or type="cap" are PASSIVE COMPONENTS.
- Place ALL passive devices in a dedicated PASSIVE ROW at y = 1.630.
- NEVER place passives in the PMOS row (y=0.668) or NMOS row (y=0).
- Passives are placed left-to-right in the passive row with a minimum gap of 0.294 um between them.
- The passive row height is independent of transistor geometry.
{abut_section}
IMPORTANT:
You must return the EXACT same JSON structure as the input, keeping all existing keys and arrays intact.
Your only task is to add or update the "x", "y", and "orientation" (default "R0") keys inside every object within the "nodes" array.

Return ONLY raw JSON. Do not include explanations, markdown, or text outside the JSON object.
CRITICAL: Your response MUST be complete valid JSON. Do NOT truncate the output.
"""

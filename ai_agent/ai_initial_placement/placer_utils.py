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

    # Overlap check - more flexible for abutment
    rows = defaultdict(list)
    for n in placed_nodes:
        if not isinstance(n, dict): continue
        y = n.get("geometry", {}).get("y", 0)
        # Use 0.001 tolerance for row grouping
        rows[round(float(y), 3)].append(n)
    
    for y, row_nodes in rows.items():
        sorted_row = sorted(row_nodes, key=lambda n: n.get("geometry", {}).get("x", 0))
        for i in range(len(sorted_row) - 1):
            n1 = sorted_row[i]
            n2 = sorted_row[i+1]
            dx = n2.get("geometry", {}).get("x", 0) - n1.get("geometry", {}).get("x", 0)
            
            # Check if they are abutted
            abut1 = n1.get("abutment", {})
            abut2 = n2.get("abutment", {})
            is_abutted = abut1.get("abut_right") and abut2.get("abut_left")
            
            # print(f"DEBUG: Validating {n1['id']} and {n2['id']}, dx={dx:.4f}, abutted={is_abutted}")
            
            if is_abutted:
                # Target distance is 0.070 (5 fins). Allow some tolerance (0.015)
                if abs(dx - 0.070) > 0.015:
                    errors.append(f"Abutment spacing error between {n1['id']} and {n2['id']}: delta X is {dx:.4f}um, expected 0.070um.")
            else:
                # Non-abutted: must be at least 0.294 apart
                if dx < 0.290:
                    errors.append(f"Collision in row y={y} between {n1['id']} and {n2['id']}: delta X is {dx:.4f}um (too close for non-abutted devices).")

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
        flip_note = " (Note: set orientation='R0_FH' for device B)" if c.get("needs_flip") else ""
        lines.append(
            f"  - ABUTMENT CHAIN: {c['dev_a']} (Right Side) <---> (Left Side) {c['dev_b']}. Net: '{c['shared_net']}'.{flip_note}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Post-placement Healing
# ---------------------------------------------------------------------------
def _heal_abutment_positions(nodes: list, candidates: list) -> list:
    """
    Robust Algorithmic correction: 
    1. Ensures correct abutment flags.
    2. Sorts devices in each row.
    3. Forces exact 0.070um spacing for abutted neighbors and 0.294um for others.
    """
    if not nodes:
        return nodes

    node_map = {n["id"]: n for n in nodes if "id" in n}
    
    # 1. Map candidates to a set of pairs for quick lookup
    abut_pairs = set()
    for c in candidates:
        abut_pairs.add(tuple(sorted([c["dev_a"], c["dev_b"]])))

    # 2. Group nodes into rows (with tolerance)
    rows = defaultdict(list)
    for n in nodes:
        y = n.get("geometry", {}).get("y", 0.0)
        # Group by y rounded to 3 decimals to handle floats
        rows[round(float(y), 3)].append(n)
        
    for y_key, row_nodes in rows.items():
        # Sort row by X to establish relative order
        sorted_nodes = sorted(row_nodes, key=lambda n: n.get("geometry", {}).get("x", 0.0))
        
        if not sorted_nodes:
            continue
            
        # Start the row placement
        current_x = sorted_nodes[0].get("geometry", {}).get("x", 0.0)
        
        for i in range(len(sorted_nodes) - 1):
            n1 = sorted_nodes[i]
            n2 = sorted_nodes[i+1]
            
            pair = tuple(sorted([n1["id"], n2["id"]]))
            
            abut1 = n1.get("abutment", {})
            abut2 = n2.get("abutment", {})
            is_explicitly_abutted = abut1.get("abut_right") and abut2.get("abut_left")
            
            if pair in abut_pairs or is_explicitly_abutted:
                # FORCE ABUTMENT
                n1.setdefault("abutment", {})["abut_right"] = True
                n2.setdefault("abutment", {})["abut_left"] = True
                new_x = round(current_x + 0.070, 4)
            else:
                # FORCE SPACING (min 0.294)
                # If they were too close (e.g. both at 0), spread them out
                suggested_x = n2.get("geometry", {}).get("x", 0.0)
                new_x = round(max(suggested_x, current_x + 0.294), 4)
            
            n2["geometry"]["x"] = new_x
            current_x = new_x
                
    return nodes

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
The following transistors share a Source/Drain net and MUST be abutted to save area.

REQUIRED ABUTMENT CHAINS:
{abutment_str}

ABUTMENT MATHEMATICAL RULES:
- If Device A abuts Device B on the right: Origin X_b = X_a + 0.070 um.
- Example: MM28 at x=0.000, then MM5 MUST be at x=0.070, then MM4 at x=0.140.
- For EVERY abutted pair (A, B): 
    * Device A MUST have {{"abutment": {{"abut_right": true, "abut_left": false}}}}
    * Device B MUST have {{"abutment": {{"abut_left": true, "abut_right": false}}}}
- NEVER place two different devices at the exact same X and Y coordinates (delta X must be > 0).

OUTPUT EXAMPLE FOR ABUTTED PAIR:
{{
  "id": "DEV_A",
  "geometry": {{ "x": 0.000, "y": 0.000, "orientation": "R0" }},
  "abutment": {{ "abut_left": false, "abut_right": true }}
}},
{{
  "id": "DEV_B",
  "geometry": {{ "x": 0.070, "y": 0.000, "orientation": "R0" }},
  "abutment": {{ "abut_left": true, "abut_right": false }}
}}
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
- Standard non-abutted spacing between any devices should be at least 0.294 um.
- VERTICAL overlap is strictly forbidden.
- Both devices in any pair must be aligned on the same boundary.

4. Voltage Domains & Isolation:
- Strictly isolate different voltage domains (0.8 V, 1.5 V, 1.8 V).
- Direct adjacency between 0.8 V and 1.8 V blocks is strictly forbidden.

5. Diffusion & Routing:
- For devices sharing a net (abutment pairs), use exactly 0.074 um spacing between origins.
- For all other devices, reserve dedicated whitespace for diffusion breaks.
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
You must return ONLY a JSON object containing a SINGLE key "nodes" which holds the updated array of devices.
DO NOT return the "edges", "blocks", or "terminal_nets" arrays.
Your task is to:
1. Update "x", "y", and "orientation" (default "R0") keys inside every object within the "nodes" array.
2. For abutment pairs, add an "abutment" key with {{"abut_left": true/false, "abut_right": true/false}} flags as specified in the rules.

Return ONLY raw JSON. Do not include explanations, markdown, or text outside the JSON object.
CRITICAL: Your response MUST be complete valid JSON. Do NOT truncate the output.
"""

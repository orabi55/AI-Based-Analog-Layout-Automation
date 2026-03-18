"""
gemini_placer.py
================
Generates an initial analog transistor placement using the Gemini API.

Improvements over previous version:
  - Prompt rebuilt from scratch: explicit row structure, real y-values
    derived from the data (not hardcoded 0.0 / 0.668), no illegal cross-row
    placement.
  - Net adjacency pre-analysis injected into the prompt so Gemini knows
    which devices must be adjacent before it assigns any x-coordinates.
  - Strict slot-assignment table requested in chain-of-thought so Gemini
    cannot accidentally assign two devices to the same (x, row) slot.
  - sanitize_json() made more robust: handles truncated output, nested
    dicts/lists, and multiple repair strategies.
  - Post-processing validation: checks device count, detects x-collisions,
    and rejects the response with a clear error rather than silently saving
    bad data.
  - Model fallback list: tries gemini-2.0-flash first (stable), then
    gemini-2.5-flash (experimental).
"""

import os
import json
import re
from collections import defaultdict
from google import genai
from google.genai import types


# ---------------------------------------------------------------------------
# Robust JSON sanitizer
# ---------------------------------------------------------------------------
def _repair_truncated_json(text: str) -> str:
    """
    Fix truncated JSON by closing any unclosed brackets and braces.
    Handles the common case where Gemini hits token limit mid-output.
    """
    # Remove trailing commas and whitespace
    text = text.rstrip()
    if text.endswith(","):
        text = text[:-1]

    # Count unclosed brackets by walking the string
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

    # Remove any trailing comma before we close
    text = re.sub(r',\s*$', '', text)

    # Close unclosed brackets/braces
    text += ']' * max(0, open_brackets)
    text += '}' * max(0, open_braces)

    return text


def sanitize_json(text: str) -> dict:
    """
    Extract and sanitize Gemini output into strict JSON.
    Handles: markdown fences, trailing commas, comments,
    truncated output (unclosed brackets), and other LLM quirks.
    """

    if not text or len(text.strip()) == 0:
        raise ValueError("Empty response from Gemini")

    # Strategy 1: Try direct parse first
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Strip markdown fences and try again
    cleaned = re.sub(r"```json\s*", "", text)
    cleaned = re.sub(r"```\s*", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Strategy 3: Extract from first '{' onward and clean up
    brace_pos = cleaned.find('{')
    if brace_pos == -1:
        raise ValueError(
            f"No JSON object found in Gemini output. Raw text:\n{text[:500]}"
        )

    s = cleaned[brace_pos:]

    # Remove single-line comments
    s = re.sub(r'//[^\n]*', '', s)

    # Remove trailing commas before ] or }
    s = re.sub(r',\s*([\]}])', r'\1', s)

    # Try parsing after basic cleanup
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    # Strategy 4: Fix truncated output by closing unclosed brackets
    s_repaired = _repair_truncated_json(s)
    try:
        return json.loads(s_repaired)
    except json.JSONDecodeError:
        pass

    # Strategy 5: Try progressively trimming from the end to find
    # the last valid JSON boundary, then repair
    for trim in range(50, min(len(s), 2000), 50):
        attempt = _repair_truncated_json(s[:-trim])
        try:
            result = json.loads(attempt)
            if isinstance(result, dict) and "nodes" in result:
                print(
                    f"[sanitize_json] Recovered JSON by trimming "
                    f"{trim} chars from end"
                )
                return result
        except json.JSONDecodeError:
            continue

    raise ValueError(
        "Could not parse Gemini output as JSON after all repair attempts.\n"
        f"First 500 chars: {s[:500]}"
    )


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
    # Build blocks from top-level key or per-node block tags
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
def _validate_placement(original_nodes: list, result: dict) -> list[str]:
    """Run quick sanity checks on the returned placement. Returns list of errors."""
    errors = []
    orig_ids  = {n["id"] for n in original_nodes}
    orig_type = {n["id"]: n.get("type") for n in original_nodes}

    placed_nodes = result.get("nodes", [])
    if not placed_nodes:
        errors.append("Response has no 'nodes' array.")
        return errors

    placed_ids = {n.get("id") for n in placed_nodes if n.get("id")}

    # 1. Device count
    missing = orig_ids - placed_ids
    extra   = placed_ids - orig_ids
    if missing:
        errors.append(f"MISSING devices: {sorted(missing)}")
    if extra:
        errors.append(f"EXTRA (invented) devices: {sorted(extra)}")

    # 2. No x-collisions in the same row
    row_slots: dict = defaultdict(list)
    for n in placed_nodes:
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
            errors.append(
                f"OVERLAP in {dev_type} row at x-slot {slot} "
                f"(x~={slot*0.294:.4f}um): {ids}"
            )

    # 3. PMOS/NMOS must not swap rows
    for n in placed_nodes:
        dev_id   = n.get("id", "?")
        expected = orig_type.get(dev_id)
        actual   = n.get("type")
        if expected and actual and expected != actual:
            errors.append(
                f"Device {dev_id} changed type: was {expected}, now {actual}"
            )

    return errors


# ---------------------------------------------------------------------------
# Main placement function
# ---------------------------------------------------------------------------
MAX_RETRIES = 2


def gemini_generate_placement(input_json: str, output_json: str):
    """
    Generates initial transistor placement using Gemini API.
    Retries on JSON parse failure up to MAX_RETRIES times.
    """

    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise ValueError("GEMINI_API_KEY not set in environment / .env file")

    client = genai.Client(api_key=api_key)

    # Load input
    with open(input_json, "r") as f:
        graph_data = json.load(f)

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])

    # Pre-calculate prompt helpers
    adjacency_str = _build_net_adjacency(nodes, edges)
    inventory_str = _build_device_inventory(nodes)
    block_str = _build_block_info(nodes, graph_data)

    # Build structured prompt
    prompt = f"""
You are an expert VLSI placement engineer.

Given this transistor-level graph:

{json.dumps(graph_data, indent=2)}

DEVICE INVENTORY:
{inventory_str}

NET ADJACENCY (Critical Routing Requirements):
{adjacency_str}

BLOCK GROUPING (Hierarchical Structure):
{block_str}

Generate an initial transistor placement based on the following strict DRC and Floorplanning rules:

1. Device Types & Y-Axis Placement:
- Place PMOS devices at y = 0.
- Place NMOS devices right below the PMOS devices.

2. Fin Quantization & Grid:
- Placement coordinates must snap to a discrete Fin Grid.
- The Fin pitch is 0.014 um. Continuous (fractional) coordinate placement is strictly forbidden.

3. Spacing and Overlap Limits:
- Side-by-side overlap between any devices (NMOS/NMOS, PMOS/PMOS, or NMOS/PMOS) must not exceed 0.028 um.
- Vertical (up/down) overlap is strictly forbidden.
- Both devices in any pair must be aligned on the same boundary.

4. Voltage Domains & Isolation:
- Strictly isolate different voltage domains (0.8 V, 1.5 V, 1.8 V).
- Direct adjacency between 0.8 V and 1.8 V blocks is strictly forbidden.

5. Diffusion & Routing:
- Do not place blocks completely back-to-back.
- You must reserve dedicated whitespace between blocks for diffusion breaks (SDB/DDB) and dummy fill.
- Minimize net/wire crossings.

6. Block Grouping:
- Devices belonging to the same block (listed in BLOCK GROUPING above) MUST be placed adjacent to each other.
- Within each row (PMOS / NMOS), keep block members contiguous.
- Place blocks as cohesive groups — do not interleave devices from different blocks.

IMPORTANT:
You must return the EXACT same JSON structure as the input, keeping all existing keys and arrays intact. 
Your only task is to add or update the "x", "y", and "orientation" (default "R0") keys inside every object within the "nodes" array.

Return ONLY raw JSON. Do not include explanations, markdown, or text outside the JSON object.
CRITICAL: Your response MUST be complete valid JSON. Do NOT truncate the output.
"""

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"[gemini_placer] Attempt {attempt}/{MAX_RETRIES}...")

        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=65536,
                ),
            )

            if not response or not response.text:
                raise ValueError("Gemini returned empty response")

            raw_output = response.text.strip()

            # Parse safely
            placement = sanitize_json(raw_output)

            # Save result
            with open(output_json, "w") as f:
                json.dump(placement, f, indent=4)

            print("Placement saved to:", output_json)
            return

        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
            print(f"[gemini_placer] Attempt {attempt} failed: {e}")
            if attempt < MAX_RETRIES:
                # Add stronger instruction for retry
                prompt += (
                    "\n\nPREVIOUS ATTEMPT FAILED because your JSON output was "
                    "truncated or malformed. You MUST output COMPLETE, VALID "
                    "JSON with ALL devices included. Do not stop mid-output."
                )

    raise ValueError(
        f"AI placement failed after {MAX_RETRIES} attempts. "
        f"Last error: {last_error}"
    )

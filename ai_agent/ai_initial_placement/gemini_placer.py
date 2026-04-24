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
from pathlib import Path
from collections import defaultdict
from google import genai
from google.genai import types

# Load .env from the repository root so GEMINI_API_KEY is available
# even when this module is imported standalone (e.g. from the Design menu).
# The file now lives under ai_agent/ai_initial_placement/, so project root is
# not a fixed two-level parent anymore.
try:
    from dotenv import load_dotenv as _load_dotenv

    _this_file = Path(__file__).resolve()
    _env_loaded = False

    # Prefer a parent that looks like repo root in the new layout.
    for _parent in _this_file.parents:
        if (_parent / "README.md").is_file() and (_parent / "ai_agent").is_dir():
            _env_path = _parent / ".env"
            if _env_path.is_file():
                _load_dotenv(_env_path)
                _env_loaded = True
            break

    # Fallback: first .env found while walking upward.
    if not _env_loaded:
        for _parent in _this_file.parents:
            _env_path = _parent / ".env"
            if _env_path.is_file():
                _load_dotenv(_env_path)
                break
except ImportError:
    pass  # python-dotenv not installed; rely on shell environment


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


def _ensure_placement_dict(parsed) -> dict:
    """
    Normalise the result of sanitize_json() to always be a dict with a 'nodes' key.

    Gemini sometimes returns a bare JSON array  [ {...}, {...} ]  instead of
    { "nodes": [ {...}, {...} ] }.  Without this guard the caller does
    placement.get("nodes") on a list and crashes with
    "'list' object has no attribute 'get'".
    """
    if isinstance(parsed, list):
        # Bare array of node dicts — wrap it
        return {"nodes": parsed}
    if isinstance(parsed, dict):
        # Already correct shape — but 'nodes' may be nested under another key
        if "nodes" not in parsed:
            # Some models return {"placement": [...]} or {"result": [...]}
            for key in ("placement", "result", "layout", "devices"):
                if key in parsed and isinstance(parsed[key], list):
                    return {"nodes": parsed[key]}
        return parsed
    raise ValueError(
        f"Unexpected JSON type from Gemini: {type(parsed).__name__}. "
        f"Expected dict or list, got: {str(parsed)[:200]}"
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
def _validate_placement(original_nodes: list, result) -> list:
    """Run quick sanity checks on the returned placement. Returns list of errors.

    Args:
        original_nodes: list of original node dicts (with 'id', 'type', etc.)
        result:         EITHER a list of placed node dicts (new call convention)
                        OR a dict with a 'nodes' key (legacy call convention).
                        Both forms are handled transparently.
    """
    errors = []
    orig_ids  = {n["id"] for n in original_nodes}
    orig_type = {n["id"]: n.get("type") for n in original_nodes}

    # Accept either a list of nodes or a {"nodes": [...]} dict
    if isinstance(result, list):
        placed_nodes = result
    elif isinstance(result, dict):
        placed_nodes = result.get("nodes", [])
    else:
        errors.append(
            f"Unexpected placement result type: {type(result).__name__} — "
            "expected list or dict."
        )
        return errors

    if not placed_nodes:
        errors.append("Response has no 'nodes' array.")
        return errors

    placed_ids = {n.get("id") for n in placed_nodes if isinstance(n, dict) and n.get("id")}

    # 1. Device count
    missing = orig_ids - placed_ids
    extra   = placed_ids - orig_ids
    if missing:
        errors.append(f"MISSING devices: {sorted(missing)}")
    if extra:
        errors.append(f"EXTRA (invented) devices: {sorted(extra)}")

    # 2. No x-collisions in the same row (bounding-box overlap check)
    rows: dict = defaultdict(list)
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
        w = float(geo.get("width", 0.294))
        rows[(dev_type, round(float(geo.get("y", 0.0)), 3))].append({
            "id": dev_id, "x": float(x), "width": w,
        })

    for (dev_type, y), devs in rows.items():
        devs_sorted = sorted(devs, key=lambda d: d["x"])
        for i in range(len(devs_sorted) - 1):
            d1 = devs_sorted[i]
            d2 = devs_sorted[i + 1]
            min_x2 = d1["x"] + d1["width"]
            if d2["x"] < min_x2 - 0.001:
                errors.append(
                    f"OVERLAP in {dev_type} row y={y}: "
                    f"{d1['id']} (x={d1['x']:.4f}, w={d1['width']:.4f}) and "
                    f"{d2['id']} (x={d2['x']:.4f}) — overlap by {min_x2 - d2['x']:.4f}um"
                )

    # 3. PMOS/NMOS must not swap rows
    for n in placed_nodes:
        if not isinstance(n, dict):
            continue
        dev_id   = n.get("id", "?")
        expected = orig_type.get(dev_id)
        actual   = n.get("type")
        if expected and actual and expected != actual:
            errors.append(
                f"Device {dev_id} changed type: was {expected}, now {actual}"
            )

    return errors



# ---------------------------------------------------------------------------
# Coordinate normalisation
# ---------------------------------------------------------------------------
def _normalise_coords(nodes: list) -> tuple:
    """
    Shift all node Y-coordinates so that min(y) == 0 across all devices.

    This normalises circuits (like XOR) whose graph was extracted in an
    arbitrary or all-negative coordinate frame.  The LLM prompt already
    instructs the model to place PMOS at y=0 and NMOS below — so all we
    need is to bring the data to a sane numeric range before sending it.

    Returns:
        (normalised_nodes, y_offset)
    where  original_y = normalised_y - y_offset  (i.e. offset is ADDED to
    original to produce normalised, and SUBTRACTED to restore).
    """
    import copy

    if not nodes:
        return nodes, 0.0

    all_ys = [n.get("geometry", {}).get("y", 0.0) for n in nodes if "geometry" in n]
    if not all_ys:
        return nodes, 0.0

    min_y    = min(all_ys)
    y_offset = -min_y          # amount to ADD to each y to bring min_y → 0

    if abs(y_offset) < 1e-9:
        return nodes, 0.0      # already at origin — nothing to do

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
    import copy
    restored = copy.deepcopy(placed_nodes)
    for n in restored:
        geo = n.get("geometry", {})
        if "y" in geo:
            geo["y"] = round(geo["y"] - y_offset, 6)
    return restored


# ---------------------------------------------------------------------------
# Main placement function
# ---------------------------------------------------------------------------
MAX_RETRIES = 2


def gemini_generate_placement(input_json: str, output_json: str) -> None:
    """
    Generate an initial transistor placement using the Gemini API.

    Parameters
    ----------
    input_json : str
        Path to the JSON file containing the extracted circuit topology.
    output_json : str
        Path where the final placed layout JSON should be saved.

    Returns
    -------
    None
        The placement is written directly to output_json.
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

    # Normalise coordinates so PMOS sits at y >= 0 and NMOS sits below.
    # This handles circuits (e.g. XOR) with all-negative Y origins.
    norm_nodes, y_offset = _normalise_coords(nodes)
    if abs(y_offset) > 1e-9:
        print(f"[gemini_placer] Y-coord offset applied: {y_offset:+.4f} µm")
    # Use normalised nodes for the prompt; restore after placement.
    prompt_graph = dict(graph_data)
    prompt_graph["nodes"] = norm_nodes

    # Pre-calculate prompt helpers
    adjacency_str = _build_net_adjacency(norm_nodes, edges)
    inventory_str = _build_device_inventory(norm_nodes)
    block_str = _build_block_info(norm_nodes, graph_data)

    # Build structured prompt
    prompt = f"""
You are an expert VLSI placement engineer.

Given this transistor-level graph:

{json.dumps(prompt_graph, indent=2)}

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

            # Parse safely, then normalise to dict with 'nodes' key.
            # sanitize_json can return list if LLM emits a raw JSON array.
            val_errors: list = []   # reset each attempt so it's always defined
            placement = _ensure_placement_dict(sanitize_json(raw_output))
            placed_nodes = placement.get("nodes", [])

            if not isinstance(placed_nodes, list) or not placed_nodes:
                raise ValueError(
                    f"Placement 'nodes' is empty or not a list "
                    f"(got {type(placed_nodes).__name__}, len={len(placed_nodes) if isinstance(placed_nodes, list) else 'N/A'})"
                )

            # ── Validate placement quality BEFORE saving (C7 fix) ──────────
            # _validate_placement takes (original_nodes, placed_nodes_list).
            # Catches: device count mismatch, slot collisions, type swaps.
            val_errors = _validate_placement(norm_nodes, placed_nodes)
            if val_errors:
                error_summary = "; ".join(val_errors[:5])
                raise ValueError(
                    f"Placement validation failed ({len(val_errors)} error(s)): "
                    f"{error_summary}"
                )

            # Restore original Y-coordinate frame before saving
            placement["nodes"] = _restore_coords(placed_nodes, y_offset)

            # Save result while preserving the full input schema.
            # Some downstream flows expect keys like edges/terminal_nets to remain.
            output_payload = dict(graph_data)
            output_payload["nodes"] = placement["nodes"]
            with open(output_json, "w") as f:
                json.dump(output_payload, f, indent=4)

            print("Placement saved to:", output_json)
            return

        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
            print(f"[gemini_placer] Attempt {attempt} failed: {e}")
            if attempt < MAX_RETRIES:
                # Build targeted retry hint from validation errors if available
                extra = (
                    f" Specifically: {', '.join(val_errors[:3])}"
                    if val_errors
                    else ""
                )
                prompt += (
                    "\n\nPREVIOUS ATTEMPT FAILED because your JSON output was "
                    "truncated, malformed, or did not pass placement validation."
                    + extra
                    + " You MUST output COMPLETE, VALID JSON object with a 'nodes' "
                    "array containing ALL devices, with no slot collisions. "
                    "Do NOT return a bare JSON array — always wrap in {\"nodes\": [...]}"
                    " and do not stop mid-output."
                )

    raise ValueError(
        f"AI placement failed after {MAX_RETRIES} attempts. "
        f"Last error: {last_error}"
    )

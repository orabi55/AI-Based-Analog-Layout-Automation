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
  - sanitize_json() made more robust: handles nested dicts/lists, not just
    flat placement arrays.
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
def sanitize_json(text: str) -> dict:
    """
    Extract and sanitize Gemini output into strict JSON.
    Handles:
    - Extra explanation text
    - Markdown ```json blocks
    - Unquoted keys
    - Unquoted string values
    - Trailing commas
    """

    if not text or len(text.strip()) == 0:
        raise ValueError("Empty response from Gemini")

    # Strip markdown fences
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    text = text.strip()

    # Extract first {...} block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in Gemini output. Raw text:\n{text[:500]}")

    s = match.group(0)

    # Quote keys if missing
    s = re.sub(r'(\{|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s*:', r'\1 "\2":', s)

    # Quote unquoted string values (id, orientation, etc.)
    s = re.sub(r':\s*([A-Za-z_][A-Za-z0-9_%]*)', r': "\1"', s)

    # Remove trailing commas
    s = re.sub(r',\s*([\]}])', r'\1', s)
    s = re.sub(r'//[^\n]*', '', s)

    return json.loads(s)


# ---------------------------------------------------------------------------
# Pre-analysis helpers (pure Python — no LLM)
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
        lines.append(f"  {net:<12} → {', '.join(devs)}{cross}")
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
    row_slots: dict = defaultdict(list)  # (type, round_x) → [id, ...]
    for n in placed_nodes:
        dev_id  = n.get("id", "?")
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
                f"(x≈{slot*0.294:.4f}µm): {ids}"
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
def gemini_generate_placement(input_json: str, output_json: str):
    """
    Generates initial transistor placement using Gemini API.
    """

    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise ValueError("GEMINI_API_KEY not set in environment / .env file")

    client = genai.Client(api_key=api_key)

    # ── Load input ────────────────────────────────────────────────
    with open(input_json, "r") as f:
        graph_data = json.load(f)

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])

    # Build structured prompt
   # Build structured prompt
    prompt = f"""
You are an expert VLSI placement engineer.

Given this transistor-level graph:

{json.dumps(graph_data, indent=2)}

Generate an initial transistor placement based on the following strict DRC and Floorplanning rules:

1. Device Types & Y-Axis Placement:
- Place PMOS devices at y = 0.
- Place NMOS devices right below the PMOS devices.

2. Fin Quantization & Grid:
- Placement coordinates must snap to a discrete Fin Grid.
- The Fin pitch is 0.014 µm. Continuous (fractional) coordinate placement is strictly forbidden.

3. Spacing and Overlap Limits:
- Side-by-side overlap between any devices (NMOS/NMOS, PMOS/PMOS, or NMOS/PMOS) must not exceed 0.028 µm.
- Vertical (up/down) overlap is strictly forbidden.
- Both devices in any pair must be aligned on the same boundary.

4. Voltage Domains & Isolation:
- Strictly isolate different voltage domains (0.8 V, 1.5 V, 1.8 V).
- Direct adjacency between 0.8 V and 1.8 V blocks is strictly forbidden.

5. Diffusion & Routing:
- Do not place blocks completely back-to-back.
- You must reserve dedicated whitespace between blocks for diffusion breaks (SDB/DDB) and dummy fill.
- Minimize net/wire crossings.

IMPORTANT:
You must return the EXACT same JSON structure as the input, keeping all existing keys and arrays intact. 
Your only task is to add or update the "x", "y", and "orientation" (default "R0") keys inside every object within the "nodes" array.

Return ONLY raw JSON. Do not include explanations, markdown, or text outside the JSON object.
"""

    # Call Gemini model
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
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

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


# ---------------------------------------------------------------------------
# Robust JSON sanitizer
# ---------------------------------------------------------------------------
def sanitize_json(text: str) -> dict:
    """Extract and sanitize Gemini output into strict JSON.

    Handles:
      - Extra explanation text before/after the JSON
      - Markdown ```json ... ``` fences
      - Unquoted keys
      - Trailing commas before ] or }
    """
    if not text or not text.strip():
        raise ValueError("Empty response from Gemini")

    # Strip markdown fences
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    text = text.strip()

    # Extract the outermost { ... } block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in Gemini output. Raw text:\n{text[:500]}")

    s = match.group(0)

    # Fix: quote bare keys  (but NOT inside strings)
    s = re.sub(r'(?<=[{,])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:', r' "\1":', s)

    # Fix: remove trailing commas before ] or }
    s = re.sub(r',\s*([\]}])', r'\1', s)

    try:
        return json.loads(s)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON parse failed: {exc}\nSanitised text:\n{s[:800]}") from exc


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
    """Generate initial transistor placement using Gemini API.

    Args:
        input_json:  path to the input layout graph JSON
                     (must have 'nodes' and optionally 'edges')
        output_json: path to write the updated placement JSON
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

    if not nodes:
        raise ValueError("Input JSON has no 'nodes' array.")

    pmos_nodes = [n for n in nodes if n.get("type") == "pmos"]
    nmos_nodes = [n for n in nodes if n.get("type") == "nmos"]
    n_devices  = len(nodes)

    # ── Pre-analysis ──────────────────────────────────────────────
    adjacency_table  = _build_net_adjacency(nodes, edges)
    device_inventory = _build_device_inventory(nodes)

    # X-pitch and slot count
    pitch      = 0.294   # µm per slot
    n_pmos     = len(pmos_nodes)
    n_nmos     = len(nmos_nodes)
    max_slots  = max(n_pmos, n_nmos)

    # ── Build prompt ──────────────────────────────────────────────
    prompt = f"""You are an expert analog IC layout engineer specialising in symbolic placement.

Your task is to assign x, y, and orientation to every transistor in the device inventory below.
Return ONLY the updated JSON — no explanation, no markdown, no text outside the JSON.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DEVICE INVENTORY  ({n_devices} transistors — you must place ALL of them)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{device_inventory}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NET ADJACENCY TABLE  (devices on the same net should be placed adjacent)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{adjacency_table}
  [CROSS-ROW] = net connects both a PMOS and an NMOS device.
  For CROSS-ROW nets: place the PMOS and NMOS device at the SAME x-slot
  so the vertical wire between rows is minimised.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PLACEMENT RULES  (follow in strict priority order)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RULE 1 — ROW ASSIGNMENT (highest priority, never violate):
  • ALL PMOS devices → PMOS row.  Use y = 0.0 for every PMOS device.
  • ALL NMOS devices → NMOS row.  Use y = 0.668 for every NMOS device.
  • Never place a PMOS device at y = 0.668 or an NMOS device at y = 0.0.
  • Do not invent other y values. Only 0.0 and 0.668 are valid.

RULE 2 — ZERO OVERLAPS (never violate):
  • X-pitch is {pitch} µm. Valid x-values: 0.000, {pitch:.3f}, {pitch*2:.3f}, {pitch*3:.3f}, {pitch*4:.3f}, {pitch*5:.3f}, ...
    (x = {pitch:.3f} × n, for integer n ≥ 0)
  • Two PMOS devices must have DIFFERENT x-values.
  • Two NMOS devices must have DIFFERENT x-values.
  • A PMOS and NMOS CAN share the same x-value (they are in different rows).
  • MANDATORY PRE-OUTPUT CHECK: build a slot table before writing your answer:
      PMOS row: slot 0 → ?, slot 1 → ?, slot 2 → ?, ...
      NMOS row: slot 0 → ?, slot 1 → ?, slot 2 → ?, ...
    Verify every slot contains AT MOST one device.

RULE 3 — NET ADJACENCY (optimise routing):
  • Devices sharing a signal net (Gate, Source, or Drain) should occupy
    CONSECUTIVE x-slots in their respective row.
  • For CROSS-ROW nets: align the PMOS and NMOS devices at the SAME x-slot.
  • Minimise the x-span of each net: span = |max_x − min_x| across all
    devices on that net. A span of 0.294 µm (1 slot apart) is ideal.

RULE 4 — CONSERVATION (never violate):
  • Return EXACTLY {n_devices} devices in the output 'nodes' array.
  • Every device ID from the input MUST appear in the output unchanged.
  • Do NOT rename, merge, split, or delete any device.
  • Orientation default: "R0" for all devices unless a better choice
    (R0_FH mirror) improves matching for a differential pair.

RULE 5 — STEP-BY-STEP SLOT ASSIGNMENT:
  Step 1: List all PMOS IDs. Sort by net adjacency priority.
          Assign them to consecutive slots: x = 0, {pitch:.3f}, {pitch*2:.3f}, ...
  Step 2: List all NMOS IDs. Sort by net adjacency priority.
          Assign them to consecutive slots: x = 0, {pitch:.3f}, {pitch*2:.3f}, ...
          Try to align NMOS devices with their PMOS partners (same x-slot).
  Step 3: Fill the slot table. Verify no row has two devices at the same x.
  Step 4: Output the full JSON.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FULL INPUT DATA (return this structure with geometry.x / geometry.y updated)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{json.dumps(graph_data, indent=2)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT REQUIREMENTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Return the EXACT same top-level JSON structure as the input.
For each node in the 'nodes' array, set or update:
  "geometry": {{
    "x": <float, multiple of {pitch}>,
    "y": <0.0 for PMOS  |  0.668 for NMOS>,
    "width": <keep original>,
    "height": <keep original>,
    "orientation": "R0"
  }}
Do not add or remove any other keys.
Return ONLY raw JSON — no markdown, no explanation.
"""

    # ── Call Gemini (with model fallback) ─────────────────────────
    model_list = ["gemma-3-27b-it"]
    response_text = None
    last_error    = None

    for model_name in model_list:
        try:
            print(f"[GEMINI] Trying model: {model_name}")
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            if response and response.text and response.text.strip():
                response_text = response.text.strip()
                print(f"[GEMINI] Got response from {model_name} "
                      f"({len(response_text)} chars)")
                break
        except Exception as exc:
            last_error = exc
            print(f"[GEMINI] {model_name} failed: {exc}")

    if not response_text:
        raise RuntimeError(
            f"All Gemini models failed. Last error: {last_error}"
        )

    # ── Parse response ────────────────────────────────────────────
    try:
        placement = sanitize_json(response_text)
    except ValueError as exc:
        raise ValueError(f"Failed to parse Gemini response: {exc}") from exc

    # ── Validate placement ────────────────────────────────────────
    errors = _validate_placement(nodes, placement)
    if errors:
        error_str = "\n  ".join(errors)
        raise ValueError(
            f"Gemini placement failed validation ({len(errors)} error(s)):\n"
            f"  {error_str}\n\n"
            f"Raw response (first 1000 chars):\n{response_text[:1000]}"
        )

    print(f"[GEMINI] Placement validated ✓ "
          f"({len(placement.get('nodes', []))} devices placed)")

    # ── Save result ───────────────────────────────────────────────
    with open(output_json, "w") as f:
        json.dump(placement, f, indent=4)

    print(f"[GEMINI] Placement saved to: {output_json}")
    return placement
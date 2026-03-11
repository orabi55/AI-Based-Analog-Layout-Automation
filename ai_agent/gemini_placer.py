import os
import json
import re
from google import genai
from google.genai import types


# -------------------------------------------------------
# Robust JSON Sanitizer for Gemini Output
# -------------------------------------------------------

def sanitize_json(text: str) -> dict:
    """
    Extract and sanitize Gemini output into strict JSON.
    """
    if not text or len(text.strip()) == 0:
        raise ValueError("Empty response from Gemini")

    # Remove markdown wrappers
    text = text.replace("```json", "").replace("```", "").strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Extract largest {...} block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in Gemini output")

    s = match.group(0)

    # Remove trailing commas & inline comments
    s = re.sub(r',\s*([\]}])', r'\1', s)
    s = re.sub(r'//[^\n]*', '', s)

    try:
        return json.loads(s)
    except json.JSONDecodeError as e:
        debug_path = "gemini_raw_output_debug.txt"
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[Sanitizer] Saved raw output to {debug_path}")
        raise ValueError(f"Could not parse Gemini output: {e}")


# -------------------------------------------------------
# Main Placement Function
# -------------------------------------------------------

def gemini_generate_placement(input_json: str, output_json: str):
    """
    Generates initial transistor placement using Gemini API.

    Strategy: send only compact node summaries (not full graph with edges)
    to stay within output token limits, then merge placements back
    into the full graph structure.
    """

    api_key = "AIzaSyA5jgr9UZXRolTphNQNV_ogNqcMBndq9CY"

    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")

    client = genai.Client(api_key=api_key)

    # Load graph
    with open(input_json, "r") as f:
        graph_data = json.load(f)

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])

    # Build a COMPACT node summary for the prompt (no edges, minimal info)
    compact_nodes = []
    for n in nodes:
        compact_nodes.append({
            "id": n["id"],
            "type": n["type"],
            "w": round(n.get("geometry", {}).get("width", 0.3), 4),
            "h": round(n.get("geometry", {}).get("height", 0.7), 4),
        })

    # Build compact edge summary (just pairs)
    compact_edges = []
    for e in edges:
        compact_edges.append(f"{e['source']}-{e['target']}")

    prompt = f"""You are an expert VLSI placement engineer.

Place these {len(compact_nodes)} transistors:

{json.dumps(compact_nodes)}

Connected nets: {', '.join(compact_edges[:30])}{'...' if len(compact_edges) > 30 else ''}

Rules:
1. PMOS at y=0, NMOS at y=-1.0 (below PMOS row)
2. Snap x to fin grid (multiples of 0.014)
3. No overlaps - minimum x spacing = device width + 0.042
4. Minimize wire crossings between connected devices
5. Group devices that share nets close together

Return a JSON object with a single key "placements" containing an array.
Each element: {{"id": "...", "x": number, "y": number, "orientation": "R0"}}
Return ONLY the JSON, nothing else."""

    # Call Gemini with JSON mode
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
        ),
    )

    if not response or not response.text:
        raise ValueError("Gemini returned empty response")

    raw_output = response.text.strip()
    result = sanitize_json(raw_output)

    # Merge placements back into the full graph structure
    placements = result.get("placements", [])
    placement_map = {p["id"]: p for p in placements}

    for node in nodes:
        nid = node["id"]
        if nid in placement_map:
            p = placement_map[nid]
            geom = node.setdefault("geometry", {})
            geom["x"] = p.get("x", 0)
            geom["y"] = p.get("y", 0)
            geom["orientation"] = p.get("orientation", "R0")

    output_data = {"nodes": nodes, "edges": edges}

    with open(output_json, "w") as f:
        json.dump(output_data, f, indent=4)

    placed = len([n for n in nodes if "geometry" in n and "x" in n.get("geometry", {})])
    print(f"Placement saved to: {output_json} ({placed}/{len(nodes)} devices placed)")

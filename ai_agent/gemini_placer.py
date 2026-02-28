import os
import json
import re
from google import genai


# -------------------------------------------------------
# Robust JSON Sanitizer for Gemini Output
# -------------------------------------------------------

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

    # Remove markdown wrappers
    text = text.replace("```json", "").replace("```", "").strip()

    # Extract first {...} block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in Gemini output")

    s = match.group(0)

    # Quote keys if missing
    s = re.sub(r'(\{|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s*:', r'\1 "\2":', s)

    # Quote unquoted string values (id, orientation, etc.)
    s = re.sub(r':\s*([A-Za-z_][A-Za-z0-9_%]*)', r': "\1"', s)

    # Remove trailing commas
    s = re.sub(r',\s*([\]}])', r'\1', s)

    return json.loads(s)


# -------------------------------------------------------
# Main Placement Function
# -------------------------------------------------------

def gemini_generate_placement(input_json: str, output_json: str):
    """
    Generates initial transistor placement using Gemini API.
    """

    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")

    client = genai.Client(api_key=api_key)

    # Load graph
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

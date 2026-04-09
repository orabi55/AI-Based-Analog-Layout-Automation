"""
gemini_placer.py
================
Generates an initial analog transistor placement using the Gemini API.
"""

import os
import json
from pathlib import Path
from google import genai
from google.genai import types

from ai_agent.ai_initial_placement.placer_utils import (
    sanitize_json, _ensure_placement_dict, _build_net_adjacency,
    _build_device_inventory, _build_block_info, _validate_placement,
    _normalise_coords, _restore_coords, generate_vlsi_prompt,
    _format_abutment_candidates
)

try:
    from dotenv import load_dotenv as _load_dotenv
    _env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    _load_dotenv(_env_path)
except ImportError:
    pass

MAX_RETRIES = 2

def gemini_generate_placement(input_json: str, output_json: str):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set in environment / .env file")

    client = genai.Client(api_key=api_key)

    with open(input_json, "r") as f:
        graph_data = json.load(f)

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])

    norm_nodes, y_offset = _normalise_coords(nodes)
    if abs(y_offset) > 1e-9:
        print(f"[gemini_placer] Y-coord offset applied: {y_offset:+.4f} µm")
        
    prompt_graph = dict(graph_data)
    prompt_graph["nodes"] = norm_nodes

    adjacency_str = _build_net_adjacency(norm_nodes, edges)
    inventory_str = _build_device_inventory(norm_nodes)
    block_str = _build_block_info(norm_nodes, graph_data)

    # Build abutment constraint string if candidates were provided
    abutment_str = _format_abutment_candidates(
        graph_data.get("abutment_candidates", [])
    )

    prompt = generate_vlsi_prompt(prompt_graph, inventory_str, adjacency_str,
                                  block_str, abutment_str=abutment_str)

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
            
            val_errors = []
            placement = _ensure_placement_dict(sanitize_json(raw_output))
            placed_nodes = placement.get("nodes", [])

            if not isinstance(placed_nodes, list) or not placed_nodes:
                raise ValueError("Placement 'nodes' is empty or not a list")

            val_errors = _validate_placement(norm_nodes, placed_nodes)
            if val_errors:
                raise ValueError(f"Placement validation failed: {'; '.join(val_errors[:5])}")

            placement["nodes"] = _restore_coords(placed_nodes, y_offset)

            with open(output_json, "w") as f:
                json.dump(placement, f, indent=4)

            print("Placement saved to:", output_json)
            return

        except Exception as e:
            last_error = e
            print(f"[gemini_placer] Attempt {attempt} failed: {e}")
            if attempt < MAX_RETRIES:
                prompt += f"\n\nPREVIOUS ATTEMPT FAILED. Error: {e}\nYou MUST output COMPLETE, VALID JSON object with a 'nodes' array."

    raise ValueError(f"AI placement failed after {MAX_RETRIES} attempts. Last error: {last_error}")

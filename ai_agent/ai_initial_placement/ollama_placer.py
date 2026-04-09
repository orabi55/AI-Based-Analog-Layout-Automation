"""
ollama_placer.py
================
Generates an initial analog transistor placement using the local Ollama API.
"""

import json
import requests

from ai_agent.ai_initial_placement.placer_utils import (
    sanitize_json, _ensure_placement_dict, _build_net_adjacency,
    _build_device_inventory, _build_block_info, _validate_placement,
    _normalise_coords, _restore_coords, generate_vlsi_prompt,
    _format_abutment_candidates
)

MAX_RETRIES = 2

def ollama_generate_placement(input_json: str, output_json: str, model="llama3.2"):
    with open(input_json, "r") as f:
        graph_data = json.load(f)

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])

    norm_nodes, y_offset = _normalise_coords(nodes)
    if abs(y_offset) > 1e-9:
        print(f"[ollama_placer] Y-coord offset applied: {y_offset:+.4f} µm")
        
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
        print(f"[ollama_placer] Attempt {attempt}/{MAX_RETRIES}...")

        try:
            response = requests.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json"
                }
            )
            response.raise_for_status()
            
            result = response.json()
            raw_text = result.get("response", "{}")

            val_errors = []
            placement = _ensure_placement_dict(sanitize_json(raw_text))
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
            print(f"[ollama_placer] Attempt {attempt} failed: {e}")
            if attempt < MAX_RETRIES:
                prompt += f"\n\nPREVIOUS ATTEMPT FAILED. Error: {e}\nYou MUST output COMPLETE, VALID JSON object with a 'nodes' array."

    raise ValueError(f"AI placement failed after {MAX_RETRIES} attempts. Last error: {last_error}")
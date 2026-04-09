"""
openai_placer.py
================
Generates an initial analog transistor placement using the OpenAI API.
"""

import os
import json
from openai import OpenAI

from ai_agent.ai_initial_placement.placer_utils import (
    sanitize_json, _ensure_placement_dict, _build_net_adjacency,
    _build_device_inventory, _build_block_info, _validate_placement,
    _normalise_coords, _restore_coords, generate_vlsi_prompt
)

MAX_RETRIES = 2

def llm_generate_placement(input_json: str, output_json: str):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not set")

    client = OpenAI(api_key=api_key)

    with open(input_json, "r") as f:
        graph_data = json.load(f)

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])

    norm_nodes, y_offset = _normalise_coords(nodes)
    if abs(y_offset) > 1e-9:
        print(f"[openai_placer] Y-coord offset applied: {y_offset:+.4f} µm")
        
    prompt_graph = dict(graph_data)
    prompt_graph["nodes"] = norm_nodes

    adjacency_str = _build_net_adjacency(norm_nodes, edges)
    inventory_str = _build_device_inventory(norm_nodes)
    block_str = _build_block_info(norm_nodes, graph_data)

    prompt = generate_vlsi_prompt(prompt_graph, inventory_str, adjacency_str, block_str)

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"[openai_placer] Attempt {attempt}/{MAX_RETRIES}...")

        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3
            )
            
            raw_output = response.choices[0].message.content.strip()

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
            print(f"[openai_placer] Attempt {attempt} failed: {e}")
            if attempt < MAX_RETRIES:
                prompt += f"\n\nPREVIOUS ATTEMPT FAILED. Error: {e}\nYou MUST output COMPLETE, VALID JSON object with a 'nodes' array."

    raise ValueError(f"AI placement failed after {MAX_RETRIES} attempts. Last error: {last_error}")
import json
import requests

from ai_agent.ai_initial_placement.placer_utils import (
    sanitize_json, _ensure_placement_dict, _build_net_adjacency,
    _build_device_inventory, _build_block_info, _validate_placement,
    _normalise_coords, _restore_coords, generate_vlsi_prompt,
    _format_abutment_candidates,
)


def ollama_generate_placement(input_json, output_json, model="llama3.2"):
    try:
        with open(input_json, "r") as f:
            graph_data = json.load(f)
    except FileNotFoundError:
        print(f"Error: Input file {input_json} not found.")
        return

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    norm_nodes, y_offset = _normalise_coords(nodes)

    prompt_graph = dict(graph_data)
    prompt_graph["nodes"] = norm_nodes
    prompt = generate_vlsi_prompt(
        prompt_graph,
        _build_device_inventory(norm_nodes),
        _build_net_adjacency(norm_nodes, edges),
        _build_block_info(norm_nodes, graph_data),
        abutment_str=_format_abutment_candidates(
            graph_data.get("abutment_candidates", [])
        ),
    )

    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
            },
            timeout=300,
        )
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        print("Error: Could not connect to Ollama. Is the server running locally on port 11434?")
        return

    raw_text = response.json().get("response", "{}")
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
    print(f"Success! Placement saved to: {output_json}")

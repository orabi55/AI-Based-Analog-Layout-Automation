import os
import json
from openai import OpenAI

from ai_agent.ai_initial_placement.placer_utils import (
    sanitize_json, _ensure_placement_dict, _build_net_adjacency,
    _build_device_inventory, _build_block_info, _validate_placement,
    _normalise_coords, _restore_coords, generate_vlsi_prompt,
    _format_abutment_candidates,
)


def llm_generate_placement(input_json, output_json):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not set")

    client = OpenAI(api_key=api_key)

    with open(input_json, "r") as f:
        graph_data = json.load(f)

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

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )

    raw_output = response.choices[0].message.content.strip()
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

    print("Placement saved.")

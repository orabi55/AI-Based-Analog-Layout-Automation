"""
claude_vertex_placer.py
========================
Generates an initial analog transistor placement using Anthropic Claude
models hosted on Google Cloud Vertex AI Model Garden.

Uses the **slot-based** prompting approach: the AI only decides the
left-to-right device ordering; exact coordinates are computed
deterministically by ``_convert_slots_to_geometry()``.

Authentication: Application Default Credentials (ADC).
"""

import os
import json

from anthropic import AnthropicVertex

from ai_agent.ai_initial_placement.placer_utils import (
    sanitize_json, _ensure_placement_dict, _build_net_adjacency,
    _build_device_inventory, _build_block_info, _validate_placement,
    _normalise_coords, _restore_coords, generate_vlsi_slot_prompt,
    _format_abutment_candidates, _force_abutment_spacing,
    _convert_slots_to_geometry,
)

MAX_RETRIES = 3


def claude_generate_placement(input_json: str, output_json: str) -> None:
    """
    Generate an initial transistor placement using Claude on Vertex AI.

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

    project_id = os.getenv("VERTEX_PROJECT_ID", "")
    location = os.getenv("VERTEX_LOCATION", "us-east5")

    if not project_id:
        raise ValueError(
            "VERTEX_PROJECT_ID not set. "
            "Please enter your Google Cloud project ID in the model dialog."
        )

    client = AnthropicVertex(
        region=location,
        project_id=project_id,
    )

    # Load input graph
    with open(input_json, "r") as f:
        graph_data = json.load(f)

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])

    # Normalise coordinates
    norm_nodes, y_offset = _normalise_coords(nodes)
    if abs(y_offset) > 1e-9:
        print(f"[claude_vertex] Y-coord offset applied: {y_offset:+.4f} µm")

    abutment_candidates = graph_data.get("abutment_candidates", [])
    abutment_str = _format_abutment_candidates(abutment_candidates)

    # Build slot-based prompt (AI outputs ordering, not coordinates)
    prompt = generate_vlsi_slot_prompt(
        norm_nodes, edges, graph_data, abutment_str=abutment_str,
    )

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"[claude_vertex] Attempt {attempt}/{MAX_RETRIES} (slot-based)...")

        try:
            message = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=65536,
                messages=[{"role": "user", "content": prompt}],
            )

            if not message or not message.content:
                raise ValueError("Vertex AI Claude returned empty response")

            raw_output = message.content[0].text.strip()

            # Parse JSON — use sanitize_json as Claude doesn't enforce schema
            try:
                slot_data = json.loads(raw_output)
            except json.JSONDecodeError:
                slot_data = sanitize_json(raw_output)

            nmos_order = slot_data.get("nmos_order", [])
            pmos_order = slot_data.get("pmos_order", [])

            if not nmos_order and not pmos_order:
                raise ValueError("Both nmos_order and pmos_order are empty")

            # Validate completeness
            expected_nmos = {n["id"] for n in norm_nodes if n.get("type") == "nmos"}
            expected_pmos = {n["id"] for n in norm_nodes if n.get("type") == "pmos"}
            got_nmos = set(nmos_order)
            got_pmos = set(pmos_order)

            missing_n = expected_nmos - got_nmos
            missing_p = expected_pmos - got_pmos
            if missing_n or missing_p:
                missing_all = sorted(missing_n | missing_p)
                raise ValueError(
                    f"AI omitted {len(missing_all)} device(s): {missing_all[:10]}"
                )

            # Convert slot ordering → exact geometry (deterministic math)
            placed_nodes = _convert_slots_to_geometry(
                slot_data, norm_nodes, abutment_candidates
            )

            # Force-fix any remaining spacing issues
            placed_nodes = _force_abutment_spacing(
                placed_nodes, abutment_candidates
            )

            # Validate
            val_errors = _validate_placement(norm_nodes, placed_nodes)
            if val_errors:
                raise ValueError(
                    f"Placement validation failed: {'; '.join(val_errors[:5])}"
                )

            # Restore original coordinate frame
            placed_nodes = _restore_coords(placed_nodes, y_offset)

            placement = {"nodes": placed_nodes}
            with open(output_json, "w") as f:
                json.dump(placement, f, indent=4)

            print(f"[claude_vertex] Placement saved ({len(placed_nodes)} devices)")
            return

        except Exception as e:
            last_error = e
            print(f"[claude_vertex] Attempt {attempt} failed: {e}")
            if attempt < MAX_RETRIES:
                prompt += (
                    f"\n\nPREVIOUS ATTEMPT FAILED. Error: {e}\n"
                    "You MUST include EVERY device ID exactly once in "
                    "nmos_order or pmos_order. No duplicates, no omissions."
                )

    raise ValueError(
        f"Vertex AI Claude placement failed after {MAX_RETRIES} attempts. "
        f"Last error: {last_error}"
    )

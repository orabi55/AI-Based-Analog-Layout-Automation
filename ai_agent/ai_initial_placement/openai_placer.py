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
    _normalise_coords, _restore_coords, generate_vlsi_prompt,
    _format_abutment_candidates, _heal_abutment_positions,
    _force_abutment_spacing
)

from ai_agent.ai_initial_placement.finger_grouper import (
    group_fingers, pre_assign_rows, detect_matching_groups,
    build_matching_section, detect_inter_group_abutment,
    _enrich_matching_info, merge_matched_groups, expand_groups,
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

    # --- Finger grouping + multi-row assignment + matching ---------------
    group_nodes, group_edges, finger_map = group_fingers(norm_nodes, edges)

    # Build terminal-net lookup for matching analysis
    terminal_nets = graph_data.get("terminal_nets", {})
    group_terminal_nets = {}
    for gn in group_nodes:
        gid = gn["id"]
        if gid in terminal_nets:
            group_terminal_nets[gid] = terminal_nets[gid]
        elif gid in finger_map and finger_map[gid]:
            first_finger_id = finger_map[gid][0].get("id", "")
            group_terminal_nets[gid] = terminal_nets.get(first_finger_id, {})

    matching_section = build_matching_section(
        group_nodes, group_edges, group_terminal_nets
    )
    matching_info = detect_matching_groups(group_nodes, group_edges)
    _enrich_matching_info(matching_info, group_terminal_nets, group_nodes)

    # --- Merge matched pairs into fixed interdigitated blocks -------------
    merged_nodes, merged_edges, merged_finger_map, merged_blocks = \
        merge_matched_groups(
            group_nodes, group_edges, finger_map,
            matching_info, group_terminal_nets, terminal_nets,
        )

    row_assigned_nodes, row_summary = pre_assign_rows(
        merged_nodes, matching_info=matching_info,
        group_terminal_nets=group_terminal_nets,
    )

    # --- Inter-group abutment detection ---
    inter_group_abut = detect_inter_group_abutment(
        group_nodes, finger_map, terminal_nets,
    )
    existing_abut = graph_data.get("abutment_candidates", [])
    all_abutment = existing_abut + inter_group_abut

    adjacency_str = _build_net_adjacency(norm_nodes, edges)
    inventory_str = _build_device_inventory(norm_nodes, row_summary=row_summary)
    block_str = _build_block_info(norm_nodes, graph_data)

    abutment_str = _format_abutment_candidates(all_abutment)

    prompt = generate_vlsi_prompt(prompt_graph, inventory_str, adjacency_str,
                                  block_str, abutment_str=abutment_str,
                                  row_summary=row_summary,
                                  matching_section=matching_section)

    no_abutment = graph_data.get("no_abutment", False)

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

            # Expand group-level to finger-level first
            expanded_nodes = expand_groups(
                placed_nodes, merged_finger_map,
                matching_info=matching_info,
                no_abutment=no_abutment,
            )

            # HEAL: fix spacing after expansion
            candidates = graph_data.get("abutment_candidates", [])
            expanded_nodes = _heal_abutment_positions(expanded_nodes, candidates,
                                                       no_abutment=no_abutment)
            
            # FAILSAFE: Force correct abutment spacing
            if not no_abutment:
                expanded_nodes = _force_abutment_spacing(expanded_nodes, candidates)

            val_errors = _validate_placement(norm_nodes, expanded_nodes)
            if val_errors:
                print(f"[openai_placer] Validation warnings: {val_errors[:3]}")

            placement["nodes"] = _restore_coords(expanded_nodes, y_offset)

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
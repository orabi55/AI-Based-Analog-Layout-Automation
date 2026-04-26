"""
JSON Utilities
==============
Provides robust JSON sanitization and graph compression utilities to optimize 
data transfer and processing for the AI placement engine.

Functions:
- _repair_truncated_json: Fixes structurally invalid JSON caused by truncation.
- sanitize_json: Extracts and repairs JSON content from raw LLM output.
  - Inputs: text (str)
  - Outputs: parsed JSON dictionary.
- compress_graph_for_prompt: Reduces token count of graph JSON by collapsing instances and removing redundant data.
  - Inputs: graph_data (dict)
  - Outputs: compressed dictionary.
"""

import json
import re

from ai_agent.utils.logging import vprint


def _repair_truncated_json(text: str) -> str:
    """
    Fix truncated JSON by closing any unclosed brackets and braces.
    Handles the common case where an LLM hits its token limit mid-output.

    Parameters
    ----------
    text : str
        The raw, potentially truncated JSON string.

    Returns
    -------
    str
        A string with all detected open brackets (`[`) and braces (`{`)
        properly closed at the end to make it structurally valid JSON.
    """
    text = text.rstrip()
    if text.endswith(","):
        text = text[:-1]

    open_braces = 0
    open_brackets = 0
    in_string = False
    escape = False

    for ch in text:
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            open_braces += 1
        elif ch == '}':
            open_braces -= 1
        elif ch == '[':
            open_brackets += 1
        elif ch == ']':
            open_brackets -= 1

    text = re.sub(r',\s*$', '', text)
    text += ']' * max(0, open_brackets)
    text += '}' * max(0, open_braces)
    return text


def sanitize_json(text: str) -> dict:
    """
    Extract and sanitize LLM output into a strict JSON dictionary.

    Handles edge cases common to LLM generation such as:
    - Markdown code fences (e.g., ```json ... ```)
    - Trailing commas before array/object closures
    - Inline JavaScript-style comments (// ...)
    - Truncated output (by invoking _repair_truncated_json)

    Parameters
    ----------
    text : str
        The raw text response direct from the LLM.

    Returns
    -------
    dict
        A fully parsed JSON dictionary representing the layout geometry.

    Raises
    ------
    ValueError
        If the text cannot be repaired or parsed into valid JSON.
    """
    if not text or len(text.strip()) == 0:
        raise ValueError("Empty response from LLM")

    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    cleaned = re.sub(r"```json\s*", "", text)
    cleaned = re.sub(r"```\s*", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    brace_pos = cleaned.find('{')
    if brace_pos == -1:
        raise ValueError(f"No JSON object found. Raw text:\n{text[:500]}")

    s = cleaned[brace_pos:]
    s = re.sub(r'//[^\n]*', '', s)
    s = re.sub(r',\s*([\]}])', r'\1', s)

    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    s_repaired = _repair_truncated_json(s)
    try:
        return json.loads(s_repaired)
    except json.JSONDecodeError:
        pass

    for trim in range(50, min(len(s), 2000), 50):
        attempt = _repair_truncated_json(s[:-trim])
        try:
            result = json.loads(attempt)
            if isinstance(result, dict) and "nodes" in result:
                vprint(f"[sanitize_json] Recovered JSON by trimming {trim} chars from end")
                return result
        except json.JSONDecodeError:
            continue

    raise ValueError(f"Could not parse LLM output as JSON. First 500 chars: {s[:500]}")


def compress_graph_for_prompt(graph_data: dict) -> dict:
    """
    Compress graph JSON for AI prompt to drastically reduce token count.

    Problems solved:
    - Collapses finger/multiplier instances into parent devices
    - Removes pre-computed geometry (AI's job to compute it)
    - Compresses terminal_nets (one per parent, not per finger)
    - Uses net-centric connectivity instead of verbose edge lists

    Expected reduction: 95-97% smaller (e.g., 7300 lines -> 150-200 lines).

    Parameters
    ----------
    graph_data : dict
        The full verbose JSON dictionary exported directly from the GUI.

    Returns
    -------
    dict
        A highly compressed dictionary containing exactly what the LLM needs:
        devices, signal nets, matching constraints, and blocks.
    """
    if not graph_data:
        return {}

    compressed = {
        "devices": {},
        "nets": {},
        "matching_constraints": graph_data.get("matching_constraints", {}),
        "blocks": graph_data.get("blocks", {})
    }

    # 1. Collapse finger/multiplier instances into parent devices
    terminal_nets = graph_data.get("terminal_nets", {})
    for node in graph_data.get("nodes", []):
        # Get parent device ID (strip _mN, _fN suffixes)
        node_id = node["id"]
        parent_id = node["electrical"].get("parent")

        # If no parent field, extract from node_id pattern
        if not parent_id:
            # Strip _mN or _fN suffixes to get base device
            parent_id = re.sub(r'_[mf]\d+$', '', node_id)

        # Skip if we already processed this parent
        if parent_id in compressed["devices"]:
            continue

        # Extract electrical parameters from this node
        electrical = node.get("electrical", {})
        dev_type = node.get("type", "nmos")

        # Get terminal nets from the first instance (all fingers share same nets)
        dev_terminal_nets = terminal_nets.get(node_id, {})

        # Build compressed device entry
        compressed["devices"][parent_id] = {
            "type": dev_type,
            "m": electrical.get("m", 1),
            "nf": electrical.get("nf", 1),
            "nfin": electrical.get("nfin", 1),
            "l": electrical.get("l", 0.0),
            "terminal_nets": dev_terminal_nets
        }

        # Add block membership if present
        block_info = node.get("block")
        if block_info:
            compressed["devices"][parent_id]["block"] = block_info

    # 2. Build net-centric connectivity (replace verbose edge list)
    edges = graph_data.get("edges", [])
    for edge in edges:
        net = edge.get("net", "")
        if not net:
            continue

        # Skip power nets (too verbose, not useful for placement)
        if net.upper() in {"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS"}:
            continue

        source = edge.get("source", "")
        target = edge.get("target", "")

        # Convert finger instances to parent devices
        source_parent = re.sub(r'_[mf]\d+$', '', source)
        target_parent = re.sub(r'_[mf]\d+$', '', target)

        if net not in compressed["nets"]:
            compressed["nets"][net] = set()

        compressed["nets"][net].add(source_parent)
        compressed["nets"][net].add(target_parent)

    # Convert sets to sorted lists for JSON serialization
    for net in compressed["nets"]:
        compressed["nets"][net] = sorted(compressed["nets"][net])

    return compressed

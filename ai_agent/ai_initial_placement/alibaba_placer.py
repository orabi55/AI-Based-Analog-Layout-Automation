"""
ai_agent/ai_initial_placement/alibaba_placer.py
================================================
Generates an initial analog transistor placement using the Alibaba
DashScope API (Qwen family of models).

Uses the same slot-based approach as every other placer:
  1. The LLM decides only the LEFT-TO-RIGHT ordering (nmos_order / pmos_order).
  2. Exact x/y coordinates are computed deterministically by
     ``_convert_slots_to_geometry()``.

Compatible with the OpenAI Python SDK via the DashScope-compatible endpoint:
  base_url = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

Authentication: environment variable ``ALIBABA_API_KEY``.
Recommended model: ``qwen-plus`` (fast, good reasoning, 32k context).
"""

import os
import json

from openai import OpenAI

from ai_agent.ai_initial_placement.placer_utils import (
    sanitize_json,
    _ensure_placement_dict,
    _validate_placement,
    _normalise_coords,
    _restore_coords,
    generate_vlsi_slot_prompt,
    _format_abutment_candidates,
    _force_abutment_spacing,
    _convert_slots_to_geometry,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_DASHSCOPE_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
_DEFAULT_MODEL = "qwen-plus"          # fast + strong reasoning
_FALLBACK_MODEL = "qwen3.6-flash"     # ultra-fast fallback
MAX_RETRIES = 3


def alibaba_generate_placement(input_json: str, output_json: str) -> None:
    """
    Generate an initial transistor placement using Alibaba's Qwen API.

    Parameters
    ----------
    input_json : str
        Path to the JSON file containing the extracted circuit topology.
    output_json : str
        Path where the final placed layout JSON should be saved.

    Returns
    -------
    None
        The placement is written directly to ``output_json``.

    Raises
    ------
    ValueError
        If ``ALIBABA_API_KEY`` is not set, or if all retry attempts fail.
    """
    api_key = os.getenv("ALIBABA_API_KEY", "")
    if not api_key:
        raise ValueError(
            "ALIBABA_API_KEY not set. "
            "Please enter your Alibaba DashScope API key in the model dialog."
        )

    client = OpenAI(api_key=api_key, base_url=_DASHSCOPE_BASE_URL)

    # ── Load input ──────────────────────────────────────────────────
    with open(input_json, "r", encoding="utf-8") as f:
        graph_data = json.load(f)

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    abutment_candidates = graph_data.get("abutment_candidates", [])

    # ── Normalise Y-coordinates ─────────────────────────────────────
    norm_nodes, y_offset = _normalise_coords(nodes)
    if abs(y_offset) > 1e-9:
        print(f"[alibaba_placer] Y-coord offset applied: {y_offset:+.4f} µm")

    abutment_str = _format_abutment_candidates(abutment_candidates)

    # ── Build slot-based prompt ────────────────────────────────────
    prompt = generate_vlsi_slot_prompt(
        norm_nodes, edges, graph_data, abutment_str=abutment_str
    )

    expected_nmos = {n["id"] for n in norm_nodes if n.get("type") == "nmos"}
    expected_pmos = {n["id"] for n in norm_nodes if n.get("type") == "pmos"}

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        model = _DEFAULT_MODEL if attempt < MAX_RETRIES else _FALLBACK_MODEL
        print(f"[alibaba_placer] Attempt {attempt}/{MAX_RETRIES} (model={model})...")

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=8192,
            )

            raw_output = (response.choices[0].message.content or "").strip()
            if not raw_output:
                raise ValueError("Alibaba Qwen returned an empty response.")

            # ── Parse slot ordering ────────────────────────────────
            try:
                slot_data = sanitize_json(raw_output)
            except Exception as exc:
                raise ValueError(f"JSON parse failed: {exc}") from exc

            nmos_order = slot_data.get("nmos_order", [])
            pmos_order = slot_data.get("pmos_order", [])

            # Fill in any missing devices so no node is lost
            for dev_id in sorted(expected_nmos):
                if dev_id not in nmos_order:
                    nmos_order.append(dev_id)
            for dev_id in sorted(expected_pmos):
                if dev_id not in pmos_order:
                    pmos_order.append(dev_id)

            if not nmos_order and not pmos_order:
                raise ValueError("Both nmos_order and pmos_order are empty.")

            # ── Convert ordering → geometry (deterministic) ────────
            placed_nodes = _convert_slots_to_geometry(
                {"nmos_order": nmos_order, "pmos_order": pmos_order},
                norm_nodes,
                abutment_candidates,
            )

            # Force-fix any residual spacing issues
            placed_nodes = _force_abutment_spacing(placed_nodes, abutment_candidates)

            # ── Validate ──────────────────────────────────────────
            val_errors = _validate_placement(norm_nodes, placed_nodes)
            if val_errors:
                raise ValueError(
                    f"Placement validation failed: {'; '.join(val_errors[:5])}"
                )

            # ── Restore original Y frame ───────────────────────────
            placed_nodes = _restore_coords(placed_nodes, y_offset)

            # ── Write output ───────────────────────────────────────
            output = dict(graph_data)
            output["nodes"] = placed_nodes
            with open(output_json, "w", encoding="utf-8") as f:
                json.dump(output, f, indent=4)

            print(f"[alibaba_placer] Placement saved to: {output_json} "
                  f"({len(placed_nodes)} devices)")
            return

        except Exception as exc:
            last_error = exc
            print(f"[alibaba_placer] Attempt {attempt} failed: {exc}")
            if attempt < MAX_RETRIES:
                prompt += (
                    f"\n\nPREVIOUS ATTEMPT FAILED. Error: {exc}\n"
                    "You MUST include EVERY device ID exactly once in "
                    "nmos_order or pmos_order. Return ONLY raw JSON."
                )

    raise ValueError(
        f"Alibaba Qwen placement failed after {MAX_RETRIES} attempts. "
        f"Last error: {last_error}"
    )

# -*- coding: utf-8 -*-
"""
schematic_ai.py
AI-assisted schematic layout using Google Cloud Vertex AI.

Uses google.genai (new unified SDK, already installed as google-genai==1.65.0).
Auth: Google Cloud Application Default Credentials (ADC) — no API key needed.
Model: gemini-2.5-flash on Vertex AI (confirmed working in this project).

Reads VERTEX_PROJECT_ID and VERTEX_LOCATION from environment (set by ai_model_dialog.py).
"""
from __future__ import annotations

import json
import logging
import os
import re
import textwrap
from typing import Any

log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
_DEFAULT_PROJECT  = "project-03484c74-0ab0-4f9e-b48"
_DEFAULT_LOCATION = "us-central1"
_DEFAULT_MODEL    = "gemini-2.5-flash"     # confirmed available in this project

# ── Lazy client ────────────────────────────────────────────────────────────────
_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    project  = os.getenv("VERTEX_PROJECT_ID",  _DEFAULT_PROJECT)
    location = os.getenv("VERTEX_LOCATION",     _DEFAULT_LOCATION)
    if location.lower() in ("global", ""):
        location = _DEFAULT_LOCATION
    try:
        import google.genai as genai          # type: ignore
        _client = genai.Client(
            vertexai=True,
            project=project,
            location=location,
        )
        log.info("Vertex AI client ready: project=%s location=%s model=%s",
                 project, location, _DEFAULT_MODEL)
        return _client
    except Exception as exc:
        log.warning("Vertex AI client init failed: %s", exc)
        return None


def _call_model(client, prompt: str, max_tokens: int = 2048) -> str | None:
    """Call Vertex AI and return the text response, or None on failure."""
    try:
        import google.genai as genai          # type: ignore
        from google.genai import types        # type: ignore

        model = os.getenv("VERTEX_MODEL_NAME", _DEFAULT_MODEL)
        resp = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=max_tokens,
                temperature=0.1,
                response_mime_type="application/json",
                # Disable thinking tokens — they produce empty parts
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        # resp.text is the canonical way; parts fallback for safety
        if hasattr(resp, "text") and resp.text:
            return resp.text
        cand = resp.candidates[0] if resp.candidates else None
        if cand and cand.content and cand.content.parts:
            return "".join(
                p.text for p in cand.content.parts
                if p.text and not getattr(p, "thought", False)
            )
        log.warning("Vertex AI returned empty response (finish=%s)",
                    cand.finish_reason if cand else "unknown")
        return None
    except Exception as exc:
        log.warning("Vertex AI generate_content failed: %s", exc)
        return None


# ── Prompt ─────────────────────────────────────────────────────────────────────
_SYSTEM = textwrap.dedent("""
You are an expert analog IC schematic layout engineer.
Given a SPICE netlist and topology groups, assign (grid_x, grid_y) coordinates to build a beautiful, professional schematic.

ABSOLUTE PLACEMENT RULES:
1. Signal flow is strictly Top-to-Bottom. PMOS MUST have grid_y < 0 (e.g. -2, -1). NMOS MUST have grid_y >= 0 (e.g. 0, 1, 2).
2. Vertical Stacking: Align devices vertically (same grid_x) if they share a major signal path. Place loads and latches directly above the differential pairs that drive them.
3. Differential Pairs: Place side-by-side, symmetric around x=0. Left: grid_x = -1.0, Right: grid_x = 1.0. Set mirrored=true for the right device.
4. Current Mirrors: Place side-by-side. Reference at grid_x = -1.0, Copy at grid_x = 1.0.
5. Cross-coupled latches: PMOS latches at grid_y=-1, NMOS latches at grid_y=0. Left: grid_x = -1.0 (mirrored=true), Right: grid_x = 1.0 (mirrored=false). This makes them face inward.
6. Tails / Bias devices: Center them at grid_x = 0, at the very bottom (below the diff pair).
7. Un-grouped devices ("single"): 
   - If S=VDD (PMOS), put them near the top (e.g. grid_y=-2). Cluster them above the branches they drive (e.g. x=-2, x=-1 for the left branch).
   - If S=GND (NMOS), put them near the bottom.
   - LEFT vs RIGHT RULE: If a device connects to 'VINP', 'VOUTN', or 'VX', it MUST be placed on the LEFT (grid_x < 0). If it connects to 'VINN', 'VOUTP', or 'VY', it MUST be placed on the RIGHT (grid_x > 0).
   - NEVER assign the exact same (grid_x, grid_y) to two devices. Space them horizontally if on the same side.

WIRE RULES:
1. "wire_nets": Array of net names to draw as solid Manhattan wires.
2. ONLY include INTERNAL local nets with 2-4 terminals (e.g., "net1", "net2", "VX", "VY").
3. NEVER include global/supply/port nets (VDD, GND, VSS, CLK, VIN, VOUT, VBIAS) in wire_nets. These use labels.

CROSS_WIRES:
If a cross-coupled latch is present, emit exactly two cross-wires to draw the X shape:
[{"x1": -1.2, "y1": 0, "x2": 1.2, "y2": 0}, {"x1": 1.2, "y1": 0, "x2": -1.2, "y2": 0}]. Otherwise [].

--- 1-SHOT EXAMPLE (5-Transistor OTA) ---
INPUT NETLIST:
  MM1 (PMOS) D=net1 G=net1 S=VDD
  MM2 (PMOS) D=VOUT G=net1 S=VDD
  MM3 (NMOS) D=net1 G=VINN S=net2
  MM4 (NMOS) D=VOUT G=VINP S=net2
  MM5 (NMOS) D=net2 G=VBIAS S=GND
GROUPS:
  current_mirror: ['MM1', 'MM2']
  diff_pair: ['MM3', 'MM4']
  tail: ['MM5']

OUTPUT JSON:
{
  "devices": [
    {"id": "MM1", "grid_x": -1.5, "grid_y": -2, "mirrored": false},
    {"id": "MM2", "grid_x": 1.5, "grid_y": -2, "mirrored": true},
    {"id": "MM3", "grid_x": -1.5, "grid_y": 0, "mirrored": false},
    {"id": "MM4", "grid_x": 1.5, "grid_y": 0, "mirrored": true},
    {"id": "MM5", "grid_x": 0, "grid_y": 2, "mirrored": false}
  ],
  "wire_nets": ["net1", "net2"],
  "cross_wires": []
}

Return ONLY valid compact JSON matching the schema above for the provided circuit. No explanation.
""").strip()


def _build_prompt(devs: list[dict], groups: list, terminal_nets: dict) -> str:
    lines = ["NETLIST:"]
    
    # Helper to check if a device is structurally 'right' based ONLY on Drain/Source, not Gate.
    def is_right(did: str) -> bool:
        for d in devs:
            if d["id"] == did:
                tn = d.get("terminal_nets", terminal_nets.get(d["id"], {}))
                for term in ("D", "S"):
                    net = tn.get(term, "").upper()
                    if net in {"VINN", "VOUTP", "VY", "OUTN", "INN"}:
                        return True
        return False

    for d in devs:
        tn = d.get("terminal_nets", terminal_nets.get(d["id"], {}))
        lines.append(f"  {d['id']} ({d.get('type','nmos').upper()}) D={tn.get('D','?')} G={tn.get('G','?')} S={tn.get('S','?')}")
    
    lines.append("\nGROUPS:")
    has_diff_pair = False
    latch_count = 0
    for g in groups:
        if g.kind == "diff_pair": has_diff_pair = True
        if g.kind == "cross_coupled_latch": latch_count += 1
        
        # Enforce exact Left/Right hints in the prompt based on Drain/Source
        m1, m2 = g.members[0], g.members[-1]
        if is_right(m1) and not is_right(m2):
            left_id, right_id = m2, m1
        else:
            left_id, right_id = m1, m2
            
        lines.append(f"  {g.kind}: [{left_id} (LEFT), {right_id} (RIGHT)]")
        
    if has_diff_pair and latch_count >= 1:
        lines.append("\n=== TOPOLOGY IDENTIFIED: Comparator / StrongARM ===")
        lines.append("CRITICAL VERTICAL AND HORIZONTAL STACKING REQUIREMENT:")
        lines.append("  grid_y = -2 : PMOS Precharge/Load devices (Cluster at grid_x = -1.5, -0.5 for left; 0.5, 1.5 for right)")
        lines.append("  grid_y = -1 : PMOS Cross-coupled latches (Left: grid_x=-1.0, Right: grid_x=1.0)")
        lines.append("  grid_y =  0 : NMOS Cross-coupled latches (Left: grid_x=-1.0, Right: grid_x=1.0)")
        lines.append("  grid_y =  1 : NMOS Differential Pair (Left: grid_x=-1.0, Right: grid_x=1.0)")
        lines.append("  grid_y =  2 : NMOS Tail device (Center: grid_x=0)")

    lines.append("\nReturn JSON only. Every device must be in 'devices'.")
    return _SYSTEM + "\n\n" + "\n".join(lines)


# ── Public API ─────────────────────────────────────────────────────────────────
def ai_layout(
    devs: list[dict],
    groups: list,
    terminal_nets: dict,
    cell_px: float = 160.0,
    timeout: float = 30.0,
) -> dict[str, Any] | None:
    """
    Call Vertex AI Gemini to produce professional schematic positions.
    Returns {"positions", "wire_nets", "cross_wires"} or None on failure.
    """
    client = _get_client()
    if client is None:
        return None

    prompt = _build_prompt(devs, groups, terminal_nets)
    # Allow generous tokens: ~80 tokens per device + overhead
    max_tokens = max(1024, len(devs) * 80 + 512)

    raw = _call_model(client, prompt, max_tokens=min(max_tokens, 4096))
    if raw is None:
        return None

    return _parse_response(raw, cell_px, groups)


def _parse_response(raw: str, cell_px: float, groups: list) -> dict[str, Any] | None:
    # Strip markdown fences
    raw = re.sub(r"^```[a-z]*\n?", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\n?```$",       "", raw, flags=re.MULTILINE)
    raw = raw.strip()

    # Handle truncated JSON gracefully
    if not raw.endswith("}"):
        # Try to find the last complete device entry
        last_brace = raw.rfind("}")
        if last_brace > 0:
            raw = raw[:last_brace + 1]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Try partial recovery: extract devices array manually
        m = re.search(r'"devices"\s*:\s*(\[.*?\])', raw, re.DOTALL)
        if not m:
            log.warning("Vertex AI: could not parse JSON response")
            return None
        try:
            data = {"devices": json.loads(m.group(1)), "wire_nets": [], "cross_wires": []}
        except Exception:
            log.warning("Vertex AI: partial JSON recovery also failed")
            return None

    positions: dict[str, dict] = {}
    for entry in data.get("devices", []):
        did = entry.get("id")
        if not did:
            continue
        gx = float(entry.get("grid_x", 0))
        gy = float(entry.get("grid_y", 0))
        mirrored = bool(entry.get("mirrored", False))
        
        # Enforce strict topology mirroring to fix AI hallucinations
        for g in groups:
            if g.kind in ("cross_coupled_latch", "diff_pair") and did in g.members:
                # Left-side devices (gx < 0) face inward (mirrored=True)
                # Right-side devices (gx > 0) face inward (mirrored=False)
                mirrored = (gx < 0)

        positions[did] = {
            "cx":       gx * cell_px,
            "cy":       gy * cell_px,
            "mirrored": mirrored,
            "rank":     0 if gy < 0 else 2,
        }

    # wire_nets: accept both ["net1", "net2"] and [{"net_name": "net1"}, ...]
    raw_wn = data.get("wire_nets", [])
    wire_nets: list[str] = []
    for entry in raw_wn:
        if isinstance(entry, str):
            wire_nets.append(entry)
        elif isinstance(entry, dict):
            n = entry.get("net_name") or entry.get("name") or entry.get("net")
            if n:
                wire_nets.append(str(n))

    # Force internal cross-coupling nets to be wires regardless of AI hallucination
    for forced_net in ["VX", "VY"]:
        if forced_net not in wire_nets:
            wire_nets.append(forced_net)

    cross_wires: list[tuple] = []
    for seg in data.get("cross_wires", []):
        cross_wires.append((
            float(seg.get("x1", 0)) * cell_px,
            float(seg.get("y1", 0)) * cell_px,
            float(seg.get("x2", 0)) * cell_px,
            float(seg.get("y2", 0)) * cell_px,
        ))

    if not positions:
        log.warning("Vertex AI: no device positions in response")
        return None

    log.info("Vertex AI layout applied: %d devices, %d wire_nets, %d cross_wires",
             len(positions), len(wire_nets), len(cross_wires))
    return {"positions": positions, "wire_nets": wire_nets, "cross_wires": cross_wires}

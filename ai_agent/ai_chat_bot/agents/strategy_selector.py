"""
ai_agent/ai_chat_bot/agents/strategy_selector.py
================================
Stage 2 — Presents high-level improvement strategies to the user.
Runs after topology confirmation, before the Placement Specialist.
The user picks a strategy (or 'all'), then the pipeline resumes.
"""

STRATEGY_SELECTOR_PROMPT = """\
You are the STRATEGY SELECTOR agent in a multi-agent analog IC layout system.

You are given:
- A circuit topology (devices, connectivity, roles)
- A user improvement request

Your task is to generate 3 to 5 high-level floorplanning strategies specific to this circuit.

------------------------------
MANDATORY RULES (STRICT)
------------------------------

1) Device Exclusivity (VERY IMPORTANT)
- Each device can appear in ONLY ONE strategy.
- A device name must NOT be repeated in any other strategy.
- Before output, ensure NO device is reused across strategies.

1.5) Strategy Scope Definition
- Each strategy should operate on one or more COMPLETE topology groups.
- A group must not be split across strategies.
- Different strategies should cover different groups when possible.

2) Floorplanning Only (NO EXCEPTIONS)
Strategies must involve device placement ONLY.

Allowed concepts:
- Common centroid
- Interdigitated placement
- Symmetry (horizontal/vertical)
- Mirroring
- Clustering / grouping
- Relative positioning (alignment, proximity)
- Bias mirror proximity optimization

Forbidden:
- Guard rings
- Routing or wiring changes
- Transistor sizing (W/L)
- Electrical parameter tuning
- Adding or removing devices

3) Topology-Aware
- Use actual device names from the topology.
- Use the provided TOPOLOGY_GROUPS, Roles, Matching_Requirements, and Symmetry explicitly.
- Placement decisions MUST respect matching and symmetry constraints.
- Do NOT break required matching or symmetry relationships.
- Do NOT give generic advice.

4) High-Level Only
- Do NOT give step-by-step instructions.
- Each strategy must be a conceptual placement approach.

5) Distinct Strategies
- Each strategy must represent a different placement idea, not small variations.

6) Global Compatibility (CRITICAL)
- ALL strategies must be mutually compatible and non-conflicting.
- The strategies are NOT alternatives; they are complementary.
- It must be possible to apply ALL strategies together to form one valid, consistent floorplan.
- Do NOT create conflicting symmetry axes or placement constraints between strategies.
- All strategies must be geometrically consistent when combined into one floorplan.
- Relative placements defined in one strategy must not contradict another.

7) Group Integrity (CRITICAL)
- Devices belonging to the same topology group MUST remain together.
- Do NOT split devices from the same group across different strategies.
- If a strategy uses a group, it must include ALL devices in that group.

------------------------------
OUTPUT FORMAT (EXACT)
------------------------------

Based on your circuit topology, here are the recommended improvement strategies:

[STRATEGY_NAME] — [One sentence: describe WHAT placement is applied to WHICH devices and WHY it improves matching, symmetry, or parasitics]

[STRATEGY_NAME] — [One sentence]

[STRATEGY_NAME] — [One sentence]

(Add a 4th and 5th strategy only if clearly useful and different)

------------------------------
MATCHING GROUPS (MANDATORY — pass through from topology)
------------------------------

If the topology analysis identified match_groups (diff pairs, current mirrors),
you MUST include them in your output JSON. You may also add new matching groups
if your strategy calls for matching that the topology analyst missed.

Include the match_groups in your JSON output block like this:

```json
{
  "reasoning": "...",
  "nmos_rows": [...],
  "pmos_rows": [...],
  "match_groups": [
    {"devices": ["MM0", "MM1"], "technique": "COMMON_CENTROID_1D"},
    {"devices": ["MM3", "MM4"], "technique": "INTERDIGITATION"}
  ]
}
```

Available matching techniques:
  - "COMMON_CENTROID_1D" — diff pairs (centroid symmetry in 1 row)
  - "COMMON_CENTROID_2D" — diff pairs (point symmetry across 2 rows)
  - "INTERDIGITATION"    — current mirrors (ratio-proportional mixing)

------------------------------
FINAL CHECK (REQUIRED BEFORE OUTPUT)
------------------------------

- No device appears in more than one strategy
- All strategies are placement-only
- No forbidden operations are mentioned
- Strategies are specific to the given topology
- All strategies can be applied together without conflict
- match_groups from topology are carried forward in the JSON

If any rule is violated, regenerate the answer.
"""


def _normalize_chat_history(chat_history):
  normalized = []
  if not isinstance(chat_history, list):
    return normalized

  for msg in chat_history:
    if not isinstance(msg, dict):
      continue
    role = str(msg.get("role", "")).strip()
    content = str(msg.get("content", "")).strip()
    if not role or not content:
      continue
    normalized.append({"role": role, "content": content})

  return normalized


def generate_strategies(user_message: str, constraint_text: str, run_llm_fn, chat_history=None) -> str:
    """Ask the LLM to generate strategy options for this circuit.

    Args:
        user_message:    the user's original request.
        constraint_text: topology constraint summary from Stage 1.
        run_llm_fn:      the run_llm callable from llm_worker.py.
        chat_history:    optional prior role/content messages.

    Returns:
        A formatted strategy selection string for the user.
    """
    # Check if circuit has mirrors — if so, add mirror-specific strategies
    has_mirror = "MIRROR" in (constraint_text or "").upper()

    user_content = (
        f"User request: {user_message}\n\n"
        f"Circuit topology:\n{constraint_text}\n\n"
        f"Generate 3-5 strategies tailored to the devices and nets shown above."
    )
    msgs = [{"role": "system", "content": STRATEGY_SELECTOR_PROMPT}]
    msgs.extend(_normalize_chat_history(chat_history)[-8:])
    msgs.append({"role": "user", "content": user_content})
    try:
        result = run_llm_fn(msgs, user_content)
        if result and len(result.strip()) > 20:
            llm_text = result.strip()
            return llm_text
    except Exception as exc:
        print(f"[STRATEGY] LLM failed: {exc} — using fallback")

    # Deterministic fallback — always safe
    if has_mirror:
        return _mirror_fallback_strategies()
    else:
        return (
            "Here are the recommended improvement strategies:\n\n"
            "1. **Enhance Symmetry** — Place matched pairs equidistant from the "
            "row centre to enforce layout symmetry.\n"
            "2. **Improve Matching** — Abut mirror devices (same W/L/nf) with the "
            "same orientation to minimise systematic mismatch.\n"
            "3. **Minimise DRC Violations** — Resolve all overlap and gap violations "
            "before any other optimisation.\n\n"
            "Type a number (1-3), 'all', or describe a custom approach to proceed."
        )


def _mirror_fallback_strategies() -> str:
    """Fallback strategies when mirrors are detected."""
    return (
        "Here are the recommended placement strategies for your current mirror:\n\n"
        "1. **Interdigitated Placement** — Single-row ABAB pattern. "
        "Fingers of matched devices are alternated in one row for 1D gradient cancellation. "
        "Compact layout, good for moderate matching requirements.\n\n"
        "2. **Common Centroid Placement** — Multi-row 2D symmetric placement. "
        "Fingers are distributed across multiple rows, symmetric about the center. "
        "Best matching performance — cancels gradients in both X and Y directions.\n\n"
        "3. **Auto (recommended)** — System automatically selects interdigitated "
        "(≤16 fingers) or common centroid (>16 fingers) based on total finger count.\n\n"
        "Type **1** for interdigitated, **2** for common centroid, "
        "**3** for auto, or describe a custom approach."
    )

def parse_placement_mode(user_message: str, constraint_text: str = "") -> str:
    """Parse the user's strategy choice into a placement mode.

    Args:
        user_message:    User's reply (e.g., "1", "2", "common centroid", etc.)
        constraint_text: Topology constraints (used to check for mirrors)

    Returns:
        "interdigitated" | "common_centroid" | "auto"
    """
    has_mirror = "MIRROR" in (constraint_text or "").upper()
    if not has_mirror:
        return "auto"

    msg = user_message.strip().lower()

    # Explicit keyword matching
    if "common centroid" in msg or "common_centroid" in msg or "common-centroid" in msg:
        return "common_centroid"
    if "interdigit" in msg:  # matches "interdigitated", "interdigitation"
        return "interdigitated"

    # Strategy number matching (only if using mirror fallback strategies)
    # 1 = interdigitated, 2 = common centroid, 3 = auto
    if msg in ("1", "1."):
        return "interdigitated"
    if msg in ("2", "2."):
        return "common_centroid"
    if msg in ("3", "3.", "auto", "all", "yes"):
        return "auto"

    # Default: auto
    return "auto"


# ---------------------------------------------------------------------------
# Multi-row floorplan context builder
# (ported + improved from multi_agent_placer._build_multirow_prompt)
# ---------------------------------------------------------------------------

import math
import re as _re
import json as _json

# Physical layout constants (must match geometry_engine.py)
_ROW_PITCH    = 0.668
_STD_PITCH    = 0.294
_MAX_ROW_DEVS = 16


def _device_width_est(node: dict) -> float:
    """Estimate device width from geometry or nf parameter."""
    geo = node.get("geometry", {})
    w = geo.get("width", 0)
    if w and float(w) > 0:
        return float(w)
    nf = max(1, int(node.get("electrical", {}).get("nf", 1)))
    return round(nf * _STD_PITCH, 6)


def build_multirow_floorplan_context(
    nodes: list,
    edges: list,
    constraint_text: str,
    abutment_candidates: list = None,
) -> str:
    """
    Build the comprehensive multi-row placement prompt for the Strategy/Placement LLM.

    Asks the LLM to assign every device to a NAMED FUNCTIONAL ROW and order
    it Left-to-Right. Coordinates are computed later by the geometry engine —
    the LLM outputs ZERO floating-point numbers.

    Parameters
    ----------
    nodes               : logical device nodes
    edges               : edge list
    constraint_text     : topology summary from Stage 1 (topology analyst)
    abutment_candidates : abutment pair candidates

    Returns
    -------
    str — the full prompt to inject into the strategy/placement LLM call.
    """
    pmos_ids = sorted(n["id"] for n in nodes if n.get("type") == "pmos")
    nmos_ids = sorted(n["id"] for n in nodes if n.get("type") == "nmos")

    # Electrical summary per device
    elec_lines = []
    for n in sorted(nodes, key=lambda x: x.get("id", "")):
        e = n.get("electrical", {})
        elec_lines.append(
            f"  {n['id']:12s}  {n.get('type','?'):5s}  "
            f"m={e.get('m',1)}  nf={e.get('nf',1)}  nfin={e.get('nfin',1)}"
        )
    elec_str = "\n".join(elec_lines)

    # Net adjacency
    _POWER = frozenset({"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS"})
    net_devs: dict = {}
    for e in (edges or []):
        net = e.get("net", "")
        if net and net.upper() not in _POWER:
            net_devs.setdefault(net, set()).add(e.get("source", ""))
            net_devs.setdefault(net, set()).add(e.get("target", ""))
    adjacency_lines = []
    for net, devs in sorted(net_devs.items()):
        adjacency_lines.append(f"  {net:<12} -> {', '.join(sorted(d for d in devs if d))}")
    adjacency_str = "\n".join(adjacency_lines) if adjacency_lines else "  (no signal nets)"

    # Abutment section
    abut_section = ""
    if abutment_candidates:
        abut_lines = [
            f"  - {c['dev_a']} <-> {c['dev_b']}  (net: {c.get('shared_net', '?')})"
            for c in abutment_candidates
        ]
        abut_section = "\nABUTMENT REQUIREMENTS (these pairs MUST be adjacent):\n" + "\n".join(abut_lines) + "\n"

    # Square aspect ratio guidance
    n_total  = len(nmos_ids) + len(pmos_ids)
    avg_w    = sum(_device_width_est(n) for n in nodes) / max(1, len(nodes))
    devs_per_row   = max(4, int(math.sqrt(n_total * _ROW_PITCH / avg_w)))
    devs_per_row   = min(devs_per_row, _MAX_ROW_DEVS)
    target_n_rows  = max(1, math.ceil(len(nmos_ids) / devs_per_row))
    target_p_rows  = max(1, math.ceil(len(pmos_ids) / devs_per_row))
    target_n_per   = math.ceil(len(nmos_ids) / target_n_rows) if target_n_rows else 0
    target_p_per   = math.ceil(len(pmos_ids) / target_p_rows) if target_p_rows else 0

    square_guidance = (
        f"SQUARE ASPECT RATIO TARGET (IMPORTANT):\n"
        f"  Total devices = {n_total} ({len(nmos_ids)} NMOS + {len(pmos_ids)} PMOS)\n"
        f"  Average device width ≈ {avg_w:.3f}µm, row pitch = {_ROW_PITCH}µm\n"
        f"  For a near-square layout, aim for:\n"
        f"    • ~{target_n_rows} NMOS row(s) with ~{target_n_per} devices each\n"
        f"    • ~{target_p_rows} PMOS row(s) with ~{target_p_per} devices each\n"
        f"  Maximum {_MAX_ROW_DEVS} devices per row (rows exceeding this will be auto-split).\n"
    )

    return f"""\
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MULTI-ROW FLOORPLAN ASSIGNMENT (required output)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

You must also output a JSON block (after your strategy text) that assigns
every device to a named functional row. This JSON is consumed directly by
the geometry engine — DO NOT include any x/y coordinates.

CIRCUIT TOPOLOGY (read carefully):
{constraint_text}

ELECTRICAL PARAMETERS:
{elec_str}

NET CONNECTIVITY (place devices sharing a net ADJACENT):
{adjacency_str}
{abut_section}
DEVICES TO PLACE:
  NMOS ({len(nmos_ids)} total): {', '.join(nmos_ids)}
  PMOS ({len(pmos_ids)} total): {', '.join(pmos_ids)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PLACEMENT RULES (MANDATORY):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RULE 1 — PMOS/NMOS SEPARATION (ABSOLUTE):
  • nmos_rows contains ONLY NMOS device IDs.
  • pmos_rows contains ONLY PMOS device IDs. NEVER mix.

RULE 2 — COMPLETE COVERAGE:
  • Every device ID appears EXACTLY ONCE across ALL rows.

RULE 3 — FUNCTIONAL ROW GROUPING:
  • Each row must contain one functional group (do NOT mix input pair + cascode).

RULE 4 — WITHIN-ROW ORDERING for MATCHING:
  • Diff pair (A,B): use A-B-B-A interdigitation.
  • Current mirror (ref, copies): centroid ordering.
  • Cascode devices: same slot order as the row below.
  • Multi-finger devices (_f1,_f2,…): consecutive order.

RULE 5 — ROUTING AWARENESS:
  • Place devices sharing a SIGNAL net adjacent.
  • Place cascode devices directly above their drive device.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{square_guidance}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

OUTPUT FORMAT (append to your strategy text, valid JSON only):

```json
{{
  "reasoning": "One sentence: circuit type + why you chose this row structure.",
  "nmos_rows": [
    {{"label": "FUNCTIONAL_LABEL", "devices": ["dev_id1", "dev_id2", ...]}},
    ...
  ],
  "pmos_rows": [
    {{"label": "FUNCTIONAL_LABEL", "devices": ["dev_id1", "dev_id2", ...]}},
    ...
  ]
}}
```

CRITICAL CHECKS before outputting:
  [ ] Every NMOS device ID appears exactly once in nmos_rows.
  [ ] Every PMOS device ID appears exactly once in pmos_rows.
  [ ] No PMOS ID is in nmos_rows. No NMOS ID is in pmos_rows.
  [ ] No row has more than {_MAX_ROW_DEVS} devices.
  [ ] The JSON is valid (no trailing commas, no comments).
"""


def parse_multirow_json(text: str) -> dict:
    """
    Extract the {nmos_rows, pmos_rows} JSON from an LLM response that may
    contain free-form strategy text before the JSON block.

    Returns an empty dict if no valid JSON with nmos_rows/pmos_rows is found.
    """
    if not text:
        return {}

    # Try a fenced code block first
    fenced = _re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, _re.IGNORECASE)
    if fenced:
        try:
            data = _json.loads(fenced.group(1))
            if "nmos_rows" in data or "pmos_rows" in data:
                return data
        except Exception:
            pass

    # Try bare JSON object anywhere in the text
    brace = text.find("{")
    while brace != -1:
        try:
            # Find matching close brace via bracket counting
            depth, end = 0, brace
            for i, ch in enumerate(text[brace:]):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = brace + i + 1
                        break
            candidate = text[brace:end]
            data = _json.loads(candidate)
            if "nmos_rows" in data or "pmos_rows" in data:
                return data
        except Exception:
            pass
        brace = text.find("{", brace + 1)

    return {}

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
FINAL CHECK (REQUIRED BEFORE OUTPUT)
------------------------------

- No device appears in more than one strategy
- All strategies are placement-only
- No forbidden operations are mentioned
- Strategies are specific to the given topology
- All strategies can be applied together without conflict

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

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
- TOPOLOGY_GROUPS (predefined logical groupings)
- Matching_Requirements and Symmetry constraints
- A user improvement request

Your task is to generate 3 to 5 HIGH-LEVEL floorplanning strategies.

Each strategy represents a PLACEMENT CONSTRAINT LAYER.
Strategies are NOT alternatives — they are COMPOSABLE and must work together.

────────────────────────────────────
CORE PRINCIPLE (CRITICAL)
────────────────────────────────────

Strategies DEFINE constraints over the SAME layout.

- A device MAY appear in MULTIPLE strategies
- Strategies MUST be geometrically compatible
- Strategies MUST NOT conflict when applied together

Think of each strategy as adding a constraint to a shared placement solution.

────────────────────────────────────
MANDATORY RULES (STRICT)
────────────────────────────────────

1) Constraint-Based (NOT Partition-Based)
- DO NOT partition devices across strategies
- DO NOT enforce exclusivity
- Devices and groups MAY appear in multiple strategies

2) Floorplanning Only (NO EXCEPTIONS)

Allowed:
- Common centroid placement
- Interdigitated placement
- Symmetry (horizontal / vertical)
- Mirroring
- Clustering / grouping
- Relative positioning (alignment, adjacency, centering)
- Proximity based on connectivity (shared nets, current flow)

Forbidden:
- Guard rings
- Routing/wiring instructions
- Transistor sizing (W/L)
- Electrical parameter tuning
- Adding/removing devices

3) Topology-Aware (MANDATORY)

- Use actual device names from the topology
- Use TOPOLOGY_GROUPS explicitly
- Respect Matching_Requirements and Symmetry constraints
- DO NOT break required matching relationships
- DO NOT split topology groups

4) Group Integrity

- Devices in the same topology group must remain logically unified
- Internal interleaving (e.g., interdigitation, centroid patterns) is ALLOWED
- Groups must NOT be fragmented across unrelated placement regions

5) Geometric Explicitness (CRITICAL)

Each strategy MUST clearly imply:

- Target group(s) or devices
- Placement structure:
    (common centroid / interdigitated / mirror / cluster / aligned)
- Symmetry axis (horizontal or vertical) IF applicable
- Relative positioning:
    (centered, edge-aligned, adjacent to another group, aligned with axis)

Do NOT leave geometry ambiguous.

6) Electrical Awareness (REQUIRED)

Strategies SHOULD consider:

- Proximity of strongly connected devices
- Bias distribution paths
- Differential signal symmetry
- Minimization of parasitic imbalance via placement

7) Global Compatibility (CRITICAL)

ALL strategies MUST be simultaneously satisfiable.

Two strategies are compatible ONLY IF:
- They do NOT impose conflicting symmetry axes on the same group
- They do NOT enforce contradictory relative positions
- They do NOT assign the same group to incompatible anchors
- They preserve all matching constraints together

8) Constraint Priority Awareness

Prefer generating strategies in this implicit priority order:

1. Matching & symmetry (highest priority)
2. Bias/mirror structure integrity
3. Connectivity-driven proximity
4. Secondary clustering / alignment

Do NOT generate low-value or redundant strategies.

9) High-Level Only

- Do NOT provide step-by-step procedures
- Do NOT describe implementation details
- Each strategy must be ONE concise sentence

10) Distinct Constraint Layers

- Each strategy must represent a DIFFERENT placement idea
- Avoid redundant or overlapping descriptions of the same constraint

11) Constraint Relaxation Awareness

- Strategies represent DESIRED constraints, not guaranteed constraints
- Lower-priority strategies may be partially relaxed during placement
- Do NOT assume all strategies will be fully satisfied simultaneously
- Prefer generating strategies aligned with known constraint priority:
    Matching > Symmetry > Bias structure > Proximity > Clustering

12) Symmetry Relaxation Awareness

- Symmetry-based strategies (CC/MB/DP) may be locally relaxed
  if strong connectivity constraints dominate in placement
- Avoid over-constraining symmetry when connectivity is critical

────────────────────────────────────
OUTPUT FORMAT (EXACT)
────────────────────────────────────

Based on your circuit topology, here are the recommended improvement strategies:

[STRATEGY_NAME] — Apply [placement structure] to [devices/groups] along [axis if applicable], positioned [relative placement], to improve [matching/symmetry/parasitics reason].

[STRATEGY_NAME] — Apply ...

[STRATEGY_NAME] — Apply ...

(Add a 4th and 5th strategy only if clearly useful and non-redundant)

────────────────────────────────────
FINAL VALIDATION (REQUIRED)
────────────────────────────────────

Before output, ensure:

✓ Strategies are placement-only (no forbidden operations)
✓ Devices/groups may appear in multiple strategies where appropriate
✓ No topology group is split or violated
✓ All strategies are geometrically compatible
✓ No conflicting symmetry axes or placement constraints
✓ Strategies are specific to the given topology (NOT generic)
✓ Each strategy is a distinct constraint layer
✓ All strategies can be applied together into ONE valid floorplan

If ANY rule is violated → regenerate the answer.

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

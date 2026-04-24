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
- Circuit topology (devices, connectivity, roles)
- TOPOLOGY_GROUPS (logical groupings)
- Matching requirements and symmetry constraints
- User improvement request

Your task:
Generate 3 to 5 HIGH-LEVEL floorplanning strategies.

Each strategy is a PLACEMENT CONSTRAINT LAYER.
Strategies are COMPOSABLE (NOT alternatives) and must work together.

────────────────────────────────────────────
1. INPUTS
────────────────────────────────────────────

You receive:

- TOPOLOGY_GROUPS (device groupings, roles, matching, symmetry)
- CURRENT_FLOW_GRAPH (directed bias dependency graph)
- NETLIST_GRAPH (undirected weighted connectivity graph)
- PAIR_MAPPING ((Di+, Di-) differential pairs)
- SKILL_HINT tags (per-device annotations)
- User improvement request

CRITICAL:
- CURRENT_FLOW_GRAPH → bias-related strategies
- NETLIST_GRAPH → proximity/connectivity strategies
Do NOT infer these without graphs.

────────────────────────────────────────────
2. CORE PRINCIPLE
────────────────────────────────────────────

Strategies define constraints over ONE shared layout.

- Devices MAY appear in multiple strategies
- Strategies MUST be geometrically compatible
- Strategies MUST NOT be mutually contradictory

Each strategy = one constraint layer in a global solution.

────────────────────────────────────────────
3. MANDATORY RULES
────────────────────────────────────────────

3.1 Constraint-Based (NOT partition-based)
- DO NOT partition devices across strategies
- DO NOT enforce exclusivity
- Overlap between strategies is allowed

3.2 Floorplanning Only (STRICT)

Allowed:
- Common centroid
- Interdigitation
- Symmetry (horizontal/vertical)
- Mirroring
- Clustering/grouping
- Relative positioning (adjacency, alignment, centering)
- Connectivity-driven proximity

Forbidden:
- Guard rings
- Routing/wiring instructions
- Device sizing (W/L)
- Electrical tuning
- Adding/removing devices

3.3 Topology-Aware (MANDATORY)
- Use exact device names
- Use TOPOLOGY_GROUPS explicitly
- Respect matching + symmetry constraints
- Do NOT split topology groups

3.4 Group Integrity
- Groups remain logically unified
- Internal interleaving is allowed
- No fragmentation across unrelated regions

3.5 Geometric Explicitness (CRITICAL)
Each strategy MUST specify:

- Target devices/groups
- Placement structure (centroid / mirror / interdigitated / cluster / aligned)
- Symmetry axis if applicable
- Relative positioning (centered / edge-aligned / adjacent / axis-aligned)

Avoid ambiguity.

3.6 Electrical Awareness (REQUIRED)
Consider:
- Strong connectivity proximity
- Bias flow alignment
- Differential symmetry
- Parasitic minimization via placement

3.7 Feasibility Awareness
All strategies must be jointly feasible under:

Matching > Symmetry > Bias structure > Proximity > Clustering

Conflicts allowed only if:
- They are lower-priority and resolvable by Placement Specialist
- They do NOT create irreconcilable hard constraint collisions

DO NOT reject strategies due to soft constraint relaxation.

3.8 Constraint Priority Awareness
Preferred strategy ordering:

1. Matching & symmetry
2. Bias/mirror structure
3. Connectivity proximity
4. Alignment/clustering

3.9 High-Level Only
- NO step-by-step procedures
- NO implementation details
- Each strategy = ONE sentence

3.10 Distinctness
- Each strategy must introduce a unique constraint idea
- No redundancy

3.11 Relaxation Awareness
- Strategies are desired constraints, not guarantees
- Lower-priority constraints may be relaxed in placement
- Do NOT assume full simultaneous satisfaction

3.12 Symmetry Relaxation Awareness
- Symmetry may be relaxed if connectivity dominates
- Do NOT over-constrain symmetry in high-connectivity regions

────────────────────────────────────────────
4. OUTPUT FORMAT
────────────────────────────────────────────

Based on your circuit topology, here are the recommended improvement strategies:

[STRATEGY_NAME] — Apply [placement structure] to [devices/groups] along [axis if any], positioned [relative placement], to improve [reason]. [SKILL_HINT: skill_id]

(repeat 3–5 strategies)

────────────────────────────────────────────
5. SKILL_MAP (MANDATORY)
────────────────────────────────────────────

SKILL_MAP:
  [GROUP_NAME]: [skill_id]
  [GROUP_NAME]: [skill_id]

RULES:
- One skill per group
- Choose highest-priority applicable skill

Skill priority:
bias_mirror > differential_pair > common_centroid > interdigitate > multirow_placement > proximity_net

Special rule:
- Groups in bias chains:
  GLOBAL: bias_chain (if CURRENT_FLOW_GRAPH contains bias dependencies)

Valid skill_id values:
bias_mirror | differential_pair | common_centroid | interdigitate |
multirow_placement | proximity_net | matched_environment | diffusion_sharing

────────────────────────────────────────────
6. VALIDATION (STRICT)
────────────────────────────────────────────

Before output ensure:

✓ Placement-only constraints
✓ Devices may appear in multiple strategies
✓ No topology group violations
✓ No conflicting symmetry axes
✓ Strategies are mutually compatible
✓ Strategies are topology-specific
✓ Each strategy is a distinct constraint layer
✓ All strategies can co-exist in one floorplan

FAIL ANY → regenerate output

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

"""
Strategy Selector Agent
=======================
Generates high-level placement improvement strategies based on circuit topology
and user requests.

Functions:
- _normalize_chat_history: Normalizes chat history for LLM consumption.
- generate_strategies: Prompts the LLM to generate 3-5 placement strategies.
  - Inputs: user_message (str), constraint_text (str), run_llm_fn (callable), chat_history (optional list)
  - Outputs: formatted strategy selection string.
- _mirror_fallback_strategies: Provides static fallback strategies for mirrors.
- parse_placement_mode: Parses user feedback into a specific placement mode.
  - Inputs: user_message (str), constraint_text (str)
  - Outputs: mode string ("interdigitated", "common_centroid", or "auto").

NOTE: parse_placement_mode, _normalize_chat_history, and _mirror_fallback_strategies
      now live in ai_agent.core.strategy.
      This module re-exports them for backwards compatibility.
"""

# Re-export pure-Python helpers from core
from ai_agent.core.strategy import (
    _normalize_chat_history,
    _mirror_fallback_strategies,
    parse_placement_mode,
)

__all__ = [
    "STRATEGY_SELECTOR_PROMPT",
    "generate_strategies",
    "parse_placement_mode",
    "_normalize_chat_history",
    "_mirror_fallback_strategies",
]

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
- Symmetry (horizontal/vertical/two-half vertical-axis)
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

3.13 THE FATAL CONFLICT RULE (STRICT)
- NEVER generate a strategy with [SKILL_HINT: common_centroid] for devices that belong to a group assigned 'differential_pair' in the SKILL_MAP.
- This specific combination will trigger an immediate FATAL system crash downstream.
- Differential pairs MUST only use DP, MB, or Matched Environment hints.

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
- One STRUCTURAL skill per group (bias_mirror, differential_pair, common_centroid, interdigitate).
- REFINEMENT skills (matched_environment, diffusion_sharing) are EXEMPT from the one-skill limit and can be appended to any group.
- Choose highest-priority applicable structural skill.

Skill priority:
differential_pair > bias_mirror > common_centroid > interdigitate > multirow_placement > proximity_net

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

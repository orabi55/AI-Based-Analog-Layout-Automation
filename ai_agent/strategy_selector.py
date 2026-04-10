"""
ai_agent/strategy_selector.py
================================
Stage 1.5 — Presents high-level improvement strategies to the user.
Runs after topology confirmation, before the Placement Specialist.
The user picks a strategy (or 'all'), then the pipeline resumes.
"""

STRATEGY_SELECTOR_PROMPT = """\
You are the STRATEGY SELECTOR agent in a multi-agent analog IC layout system.

You receive the circuit topology and the user's improvement request.
Your job is to present 3-5 high-level improvement strategies the designer
can choose from — specific to THIS circuit's topology.

Format your response EXACTLY like this:

Based on your circuit topology, here are the recommended improvement strategies:

1. [STRATEGY_NAME] — [one sentence: what will be done and why it improves this circuit]
2. [STRATEGY_NAME] — [one sentence]
3. [STRATEGY_NAME] — [one sentence]
(Add 4th and 5th if genuinely useful for this topology)

Then add EXACTLY this closing line (no other text after it):
Type a number (1-3), 'all', or describe a custom approach to proceed.

AVAILABLE STRATEGIES (pick the most relevant for this specific circuit):
- Enhance Symmetry — Add symmetry constraints so matched pairs have identical
  x-distance from the row centre.
- Improve Matching — Place matched devices (same W/L/nf) adjacent with the
  same orientation to minimise systematic mismatch.
- Reduce Parasitics — Move critical-path devices closer to minimise wire length
  and coupling capacitance on signal nets.
- Prevent Crosstalk — Increase x-separation between sensitive signal-path
  devices and bias/supply-connected devices.
- Align Differential Pair — Centre the diff-pair symmetrically with the tail
  current source directly below.
- Optimise Dummy Placement — Move dummy devices to row edges to free the centre
  for matched active devices.
- Minimise DRC Violations — Resolve all overlap and gap violations first before
  any other optimisation step.
- Optimise Routing Crossings — Reorder devices to reduce net x-span and crossing
  count for the critical differential and output nets.
- Optimise Dummy Placement — Move dummy devices to row edges to free the centre
  for matched active devices.
- Minimise DRC Violations — Resolve all overlap and gap violations first before
  any other optimisation step.
- Optimise Routing Crossings — Reorder devices to reduce net x-span and crossing
  count for the critical differential and output nets.
- **Optimize Current Mirror Matching** — Place ALL mirror devices (shared gate net)
  in consecutive x-slots with identical orientation to minimize systematic mismatch.
  Targets gate resistance, Vth asymmetry, and etch variation.

Example Strategy Output for Circuit with Mirrors:
```
Based on your circuit topology, here are the recommended improvement strategies:

1. **Optimize Current Mirror Matching** — Place MM1 ↔ MM2 (NBIAS mirror) and
   MM5 ↔ MM6 (PBIAS mirror) in consecutive slots with R0 orientation to achieve
   <0.5% current matching accuracy.

2. **Enhance Symmetry** — Center the differential pair MM3 ↔ MM4 about x=1.470
   with tail current source MM7 directly below.

3. **Minimize DRC Violations** — Resolve 2 overlap violations before optimization.

Type a number (1-3), 'all', or describe a custom approach to proceed.
```

Rules:
- **ALWAYS suggest "Optimize Current Mirror Matching" if ANY shared-gate devices exist**
- Pick strategies relevant to the ACTUAL devices and constraints shown.
- Do NOT suggest cascode alignment if there are no cascode devices.
- Do NOT suggest diff-pair centering if there is no diff-pair identified.
- Be specific: name the actual device IDs in the strategy description.
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
            # Append mirror placement options if LLM didn't include them
            if has_mirror and "interdigitated" not in llm_text.lower():
                llm_text += _mirror_placement_options()
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


def _mirror_placement_options() -> str:
    """Extra options to append to LLM strategies when mirrors exist."""
    return (
        "\n\n---\n\n"
        "**Mirror Placement Options:**\n"
        "- Type **'interdigitated'** for single-row ABAB pattern (1D matching)\n"
        "- Type **'common centroid'** for multi-row 2D symmetric placement (best matching)\n"
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

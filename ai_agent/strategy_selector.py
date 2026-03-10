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

Rules:
- Pick strategies relevant to the ACTUAL devices and constraints shown.
- Do NOT suggest cascode alignment if there are no cascode devices.
- Do NOT suggest diff-pair centering if there is no diff-pair identified.
- Be specific: name the actual device IDs in the strategy description.
"""


def generate_strategies(user_message: str, constraint_text: str, run_llm_fn) -> str:
    """Ask the LLM to generate strategy options for this circuit.

    Args:
        user_message:    the user's original request.
        constraint_text: topology constraint summary from Stage 1.
        run_llm_fn:      the run_llm callable from llm_worker.py.

    Returns:
        A formatted strategy selection string for the user.
    """
    user_content = (
        f"User request: {user_message}\n\n"
        f"Circuit topology:\n{constraint_text}\n\n"
        f"Generate 3-5 strategies tailored to the devices and nets shown above."
    )
    msgs = [
        {"role": "system", "content": STRATEGY_SELECTOR_PROMPT},
        {"role": "user",   "content": user_content},
    ]
    try:
        result = run_llm_fn(msgs, user_content)
        if result and len(result.strip()) > 20:
            return result.strip()
    except Exception as exc:
        print(f"[STRATEGY] LLM failed: {exc} — using fallback")

    # Deterministic fallback — always safe
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

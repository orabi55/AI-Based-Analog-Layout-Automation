"""
Intent Classifier Agent
=======================
Classifies user intent into concrete graph node targets used by the chat workflow.

Functions:
- classify_intent: Determines intent using regex fast-paths or an LLM call.
    - Inputs: user_message (str), selected_model (str)
    - Outputs: node function name.
"""

import re
from ai_agent.llm.factory import get_langchain_llm

# ── Regex fast-path patterns (zero LLM cost) ─────────────────────
_TOPOLOGY_RE = re.compile(
    r"\b(topolog(?:y|ical)|netlist|circuit|schematic|device\s+count|"
    r"what\s+is\s+mm\d+|explain\s+.*topology|how\s+many\s+(pmos|nmos)|"
    r"which\s+net|connected\s+to|net\s+connects?)\b",
    re.IGNORECASE,
)

_STRATEGY_RE = re.compile(
    r"\b(strategy|floorplan|floorplanning|placement\s+improv|optimi[sz]e"
    r"|symmetr(?:y|ic)|matching|centroid|interdigitat(?:e|ion)|cluster|"
    r"arrange|layout\s+plan)\b",
    re.IGNORECASE,
)

_PLACEMENT_RE = re.compile(
    r"\b(swap|move|flip|add\s+dummy|add\s+dummies|delete|remove|set\s+orientation"
    r"|fix\s+abutment|abut|re-abut|reabut"
    r"|fix\s+finger|fix\s+placement|rearrange|align|pack\s+finger"
    r"|place\s+adjacent|group\s+together|row\s+assignment|abutt(?:e|ed|ing))\b",
    re.IGNORECASE,
)

_DRC_RE = re.compile(
    r"\b(drc|design\s+rule|spacing|overlap|violation|clean\s+up|shorts?|"
    r"opens?|check\s+rules|rule\s+check)\b",
    re.IGNORECASE,
)

_ROUTING_RE = re.compile(
    r"\b(route|routing|wire\s+length|wirelength|crossing|crossings|crossed|"
    r"parasitic|connectivity|connectors?|path\s+length)\b",
    re.IGNORECASE,
)

# ── LLM Classifier prompt ────────────────────────────────────────
CLASSIFIER_PROMPT = """\
You are an intent classifier for an analog IC layout editor.
Classify the user's message into exactly ONE of these intent targets:

    topology_analyst     - topology extraction, circuit understanding,
                           netlist analysis, or general circuit questions.

    strategy_selector    - high-level floorplanning strategy requests,
                           placement improvement, symmetry, matching,
                           and layout constraint brainstorming.

    placement_specialist - direct placement / movement / ordering /
                           abutment / interdigitation / row assignment.

    drc_critic           - DRC violations, spacing, overlap, clean-up,
                           or fix-and-verify layout requests.

    routing_previewer    - routing, wire-length, crossings, connectivity,
                           or parasitic-routing analysis.

Choose the single best target for the user's request.
Reply with ONLY the target name.
Do not explain. Do not add punctuation.
"""


def classify_intent(user_message: str, selected_model: str) -> str:
    """Classify user intent and return the matching node function name.

    Uses a regex fast-path for trivial cases (greetings, obvious
    commands) and falls back to a lightweight LLM call only when
    the intent is ambiguous.

    Args:
        user_message:   the raw user text from the chat panel.
        selected_model: which LLM backend to use.

    Returns:
        Node function name.
    """
    stripped = user_message.strip()

    # ── Fast-path: regex (no LLM cost) ──────────────────────────
    if _TOPOLOGY_RE.search(stripped):
        print(f"[CLASSIFIER] regex -> TOPOLOGY: '{stripped[:60]}'")
        return "topology_analyst"

    normalized = stripped.lower()
    if _STRATEGY_RE.search(stripped):
        print(f"[CLASSIFIER] regex -> STRATEGY: '{stripped[:60]}'")
        return "strategy_selector"

    if _PLACEMENT_RE.search(stripped):
        print(f"[CLASSIFIER] regex -> PLACEMENT: '{stripped[:60]}'")
        return "placement_specialist"

    if _DRC_RE.search(stripped):
        print(f"[CLASSIFIER] heuristic -> DRC: '{stripped[:60]}'")
        return "drc_critic"

    if _ROUTING_RE.search(stripped):
        print(f"[CLASSIFIER] heuristic -> ROUTING: '{stripped[:60]}'")
        return "routing_previewer"

    # ── Slow-path: ask the LLM ──────────────────────────────────
    msgs = [
        {"role": "system", "content": CLASSIFIER_PROMPT},
        {"role": "user",   "content": user_message},
    ]
    full_prompt = CLASSIFIER_PROMPT + "\n\n" + user_message
    try:
        llm = get_langchain_llm(selected_model, task_weight="light")
        print(f"[CLASSIFIER] Requesting Intent Classification from {selected_model}...")
        result = llm.invoke(msgs)
        if not result:
            return "topology_analyst"
        label = result.content.strip().lower().split()[0].rstrip(".,;:")
        node_labels = {
            "topology_analyst": "topology_analyst",
            "strategy_selector": "strategy_selector",
            "placement_specialist": "placement_specialist",
            "drc_critic": "drc_critic",
            "routing_previewer": "routing_previewer",
        }
        if label in node_labels:
            print(f"[CLASSIFIER] LLM -> {label}: '{stripped[:60]}'")
            return node_labels[label]
    except Exception as exc:
        print(f"[CLASSIFIER] Failed: {exc} — defaulting to topology analyst")
    return "topology_analyst"

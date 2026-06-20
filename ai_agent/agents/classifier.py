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
from ai_agent.utils.logging import vprint

# ── LLM Classifier prompt ────────────────────────────────────────
CLASSIFIER_PROMPT = """\
You are an intent classifier for an analog IC layout assistant.
Classify the user's message into exactly ONE of these intent targets:

    placement_specialist - Direct placement / movement / ordering /
                           abutment / interdigitation / row assignment / optimize routing crossings.
                           Usually a command for the placement engine.

    drc_critic           - DRC violations, spacing, overlap, clean-up,
                           or fix-and-verify layout requests.

    layout_query         - Specific questions about the currently loaded layout, net list,
                           design context, or explaining/analyzing the circuit/layout topology
                           (e.g. "explain this circuit", "what does this circuit do?", "is MM3 connected to MM4?").
                           Use this when the user asks about the active layout/circuit.

    general_chat         - Factual lookups, definitions, general programming help,
                           knowledge questions about concepts (e.g. "What is a cascode mirror?"),
                           greetings, or small talk. Use this when the user is asking general questions
                           and NO active layout data is required to answer.

Choose the single best target for the user's request.
Reply with ONLY the target name.
Do not explain. Do not add punctuation.
"""


def classify_intent(user_message: str, selected_model: str) -> str:
    """Classify user intent and return the matching node function name.

    Uses a regex fast-path for trivial cases (greetings, obvious
    commands, layout explanation queries) and falls back to a
    lightweight LLM call only when the intent is ambiguous.

    Args:
        user_message:   the raw user text from the chat panel.
        selected_model: which LLM backend to use.

    Returns:
        Node function name.
    """
    stripped = user_message.strip()
    msg_lower = stripped.lower()

    # Regex fast-path for layout/circuit queries and explanations
    layout_query_patterns = [
        r"\bexplain\b.*\b(circuit|layout|schematic|netlist)\b",
        r"\banalyze\b.*\b(circuit|layout|schematic|netlist)\b",
        r"\bwhat\b.*\b(circuit|layout|netlist)\b.*\b(is|do|does)\b",
        r"\bcurrent\b.*\b(layout|circuit|netlist|schematic)\b",
        r"\bhow\b.*\b(circuit|layout)\b.*\bwork\b",
        r"\bexplain this\b",
        r"\bconnected\b",
        r"\bconnectivity\b",
        r"\bconnection\b",
        r"\boverlap\b",
        r"\bdrc\b",
        r"\bsymmetry\b",
    ]
    if any(re.search(p, msg_lower) for p in layout_query_patterns):
        vprint(f"[CLASSIFIER] Regex Match -> layout_query: '{stripped[:60]}'")
        return "layout_query"

    # ── LLM-only: ask the LLM ───────────────────────────────────
    msgs = [
        {"role": "system", "content": CLASSIFIER_PROMPT},
        {"role": "user",   "content": user_message},
    ]
    full_prompt = CLASSIFIER_PROMPT + "\n\n" + user_message
    try:
        llm = get_langchain_llm(selected_model, task_weight="light")
        vprint(f"[CLASSIFIER] Requesting Intent Classification from {selected_model}...")
        result = llm.invoke(msgs)
        if not result:
            return "general_chat"
        label = result.content.strip().lower().split()[0].rstrip(".,;:")
        node_labels = {
            "topology_analyst": "topology_analyst",
            "strategy_selector": "strategy_selector",
            "placement_specialist": "placement_specialist",
            "drc_critic": "drc_critic",
            "general_chat": "general_chat",
            "layout_query": "layout_query",
            "general": "general_chat",
        }
        if label in node_labels:
            vprint(f"[CLASSIFIER] LLM -> {label}: '{stripped[:60]}'")
            return node_labels[label]
    except Exception as exc:
        vprint(f"[CLASSIFIER] Failed: {exc} — defaulting to general_chat")
    return "general_chat"

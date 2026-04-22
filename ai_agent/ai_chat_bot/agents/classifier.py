"""
ai_agent/ai_chat_bot/agents/classifier.py
==========================================
Lightweight intent classifier — runs before any pipeline stage.
Returns: 'concrete' | 'abstract' | 'question' | 'chat'

Adapted from ai_agent_old/classifier_agent.py with a local regex
fast-path so trivial greetings never hit the LLM at all.
"""

import re
from ai_agent.ai_chat_bot.llm_factory import get_langchain_llm

# ── Regex fast-path patterns (zero LLM cost) ─────────────────────
_CHAT_RE = re.compile(
    r"^(hi|hello|hey|thanks|thank you|bye|good\s*(morning|evening|night)|"
    r"how\s+are\s+you|what\s+can\s+you\s+do|help|ok|okay|great|sure|cool)[\s!?.]*$",
    re.IGNORECASE,
)

_CONCRETE_RE = re.compile(
    r"\b(swap|move|flip|add\s+dummy|add\s+dummies|delete|remove|set\s+orientation"
    r"|fix\s+abutment|abut|re-abut|reabut"
    r"|fix\s+finger|fix\s+placement|rearrange|align|pack\s+finger"
    r"|place\s+adjacent|group\s+together)\b",
    re.IGNORECASE,
)

# ── LLM Classifier prompt ────────────────────────────────────────
CLASSIFIER_PROMPT = """\
You are an intent classifier for an analog IC layout editor.
Classify the user's message into exactly ONE of these categories:

  CONCRETE  - A direct device operation that maps to a specific command:
              swap, move, flip, add dummy, delete, set orientation.
              Examples: 'swap MM3 and MM5', 'move MM8 to the left',
              'flip MM12 horizontally', 'add a dummy on the right'.

  ABSTRACT  - A high-level design goal that requires topology analysis:
              improve, optimize, enhance, reduce, fix, better, CMRR,
              matching, symmetry, routing, parasitics, DRC, placement.
              Examples: 'improve the matching', 'optimize placement',
              'reduce routing crossings', 'fix DRC violations',
              'enhance symmetry of differential pair'.

  QUESTION  - An informational query. No layout changes needed.
              Examples: 'what is MM3?', 'which net connects M6 and M7?',
              'explain current mirror topology', 'how many PMOS devices?'.

  CHAT      - Casual conversation, greetings, thanks, or small talk.
              No layout analysis or changes needed.
              Examples: 'hi', 'hello', 'thanks', 'how are you',
              'good morning', 'bye', 'what can you do?', 'help'.

Reply with ONLY one word: CONCRETE, ABSTRACT, QUESTION, or CHAT.
Do not explain. Do not add punctuation.
"""


def classify_intent(user_message: str, selected_model: str) -> str:
    """Classify user intent as 'concrete', 'abstract', 'question', or 'chat'.

    Uses a regex fast-path for trivial cases (greetings, obvious
    commands) and falls back to a lightweight LLM call only when
    the intent is ambiguous.

    Args:
        user_message:   the raw user text from the chat panel.
        selected_model: which LLM backend to use.

    Returns:
        'concrete' | 'abstract' | 'question' | 'chat'
        Falls back to 'abstract' on any error so the pipeline always runs.
    """
    stripped = user_message.strip()

    # ── Fast-path: regex (no LLM cost) ──────────────────────────
    if _CHAT_RE.match(stripped):
        print(f"[CLASSIFIER] regex -> CHAT: '{stripped[:60]}'")
        return "chat"

    if _CONCRETE_RE.search(stripped):
        print(f"[CLASSIFIER] regex -> CONCRETE: '{stripped[:60]}'")
        return "concrete"

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
            return "abstract"
        label = result.content.strip().upper().split()[0].rstrip(".,;:")
        if label in ("CONCRETE", "ABSTRACT", "QUESTION", "CHAT"):
            print(f"[CLASSIFIER] LLM -> {label}: '{stripped[:60]}'")
            return label.lower()
    except Exception as exc:
        print(f"[CLASSIFIER] Failed: {exc} — defaulting to abstract")
    return "abstract"

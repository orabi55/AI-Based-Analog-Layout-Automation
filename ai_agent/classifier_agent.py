"""
ai_agent/classifier_agent.py
==============================
Lightweight intent classifier — runs before any pipeline stage.
Returns: 'concrete' | 'abstract' | 'question'

This is a fast LLM call (tiny prompt, ~200 tokens) that routes the user's
message correctly before committing to the 4-stage pipeline.
"""

CLASSIFIER_PROMPT = """\
You are an intent classifier for an analog IC layout editor.
Classify the user's message into exactly ONE of these categories:

  CONCRETE  - A direct device operation that maps to a specific command:
              swap, move, flip, add dummy, delete, set orientation.
              Examples: 'swap MM3 and MM5', 'move MM8 to the left',
              'flip MM12 horizontally', 'add a dummy on the right'.

  ABSTRACT  - A high-level design goal that requires topology analysis,
              It usually starts with a verb or command such as:
              improve, optimize, enhance, reduce, fix, better, CMRR,
              matching, symmetry, routing, parasitics, DRC, placement.
              Examples: 'improve the matching', 'optimize placement',
              'reduce routing crossings', 'fix DRC violations',
              'enhance symmetry of differential pair'.

  QUESTION  - An informational query. No layout changes needed. It may require analysis to answer,
              but the user is asking for information, not requesting a change.
              It often starts with a question word: what, which, how, why,
              or a verb like explain, count, list.
              Examples: 'what is MM3?', 'which net connects M6 and M7?',
              'explain current mirror topology', 'how many PMOS devices?'.

  CHAT      - Casual conversation, greetings, thanks, or small talk.
              No layout analysis or changes needed.
              Examples: 'hi', 'hello', 'thanks', 'how are you',
              'good morning', 'bye', 'what can you do?', 'help'.

Reply with ONLY one word: CONCRETE, ABSTRACT, QUESTION, or CHAT.
Do not explain. Do not add punctuation.
"""


def classify_intent(user_message: str, run_llm_fn) -> str:
    """Classify user intent as 'concrete', 'abstract', 'question', or 'chat'.

    Args:
        user_message: the raw user text from the chat panel.
        run_llm_fn:   the run_llm callable from llm_worker.py.

    Returns:
        'concrete' | 'abstract' | 'question' | 'chat'
        Falls back to 'abstract' on any error so the pipeline always runs.
    """
    msgs = [
        {"role": "system", "content": CLASSIFIER_PROMPT},
        {"role": "user",   "content": user_message},
    ]
    full_prompt = CLASSIFIER_PROMPT + "\n\n" + user_message
    try:
        result = run_llm_fn(msgs, full_prompt)
        if not result:
            return "abstract"
        # Accept the first word only, upper-cased
        label = result.strip().upper().split()[0].rstrip(".,;:")
        if label in ("CONCRETE", "ABSTRACT", "QUESTION", "CHAT"):
            preview = user_message[:60]
            print(f"[CLASSIFIER] '{preview}' → {label}")
            return label.lower()
    except Exception as exc:
        print(f"[CLASSIFIER] Failed: {exc} — defaulting to abstract")
    return "abstract"

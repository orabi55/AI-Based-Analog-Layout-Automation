"""
Strategy Selector Node
======================
A LangGraph node that generates high-level placement strategies from 
topology analysis and user requests using an LLM.

Functions:
- node_strategy_selector: Prompts the LLM for strategies and updates the chat history.
  - Inputs: state (dict)
  - Outputs: strategy result and updated chat history.
"""

import time
import ai_agent.agents.strategy_selector as strategy_selector
from ai_agent.nodes._shared import (
    _build_llm_messages,
    _invoke_with_retry,
    _split_content_and_thinking,
    _strip_thinking_text,
    _print_thinking_block,
    _update_and_save_chat_history,
    vprint,
    ip_step,
)


def node_strategy_selector(state):
    t0 = time.time()
    vprint("\n" + "═" * 60, flush=True)
    vprint("  STAGE 2: STRATEGY SELECTOR", flush=True)
    vprint("═" * 60, flush=True)

    analysis_txt = state.get("Analysis_result", "")
    constraint_text = state.get("constraint_text", "")
    chat_history = state.get("chat_history", [])
    user_message = state.get("user_message", "Select a strategy based on the analysis.")
    selected_model = state.get("selected_model", "Gemini")

    # Avoid injecting a redundant system message into the chat history.

    strategy_prompt = _build_llm_messages(
        strategy_selector.STRATEGY_SELECTOR_PROMPT,
        [],
        f"User request: {user_message}\n\n"
        f"Analysis Result:\n{analysis_txt}\n\n"
        f"Layout Constraints:\n{constraint_text}\n\n",
    )
    vprint(f"[STRATEGY] Calling LLM ({selected_model}, weight=light)...", flush=True)

    try:
        strategy_response = _invoke_with_retry(strategy_prompt, selected_model, "light", "STRATEGY")
        strategy_text, strategy_thinking = _split_content_and_thinking(strategy_response.content)
        strategy_text = _strip_thinking_text(strategy_text)
        _print_thinking_block("STRATEGY", strategy_thinking)
    except Exception as exc:
        vprint(f"[STRATEGY] ✗ LLM failed: {exc}", flush=True)
        strategy_text = ""

    updated_chat_history = _update_and_save_chat_history(
        chat_history=chat_history, user_content=user_message,
    )

    elapsed = time.time() - t0
    nchar = len(strategy_text) if strategy_text else 0
    if strategy_text:
        ip_step("2/5 Strategy Selector", f"ok ({elapsed:.1f}s, {nchar} chars)")
    else:
        ip_step("2/5 Strategy Selector", f"no strategies ({elapsed:.1f}s)")

    return {
        "strategy_result": strategy_text,
        "chat_history": updated_chat_history,
    }

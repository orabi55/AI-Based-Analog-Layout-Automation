"""
ai_agent/ai_chat_bot/run_llm.py
================================
Unified LLM interface — delegates to llm_factory for model instantiation.

Provides automatic retry with exponential backoff for transient API errors
(429 RESOURCE_EXHAUSTED, 503 UNAVAILABLE) so a single hiccup does not
crash the multi-agent pipeline.
"""

import os
import time
import re
import json

# ─────────────────────────────────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────────────────────────────────

def _parse_retry_delay(exc: Exception) -> float:
    """Extract retryDelay seconds from a 429 ClientError response body."""
    try:
        if (
            hasattr(exc, "args")
            and len(exc.args) > 0
            and isinstance(exc.args[0], dict)
        ):
            details = exc.args[0].get("error", {}).get("details", [])
            for detail in details:
                if detail.get("@type", "").endswith("RetryInfo"):
                    delay_str = detail.get("retryDelay", "2s")
                    return float(re.sub(r"[^0-9.]", "", delay_str))
    except (AttributeError, KeyError, TypeError, ValueError):
        pass  # Non-standard error format; fall through to regex fallback

    delay_match = re.search(
        r"retry in ([\d.]+)s", str(exc), re.IGNORECASE
    )
    if delay_match:
        return float(delay_match.group(1))

    return 2.0


def _build_transcript_prompt(chat_messages, full_prompt):
    """Build a single JSON payload from chat messages.

    Returns:
        tuple[str, str]: (system_text, user_text)
    """
    conversation = []
    for cm in chat_messages or []:
        if not isinstance(cm, dict):
            continue
        role = str(cm.get("role", "")).strip()
        content = str(cm.get("content", "")).strip()
        if not role or not content:
            continue
        conversation.append({"role": role, "content": content})

    if not conversation:
        fallback = str(full_prompt or "").strip()
        if fallback:
            conversation = [{"role": "user", "content": fallback}]

    payload = {"conversation": conversation}
    user_text = json.dumps(payload, ensure_ascii=False, indent=2)
    system_text = ""
    return system_text, user_text


# ─────────────────────────────────────────────────────────────────────
# Main LLM interface (delegates to llm_factory)
# ─────────────────────────────────────────────────────────────────────

def run_llm(chat_messages, full_prompt, selected_model="Gemini", task_weight="light"):
    """Execute the chosen LLM request and return the reply text.

    Includes automatic retry with exponential backoff for transient
    API errors (429 RESOURCE_EXHAUSTED, 503 UNAVAILABLE).

    Args:
        chat_messages: list of {"role": ..., "content": ...} dicts
        full_prompt:   complete prompt string for single-turn APIs
        selected_model: 'Gemini' | 'Alibaba' | 'VertexGemini' | 'VertexClaude'
        task_weight:    'light' or 'heavy' — used to dynamically pick the optimal model

    Returns:
        str: the LLM reply text
    """
    MAX_RETRIES = 3
    BACKOFF_BASE = 2  # seconds

    print(f"\n{'='*60}", flush=True)
    print(f"[RUN_LLM] ▶ Request | model={selected_model} | weight={task_weight} | msgs={len(chat_messages)} | prompt_len={len(full_prompt)}", flush=True)
    print(f"{'='*60}", flush=True)

    last_result = "Error: All retries failed."
    for attempt in range(1, MAX_RETRIES + 1):
        result = _run_llm_once(chat_messages, full_prompt, selected_model, task_weight)

        # Check for transient errors worth retrying
        is_transient = (
            result.startswith("Error: Rate Limited")
            or ("429" in result and "RESOURCE_EXHAUSTED" in result)
            or ("503" in result and "UNAVAILABLE" in result)
            or ("503" in result and "high demand" in result.lower())
        )
        if is_transient and attempt < MAX_RETRIES:
            wait = BACKOFF_BASE ** attempt
            print(f"[RUN_LLM] ⚠ Transient error on attempt {attempt}/{MAX_RETRIES}, "
                  f"retrying in {wait}s...", flush=True)
            time.sleep(wait)
            continue

        if result.startswith("Error:") or result.startswith("Gemini Error:"):
            print(f"[RUN_LLM] ✗ Failed: {result[:120]}", flush=True)
        else:
            preview = result[:150].replace('\n', ' ')
            print(f"[RUN_LLM] ✓ Got {len(result)} chars: \"{preview}...\"", flush=True)
        return result

    return last_result


def _run_llm_once(chat_messages, full_prompt, selected_model, task_weight="light"):
    """Single-shot LLM call — delegates to llm_factory for model instantiation."""
    try:
        from ai_agent.ai_chat_bot.llm_factory import get_langchain_llm

        # Build LangChain-compatible messages
        lc_messages = []
        for cm in (chat_messages or []):
            role = cm.get("role", "user")
            content = cm.get("content", "")
            if role not in ("system", "user", "assistant"):
                role = "user"
            if content:
                lc_messages.append({"role": role, "content": content})

        if not lc_messages:
            lc_messages = [{"role": "user", "content": full_prompt or "Hello"}]

        print(f"[RUN_LLM]   Building LangChain model via factory...", flush=True)
        llm = get_langchain_llm(selected_model, task_weight)

        t_start = time.time()
        response = llm.invoke(lc_messages)
        elapsed = time.time() - t_start
        print(f"[RUN_LLM]   LLM responded in {elapsed:.1f}s", flush=True)

        if response and hasattr(response, "content") and response.content:
            return response.content.strip()
        return "Error: LLM returned an empty response."

    except Exception as e:
        err_str = str(e)
        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
            return f"Error: Rate Limited (429). Please wait a minute before trying again."
        if "503" in err_str or "UNAVAILABLE" in err_str:
            return f"503 UNAVAILABLE: {err_str}"
        return f"Error ({selected_model}): {err_str}"
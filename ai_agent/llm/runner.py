"""
Unified LLM Runner
==================
Provides a unified interface for executing LLM requests with automatic retries 
and exponential backoff for transient errors.

Functions:
- _parse_retry_delay: Extracts the retry delay from an exception response.
- _build_transcript_prompt: Formats chat messages into a single transcript.
- run_llm: Executes an LLM request with retry logic.
  - Inputs: chat_messages (list), full_prompt (str), selected_model (str), task_weight (str)
  - Outputs: LLM reply text (str).
- _run_llm_once: Performs a single shot LLM call via the factory.
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
# Main LLM interface (delegates to llm.factory)
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


def _extract_text_from_response(response) -> str:
    """Pull text content out of an AIMessage / AIMessageChunk."""
    if response is None:
        return ""
    content = getattr(response, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content)


def stream_llm(lc_messages, llm, message_id: str, worker):
    """Stream LLM output, emitting `worker.response_delta` for each chunk.

    Signals fired (all on `worker`):
        response_delta(message_id, delta_text) — for each text chunk
        response_done (message_id, full_text)  — once at the end

    Args:
        lc_messages: LangChain-style messages (list of dicts).
        llm:         LangChain BaseChatModel-compatible object.
        message_id:  Unique identifier for this response stream.
        worker:      QObject exposing the new streaming signals.

    Returns:
        tuple[str, Any]: (full_text, accumulated_response)
        accumulated_response is the merged AIMessage (or None on hard failure)
        — useful for downstream tool_call extraction.

    Behaviour:
        - If `llm.stream` exists, iterates chunks and emits per-chunk deltas.
        - If streaming raises ANY exception, silently falls back to llm.invoke()
          and emits the full text in a single response_delta.
        - Always emits response_done before returning. Never raises.
    """
    full_text = ""
    accumulated = None
    streamed_ok = False

    if hasattr(llm, "stream"):
        try:
            for chunk in llm.stream(lc_messages):
                # Merge chunks for downstream tool_call extraction
                if accumulated is None:
                    accumulated = chunk
                else:
                    try:
                        accumulated = accumulated + chunk
                    except Exception:
                        # Some providers don't support __add__; keep the latest
                        accumulated = chunk
                # Emit per-chunk text delta
                delta = _extract_text_from_response(chunk)
                if delta:
                    try:
                        worker.response_delta.emit(message_id, delta)
                    except Exception:
                        pass
                    full_text += delta
            streamed_ok = True
        except Exception as exc:
            print(f"[stream_llm] streaming failed ({type(exc).__name__}: {exc}); "
                  f"falling back to invoke()", flush=True)
            full_text = ""
            accumulated = None
            streamed_ok = False

    if not streamed_ok:
        # One-shot fallback path
        try:
            response = llm.invoke(lc_messages)
            accumulated = response
            full_text = _extract_text_from_response(response)
            if full_text:
                try:
                    worker.response_delta.emit(message_id, full_text)
                except Exception:
                    pass
        except Exception as exc:
            print(f"[stream_llm] invoke() also failed: {exc}", flush=True)
            full_text = f"Error: {exc}"
            try:
                worker.response_delta.emit(message_id, full_text)
            except Exception:
                pass

    try:
        worker.response_done.emit(message_id, full_text)
    except Exception:
        pass

    return full_text, accumulated


def _run_llm_once(chat_messages, full_prompt, selected_model, task_weight="light"):
    """Single-shot LLM call — delegates to llm.factory for model instantiation."""
    try:
        from ai_agent.llm.factory import get_langchain_llm

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

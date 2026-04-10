import os
import time
import re
import json

# -----------------------------------------------------------------
# Module-level helper — pure Python, no Qt, reusable by Orchestrator
# -----------------------------------------------------------------

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
    except Exception:
        pass

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

    # Keep system text empty so the entire prompt content is the JSON payload.
    system_text = ""
    return system_text, user_text

#-----------------------------------------------------------------
# Main LLM interface function
#`-----------------------------------------------------------------`

def run_llm(chat_messages, full_prompt):
    """Execute the cascading LLM request and return the reply text.

    Args:
        chat_messages: list of {"role": ..., "content": ...} dicts
        full_prompt:   complete prompt string for single-turn APIs

    Returns:
        str: the LLM reply text

    Raises:
        RuntimeError: if all backends fail
    """
    errors = []
    print(
        f"[LLM] run_llm: {len(chat_messages)} msgs, "
        f"prompt={len(full_prompt)} chars"
    )

    # ---- 1. Gemini ----
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if gemini_key:
        gemini_models = [
            "gemma-3-27b-it",
        ]
        _gemini_key_invalid = False
        for model_name in gemini_models:
            if _gemini_key_invalid:
                break
            for attempt in range(3):
                try:
                    from google import genai
                    from google.genai import types as genai_types

                    client = genai.Client(api_key=gemini_key)
                    sys_text, user_text = _build_transcript_prompt(
                        chat_messages,
                        full_prompt,
                    )

                    config_kwargs = {
                        "max_output_tokens": 4096,
                        "temperature":       0.4,
                    }

                    if "gemma" in model_name.lower():
                        if sys_text:
                            user_text = f"{sys_text}\n\n{user_text}"
                    else:
                        config_kwargs["system_instruction"] = sys_text or None

                    response = client.models.generate_content(
                        model    = model_name,
                        contents = user_text,
                        config   = genai_types.GenerateContentConfig(
                            **config_kwargs
                        ),
                    )

                    print("################ LLM Prompt ################")
                    print(user_text)
                    print("##########################################")
                    print("################ LLM Response ################")
                    print(response.text if response else "[No response]")
                    print("##########################################")

                    reply_text = None

                    if response:
                        reply_text = getattr(response, "text", None)

                        # Fallback if empty
                        if not reply_text:
                            candidates = getattr(response, "candidates", None)

                            if candidates and len(candidates) > 0:
                                content = getattr(candidates[0], "content", None)

                                if content:
                                    parts = getattr(content, "parts", None)

                                    if parts:
                                        texts = []
                                        for p in parts:
                                            text = getattr(p, "text", None)
                                            if isinstance(text, str):
                                                texts.append(text)

                                        if texts:
                                            reply_text = "".join(texts)
                          

                    if reply_text and reply_text.strip():
                        print(f"[LLM] Gemini/{model_name}")
                        return reply_text.strip()
                    else:
                        errors.append(
                            f"Gemini/{model_name}: empty response"
                        )
                        break

                except Exception as e:
                    import traceback
                    e_str   = str(e)
                    err_str = (
                        f"[{type(e).__name__}] {e}\n"
                        f"{traceback.format_exc()}"
                    )
                    if any(
                        k in e_str
                        for k in (
                            "API_KEY_INVALID",
                            "API key not valid",
                            "401",
                            "403",
                            "PERMISSION_DENIED",
                            "invalid api key",
                            "could not validate",
                        )
                    ):
                        errors.append(f"Gemini: API key invalid – {e}")
                        _gemini_key_invalid = True
                        break
                    if "429" in e_str or "RESOURCE_EXHAUSTED" in e_str:
                        retry_s = _parse_retry_delay(e)
                        wait    = min(retry_s + 2.0, 120.0)
                        if attempt < 2:
                            print(
                                f"[LLM] Gemini/{model_name} rate-limited "
                                f"(retry in {retry_s:.1f}s). "
                                f"Waiting {wait:.1f}s..."
                            )
                            time.sleep(wait)
                            continue
                    errors.append(f"Gemini/{model_name}: {err_str}")
                    break
    else:
        errors.append("Gemini: GEMINI_API_KEY not set")

    # ---- All models failed ----
    summary = "\n".join(f"  * {e}" for e in errors)
    print(
        f"[LLM] All models failed. "
        f"Falling back to prescriptive logic.\n{summary}"
    )
    return (
        "I'm having trouble connecting to my AI backend right now. "
        "Please check your API key in the `.env` file and try again. "
        "In the meantime, I can still execute direct commands like "
        "**swap**, **move**, and **add dummy** if you type them!"
    )
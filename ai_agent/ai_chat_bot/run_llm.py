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
def run_llm(chat_messages, full_prompt, selected_model="Gemini", ollama_model="llama3.2"):
    """Execute the chosen LLM request and return the reply text.

    Includes automatic retry with exponential backoff for transient
    API errors (429 RESOURCE_EXHAUSTED, 503 UNAVAILABLE) so that a
    single hiccup does not crash the multi-agent pipeline.

    Args:
        chat_messages: list of {"role": ..., "content": ...} dicts
        full_prompt:   complete prompt string for single-turn APIs
        selected_model: 'Gemini' | 'OpenAI' | 'Ollama' | 'Groq' | 'DeepSeek'
        ollama_model:   name of the local Ollama model to use

    Returns:
        str: the LLM reply text
    """
    MAX_RETRIES = 3
    BACKOFF_BASE = 2  # seconds

    print(f"[LLM] run_llm: model={selected_model}, msgs={len(chat_messages)}, prompt={len(full_prompt)}")

    last_result = "Error: All retries failed."
    for attempt in range(1, MAX_RETRIES + 1):
        result = _run_llm_once(chat_messages, full_prompt, selected_model, ollama_model)

        # Check for transient errors worth retrying
        # Fix Bug #3: Explicit parentheses for operator precedence
        is_transient = (
            result.startswith("Gemini Error: Rate Limited")
            or ("429" in result and "RESOURCE_EXHAUSTED" in result)
            or ("503" in result and "UNAVAILABLE" in result)
            or ("503" in result and "high demand" in result.lower())
        )
        if is_transient and attempt < MAX_RETRIES:
            wait = BACKOFF_BASE ** attempt
            print(f"[LLM] Transient error on attempt {attempt}/{MAX_RETRIES}, "
                  f"retrying in {wait}s...")
            time.sleep(wait)
            continue

        return result

    return last_result


def _run_llm_once(chat_messages, full_prompt, selected_model, ollama_model="llama3.2"):
    """Single-shot LLM call (no retries)."""

    if selected_model == "Gemini":
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        if not gemini_key:
            return "Error: GEMINI_API_KEY not set. Please update the API key in the Model Selection tool."

        try:
            from google import genai
            from google.genai import types as genai_types

            client = genai.Client(api_key=gemini_key)
            
            # Detect if we should use the JSON transcript format (for LangGraph nodes)
            # or the standard chat format (for UI chat).
            is_pipeline = any("topology" in str(m.get("content", "")).lower() for m in chat_messages)
            
            if is_pipeline:
                sys_text, user_text = _build_transcript_prompt(chat_messages, full_prompt)
            else:
                sys_text = ""
                conv_parts = []
                for cm in chat_messages:
                    if cm["role"] == "system":
                        sys_text = cm["content"]
                    else:
                        conv_parts.append(cm["content"])
                user_text = "\n".join(conv_parts) if conv_parts else full_prompt

            config_kwargs = {
                "max_output_tokens": 4096,
                "temperature": 0.4,
                "system_instruction": sys_text or None
            }

            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=user_text,
                config=genai_types.GenerateContentConfig(**config_kwargs),
            )

            if response and response.text:
                return response.text.strip()
            return "Error: Gemini returned an empty response."
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                return "Gemini Error: Rate Limited (429). Please wait a minute before trying again."
            if "503" in err_str or "UNAVAILABLE" in err_str:
                return f"503 UNAVAILABLE: {err_str}"
            return f"Gemini Error: {err_str}"

    elif selected_model == "OpenAI":
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        if not openai_key:
            return "Error: OPENAI_API_KEY not set."

        try:
            from openai import OpenAI
            client = OpenAI(api_key=openai_key)

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=chat_messages,
                temperature=0.4,
                max_tokens=4096
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"OpenAI Error: {str(e)}"

    elif selected_model == "Ollama":
        try:
            import requests
            response = requests.post(
                "http://localhost:11434/api/chat",
                json={
                    "model": ollama_model,
                    "messages": chat_messages,
                    "stream": False
                },
                timeout=300
            )
            if response.status_code != 200:
                err_msg = response.text
                try:
                    err_json = response.json()
                    err_msg = err_json.get("error", err_msg)
                except Exception:
                    pass
                return f"Ollama API Error ({response.status_code}): {err_msg}"
            return response.json().get("message", {}).get("content", "").strip()
        except Exception as e:
            return f"Ollama Error: Could not connect to local server on port 11434. ({str(e)})"

    elif selected_model == "Groq":
        groq_key = os.environ.get("GROQ_API_KEY", "")
        if not groq_key:
            return "Error: GROQ_API_KEY not set."

        try:
            from groq import Groq as GroqClient
            client = GroqClient(api_key=groq_key)
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=chat_messages,
                temperature=0.4,
                max_tokens=4096
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"Groq Error: {str(e)}"

    elif selected_model == "DeepSeek":
        deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not deepseek_key:
            return "Error: DEEPSEEK_API_KEY not set."

        try:
            from openai import OpenAI
            client = OpenAI(api_key=deepseek_key, base_url="https://api.deepseek.com")
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=chat_messages,
                temperature=0.4,
                max_tokens=4096
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"DeepSeek Error: {str(e)}"

    elif selected_model == "Alibaba":
        alibaba_key = os.environ.get("ALIBABA_API_KEY", "")
        if not alibaba_key:
            return "Error: ALIBABA_API_KEY not set. Please enter it in the Model Selection dialog."
        try:
            from openai import OpenAI
            client = OpenAI(
                api_key=alibaba_key,
                base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            )
            # Build messages for Qwen (same OpenAI format)
            qwen_messages = []
            for cm in chat_messages:
                role = cm.get("role", "user")
                content = cm.get("content", "")
                if role not in ("system", "user", "assistant"):
                    role = "user"
                if content:
                    qwen_messages.append({"role": role, "content": content})
            if not qwen_messages:
                qwen_messages = [{"role": "user", "content": full_prompt or "Hello"}]
            response = client.chat.completions.create(
                model="qwen-plus",
                messages=qwen_messages,
                temperature=0.4,
                max_tokens=4096,
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as e:
            return f"Alibaba Qwen Error: {str(e)}"

    elif selected_model == "VertexGemini":
        project_id = os.environ.get("VERTEX_PROJECT_ID", "")
        location = os.environ.get("VERTEX_LOCATION", "us-central1")
        if not project_id:
            return "Error: VERTEX_PROJECT_ID not set. Please configure it in the Model Selection dialog."

        try:
            from google import genai
            from google.genai import types as genai_types

            client = genai.Client(
                vertexai=True, project=project_id, location=location
            )

            # Detect if we should use the JSON transcript format
            is_pipeline = any(
                "topology" in str(m.get("content", "")).lower()
                for m in chat_messages
            )

            if is_pipeline:
                sys_text, user_text = _build_transcript_prompt(
                    chat_messages, full_prompt
                )
            else:
                sys_text = ""
                conv_parts = []
                for cm in chat_messages:
                    if cm["role"] == "system":
                        sys_text = cm["content"]
                    else:
                        conv_parts.append(cm["content"])
                user_text = "\n".join(conv_parts) if conv_parts else full_prompt

            config_kwargs = {
                "max_output_tokens": 4096,
                "temperature": 0.4,
                "system_instruction": sys_text or None,
            }

            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=user_text,
                config=genai_types.GenerateContentConfig(**config_kwargs),
            )

            if response and response.text:
                return response.text.strip()
            return "Error: Vertex AI Gemini returned an empty response."
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                return "Vertex AI Gemini Error: Rate Limited (429). Please wait before trying again."
            if "503" in err_str or "UNAVAILABLE" in err_str:
                return f"503 UNAVAILABLE: {err_str}"
            return f"Vertex AI Gemini Error: {err_str}"

    elif selected_model == "VertexClaude":
        project_id = os.environ.get("VERTEX_PROJECT_ID", "")
        location = os.environ.get("VERTEX_LOCATION", "us-east5")
        if not project_id:
            return "Error: VERTEX_PROJECT_ID not set. Please configure it in the Model Selection dialog."

        try:
            from anthropic import AnthropicVertex

            client = AnthropicVertex(
                region=location, project_id=project_id
            )

            # Build messages for Claude format
            sys_text = ""
            claude_messages = []
            for cm in chat_messages:
                if cm["role"] == "system":
                    sys_text = cm["content"]
                else:
                    claude_messages.append(
                        {"role": cm["role"], "content": cm["content"]}
                    )

            if not claude_messages:
                claude_messages = [
                    {"role": "user", "content": full_prompt or "Hello"}
                ]

            kwargs = {
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 4096,
                "messages": claude_messages,
            }
            if sys_text:
                kwargs["system"] = sys_text

            message = client.messages.create(**kwargs)

            if message and message.content:
                return message.content[0].text.strip()
            return "Error: Vertex AI Claude returned an empty response."
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                return "Vertex AI Claude Error: Rate Limited (429). Please wait before trying again."
            return f"Vertex AI Claude Error: {err_str}"

    return f"Error: Unsupported model '{selected_model}'"
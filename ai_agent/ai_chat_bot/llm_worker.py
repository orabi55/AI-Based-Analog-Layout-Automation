"""
LLM Worker using the Worker-Object Pattern (QThread + QObject).

Handles all LLM API calls in a dedicated QThread, communicating
with the GUI exclusively via Qt Signals and Slots.

Multi-Agent Architecture (LayoutCopilot):
    Uses MultiAgentOrchestrator to route user messages through
    specialised agents: Classifier -> Analyzer -> Refiner ->
    Adapter -> CodeGen.
"""

import os
import re
from pathlib import Path
from dotenv import load_dotenv
from PySide6.QtCore import QObject, Signal, Slot

from ai_agent.ai_chat_bot.agents.orchestrator import MultiAgentOrchestrator

# Load .env from the project root so API keys are available
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_path)


# -----------------------------------------------------------------
# Utility: resolve the correct .sp file for a given layout context
# -----------------------------------------------------------------
def _resolve_sp_file(layout_context: dict, project_root: Path) -> str | None:
    """Resolve the correct SPICE file for the current layout."""
    explicit = layout_context.get("sp_file_path", "")
    if explicit and Path(explicit).is_file():
        return explicit

    cell_name = layout_context.get("cell_name", "")
    if cell_name:
        all_sp = list(project_root.glob("*.sp"))
        for sp in all_sp:
            if cell_name.lower() in sp.stem.lower():
                return str(sp)

    all_sp = list(project_root.glob("*.sp"))
    if all_sp:
        all_sp_sorted = sorted(all_sp, key=lambda p: p.stat().st_mtime, reverse=True)
        return str(all_sp_sorted[0])

    return None


# -----------------------------------------------------------------
# Backward-compatible build_system_prompt (kept for any external
# callers; the new agents use their own prompts from prompts.py)
# -----------------------------------------------------------------
def build_system_prompt(layout_context):
    """Build a system prompt that includes layout context.

    NOTE: This is the legacy monolithic prompt. The multi-agent
    pipeline uses individual prompts from agents/prompts.py instead.
    """
    from ai_agent.ai_chat_bot.agents.prompts import build_chat_prompt
    return build_chat_prompt(layout_context)


# -----------------------------------------------------------------
# Module-level helper — pure Python, no Qt
# -----------------------------------------------------------------
def run_llm(chat_messages, full_prompt, selected_model, ollama_model="llama3.2"):
    """Execute the chosen LLM request and return the reply text.

    Includes automatic retry with exponential backoff for transient
    API errors (429 RESOURCE_EXHAUSTED, 503 UNAVAILABLE) so that a
    single hiccup does not crash the multi-agent pipeline.
    """
    import time as _time

    MAX_RETRIES = 3
    BACKOFF_BASE = 2  # seconds

    print(f"[LLM] run_llm: model={selected_model}, msgs={len(chat_messages)}, prompt={len(full_prompt)}")

    for attempt in range(1, MAX_RETRIES + 1):
        result = _run_llm_once(chat_messages, full_prompt, selected_model, ollama_model)

        # Check for transient errors worth retrying
        is_transient = (
            result.startswith("Gemini Error: Rate Limited")
            or "429" in result and "RESOURCE_EXHAUSTED" in result
            or "503" in result and "UNAVAILABLE" in result
            or "503" in result and "high demand" in result.lower()
        )
        if is_transient and attempt < MAX_RETRIES:
            wait = BACKOFF_BASE ** attempt
            print(f"[LLM] Transient error on attempt {attempt}/{MAX_RETRIES}, "
                  f"retrying in {wait}s...")
            _time.sleep(wait)
            continue

        return result

    return result  # return last result even if still an error


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
            return "Error: OPENAI_API_KEY not set. Please update the API key in the Model Selection tool."

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

        try:
            import requests
            response = requests.post(
                "http://localhost:11434/api/chat",
                json={
                    "model": ollama_model,
                    "messages": chat_messages,
                    "stream": False
                }
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
            return f"Ollama Error: Could not connect or generate response. ({str(e)})\nEnsure 'ollama serve' is running locally on port 11434."

    return f"Error: Unknown model selected ('{selected_model}')."


# -----------------------------------------------------------------
# Worker QObject — Multi-Agent Pipeline
# -----------------------------------------------------------------
class LLMWorker(QObject):
    """Worker object that performs LLM API calls on a background QThread.

    Uses the MultiAgentOrchestrator to route requests through the
    LayoutCopilot pipeline (Classifier -> Analyzer -> Refiner ->
    Adapter -> CodeGen).
    """

    response_ready = Signal(str)
    command_ready  = Signal(dict)
    error_occurred = Signal(str)

    def __init__(self):
        super().__init__()
        self._orchestrator = MultiAgentOrchestrator()

    @Slot(str, list, str, str)
    def process_request(self, full_prompt, chat_messages, selected_model, ollama_model):
        """Execute the multi-agent pipeline.

        Args:
            full_prompt:    complete prompt string (contains system + user).
            chat_messages:  list of {"role", "content"} dicts.
            selected_model: 'Gemini' | 'OpenAI' | 'Ollama'
            ollama_model:   local ollama sub-model (e.g. 'llama3.2', 'qwen2.5')
        """
        try:
            # Extract the user message (last user entry in chat_messages)
            user_message = ""
            layout_context = None

            for msg in reversed(chat_messages):
                if msg.get("role") == "user":
                    user_message = msg["content"]
                    break

            if not user_message:
                user_message = full_prompt

            # The layout_context is stored by ChatPanel and injected
            # into the system prompt. We parse it from there for the
            # orchestrator. The ChatPanel sets _layout_context on us
            # via set_layout_context().
            layout_context = getattr(self, '_layout_context', None)

            result = self._orchestrator.process(
                user_message=user_message,
                layout_context=layout_context,
                chat_history=chat_messages,
                run_llm_fn=lambda sys, msg, sel: run_llm(sys, msg, sel, ollama_model),
                selected_model=selected_model,
            )

            reply = result.get("reply", "")
            commands = result.get("commands", [])
            
            if reply:
                self.response_ready.emit(reply)
            else:
                self.response_ready.emit(
                    "I processed your request but had nothing to say. "
                    "Could you try rephrasing?"
                )

            for cmd in commands:
                self.command_ready.emit(cmd)

        except Exception as exc:
            import traceback
            print(f"[LLM Worker] Error:\n{traceback.format_exc()}")
            self.error_occurred.emit(f"Unexpected error: {exc}")

    def set_layout_context(self, context: dict | None):
        """Store layout context for the orchestrator to use."""
        self._layout_context = context

    def reset_pipeline(self):
        """Reset the orchestrator state (e.g. when chat is cleared)."""
        self._orchestrator.reset()

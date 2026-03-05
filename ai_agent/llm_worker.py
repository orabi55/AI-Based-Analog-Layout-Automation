"""
LLM Worker using the Worker-Object Pattern (QThread + QObject).

Handles all LLM API calls in a dedicated QThread, communicating
with the GUI exclusively via Qt Signals and Slots.

Cascading fallback order:
    1. Ollama  (local, no rate limits)
    2. Gemini  (multiple models)
    3. OpenAI  (multiple models)
    4. DeepSeek
"""

import os
from PySide6.QtCore import QObject, Signal, Slot


# -----------------------------------------------------------------
# Utility: build the system prompt that tells the LLM how to behave
# -----------------------------------------------------------------
def build_system_prompt(layout_context):
    """Build a system prompt that includes layout context.

    Args:
        layout_context: dict with 'nodes' and optionally 'edges',
                        or None if no layout is loaded yet.
    Returns:
        A string suitable for the system / preamble role.
    """
    prompt = (
        "You are an AI assistant embedded in an Analog Layout Editor. "
        "You help the user understand and optimize their circuit placement.\n\n"
        "RULES:\n"
        "1. Keep responses SHORT and conversational (1-3 sentences). "
        "NEVER output the full JSON layout.\n"
        "2. When the user asks you to perform an action (swap, move devices), "
        "include a command tag in your response using this exact format:\n"
        '   [CMD]{"action": "swap", "device_a": "ID1", "device_b": "ID2"}[/CMD]\n'
        '   [CMD]{"action": "move", "device": "ID", "x": 1.0, "y": 0.5}[/CMD]\n'
        "3. The command tag will be parsed and executed automatically. "
        "The user will NOT see the [CMD] block, only your conversational text.\n"
        "4. You may include multiple [CMD] blocks in one response if needed.\n"
        "5. Only use device IDs that exist in the layout data.\n"
        "6. You may use markdown formatting: **bold**, *italic*, "
        "- bullet lists, `code`.\n"
    )
    if layout_context:
        nodes = layout_context.get("nodes", [])
        edges = layout_context.get("edges", [])
        dev_lines = []
        for n in nodes:
            geo = n.get("geometry", {})
            dev_lines.append(
                f"  {n.get('id','?')} ({n.get('type','?')}) "
                f"at ({geo.get('x',0)}, {geo.get('y',0)})"
            )
        nets = sorted({e.get("net", "") for e in edges if e.get("net")})
        prompt += f"\nDevices ({len(nodes)}):\n" + "\n".join(dev_lines) + "\n"
        if nets:
            prompt += f"Nets: {', '.join(nets)}\n"
    return prompt


# -----------------------------------------------------------------
# Worker QObject — lives on a QThread, does blocking network I/O
# -----------------------------------------------------------------
class LLMWorker(QObject):
    """Worker object that performs LLM API calls.

    Usage (Worker-Object Pattern):
        thread = QThread()
        worker = LLMWorker()
        worker.moveToThread(thread)

        # wire signals
        some_signal.connect(worker.process_request)
        worker.response_ready.connect(some_slot)
        worker.error_occurred.connect(some_error_slot)

        thread.start()
    """

    # --- Signals emitted back to the GUI thread ---
    response_ready = Signal(str)
    error_occurred = Signal(str)

    # ----------------------------------------------------------
    @Slot(str, list)
    def process_request(self, full_prompt, chat_messages):
        """Execute a cascading LLM request (blocking).

        This slot is invoked from the GUI thread via a cross-thread
        signal connection; Qt guarantees it runs on *this* object's
        thread (the worker QThread).

        Args:
            full_prompt:    Complete prompt string for non-chat APIs
                            (Ollama, Gemini).
            chat_messages:  List of {"role": ..., "content": ...} dicts
                            for OpenAI-compatible chat APIs.
        """
        errors = []

        # ======================================================
        # Priority 1: Ollama (local, instant, no rate limits)
        # ======================================================
        print("Full prompt for LLM:\n" + full_prompt)
        try:
            import requests

            resp = requests.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": "llama3.2",
                    "prompt": full_prompt,
                    "stream": False,
                },
                timeout=120,
            )
            resp.raise_for_status()
            result = resp.json()
            reply = result.get("response", "")
            if reply:
                print("[LLM] ✓ Ollama/llama3.2 responded")
                self.response_ready.emit(reply.strip())
                return
        except Exception as e:
            errors.append(f"Ollama: {e}")
            print(f"[LLM] ✗ Ollama: {e}")

        # ======================================================
        # Priority 2: Gemini (multiple models)
        # ======================================================
        gemini_models = [
            "gemini-2.0-flash",
            "gemini-1.5-flash",
            "gemini-1.5-flash-8b",
        ]
        for model_name in gemini_models:
            try:
                from google import genai
                from google.genai import types as genai_types

                client = genai.Client(
                    api_key=os.environ.get("GEMINI_API_KEY", "")
                )
                response = client.models.generate_content(
                    model=model_name,
                    contents=full_prompt,
                    config=genai_types.GenerateContentConfig(
                        max_output_tokens=256,
                        temperature=0.3,
                    ),
                )
                if response and response.text:
                    print(f"[LLM] ✓ Gemini/{model_name} responded")
                    self.response_ready.emit(response.text.strip())
                    return
            except Exception as e:
                errors.append(f"Gemini/{model_name}: {e}")
                print(f"[LLM] ✗ Gemini/{model_name}: {e}")

        # ======================================================
        # Priority 3: OpenAI (multiple models)
        # ======================================================
        openai_models = ["gpt-4o-mini", "gpt-3.5-turbo"]
        for model_name in openai_models:
            try:
                from openai import OpenAI

                client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
                response = client.chat.completions.create(
                    model=model_name,
                    messages=chat_messages,
                    temperature=0.3,
                    max_tokens=256,
                )
                reply = response.choices[0].message.content
                if reply:
                    print(f"[LLM] ✓ OpenAI/{model_name} responded")
                    self.response_ready.emit(reply.strip())
                    return
            except Exception as e:
                errors.append(f"OpenAI/{model_name}: {e}")
                print(f"[LLM] ✗ OpenAI/{model_name}: {e}")

        # ======================================================
        # Priority 4: DeepSeek
        # ======================================================
        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
                base_url="https://api.deepseek.com",
            )
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=chat_messages,
                temperature=0.3,
                max_tokens=256,
            )
            reply = response.choices[0].message.content
            if reply:
                print("[LLM] ✓ DeepSeek/deepseek-chat responded")
                self.response_ready.emit(reply.strip())
                return
        except Exception as e:
            errors.append(f"DeepSeek: {e}")
            print(f"[LLM] ✗ DeepSeek: {e}")

        # All backends exhausted
        self.error_occurred.emit(
            "All AI models exhausted. Please wait a minute and try again."
        )

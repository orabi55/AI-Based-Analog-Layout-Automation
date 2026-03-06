"""
LLM Worker using the Worker-Object Pattern (QThread + QObject).

Handles all LLM API calls in a dedicated QThread, communicating
with the GUI exclusively via Qt Signals and Slots.

Cascading fallback order:
    1. Gemini   (best models down to 2.5-flash)
    2. Groq     (free tier, fast)
    3. OpenAI   (gpt-4o-mini, gpt-3.5-turbo)
    4. DeepSeek
    5. Ollama   (local fallback)
"""

import os
import re
import time
from pathlib import Path
from dotenv import load_dotenv
from PySide6.QtCore import QObject, Signal, Slot

# Load .env from the project root so API keys are available
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)


# -----------------------------------------------------------------
# Utility: build the system prompt that tells the LLM how to behave
# -----------------------------------------------------------------
def build_system_prompt(layout_context):
    """Build a system prompt that includes layout context.

    Args:
        layout_context: dict with 'nodes', optionally 'edges' and
                        'terminal_nets', or None if no layout loaded.
    Returns:
        A string suitable for the system / preamble role.
    """
    # ---- CMD protocol FIRST (most important — never gets truncated) ----
    prompt = (
        "RULE #1: For ANY action (swap/move/dummy), you MUST output a "
        "[CMD]{...}[/CMD] block. Without it nothing happens.\n"
        "Actions:\n"
        '[CMD]{"action":"swap","device_a":"MM28","device_b":"MM25"}[/CMD]\n'
        '[CMD]{"action":"move","device":"MM3","x":1.0,"y":0.5}[/CMD]\n'
        '[CMD]{"action":"add_dummy","type":"nmos","count":2,"side":"left"}[/CMD]\n'
        "Use full IDs (MM28 not 28). Multiple [CMD] blocks OK. "
        "add_dummy type=nmos|pmos, count defaults to 1, side=left|right (default left).\n"
        "Write the [CMD] block FIRST, then 1-2 sentences confirming.\n\n"
    )

    # ---- Identity (compact) ----
    prompt += (
        "You are an expert Analog IC Layout Engineer in a Symbolic "
        "Layout Editor. Expertise: CMOS matching, symmetry, current "
        "mirrors, diff-pairs, guard rings, dummies, parasitic-aware "
        "placement.\n\n"
    )

    # ---- Editor features (compact) ----
    prompt += (
        "Editor: Rows (PMOS top, NMOS bottom), auto-compacted by net "
        "adjacency. Toolbar: Undo/Redo, Swap(2 sel), Flip H/V, "
        "Merge SS/DD, Dummy mode(D), Delete, Zoom, Fit.\n"
        "Orientations: R0, R0_FH, R0_FV, R0_FH_FV.\n"
        "Dummies: edge devices for etch uniformity (is_dummy=true).\n\n"
    )

    # ---- Advice style (compact) ----
    prompt += (
        "Advice style: SHORT (1-3 sentences), actionable. "
        "Prioritise matched pairs, minimise routing via abutment, "
        "recommend dummies at row edges, ensure symmetry. "
        "Use markdown. NEVER dump full JSON.\n\n"
    )

    # ---- Live layout data ----
    if layout_context:
        nodes = layout_context.get("nodes", [])
        edges = layout_context.get("edges", [])
        terminal_nets = layout_context.get("terminal_nets", {})

        prompt += f"=== CURRENT LAYOUT ({len(nodes)} devices) ===\n"
        for n in nodes:
            nid = n.get("id", "?")
            ntype = n.get("type", "?")
            geo = n.get("geometry", {})
            elec = n.get("electrical", {})
            orient = geo.get("orientation", "R0")
            dummy_tag = " [DUMMY]" if n.get("is_dummy") else ""
            line = (
                f"  {nid} ({ntype}{dummy_tag}) "
                f"pos=({geo.get('x',0):.2f},{geo.get('y',0):.2f}) "
                f"orient={orient}"
            )
            # Add electrical params if present
            elec_parts = []
            for k in ("nf", "nfin", "l", "w"):
                if k in elec:
                    elec_parts.append(f"{k}={elec[k]}")
            if elec_parts:
                line += f" [{', '.join(elec_parts)}]"
            # Add terminal nets if available
            tnets = terminal_nets.get(nid, {})
            if tnets:
                parts = [f"{t}={tnets[t]}" for t in ("D", "G", "S") if t in tnets]
                line += f"  nets({', '.join(parts)})"
            prompt += line + "\n"

        # Summarise nets
        all_nets = sorted({e.get("net", "") for e in edges if e.get("net")})
        if all_nets:
            prompt += f"\nNets: {', '.join(all_nets)}\n"
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

        print(f"[LLM] Prompt length: {len(full_prompt)} chars, "
              f"chat_messages: {len(chat_messages)} msgs")

        # ======================================================
        # Priority 1: Gemini (best models, with retry on 429)
        # ======================================================
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        if gemini_key:
            gemini_models = [
               ## "gemini-3.1-flash-preview",
                ##"gemini-3.1-pro-preview",
                ##"gemini-2.5-pro-preview",
                ##"gemini-2.5-pro",
                "gemini-2.5-flash",
                "gemini-2.0-flash",
                "gemini-2.0-flash-lite",
                "gemini-1.5-pro",
                "gemini-1.5-flash",
            ]
            _gemini_key_invalid = False
            for model_name in gemini_models:
                if _gemini_key_invalid:
                    break  # don't retry other models with a bad key
                for attempt in range(3):  # up to 3 attempts per model
                    try:
                        from google import genai
                        from google.genai import types as genai_types

                        client = genai.Client(api_key=gemini_key)

                        # Split system prompt from conversation for
                        # proper Gemini system_instruction usage.
                        sys_text = ""
                        conv_parts = []
                        for cm in chat_messages:
                            if cm["role"] == "system":
                                sys_text = cm["content"]
                            else:
                                conv_parts.append(cm["content"])
                        user_text = "\n".join(conv_parts) if conv_parts else full_prompt

                        response = client.models.generate_content(
                            model=model_name,
                            contents=user_text,
                            config=genai_types.GenerateContentConfig(
                                system_instruction=sys_text or None,
                                max_output_tokens=8192,
                                temperature=0.3,
                            ),
                        )
                        # Inspect response thoroughly
                        reply_text = None
                        if response:
                            if hasattr(response, 'candidates') and response.candidates:
                                cand = response.candidates[0]
                                fr = getattr(cand, 'finish_reason', None)
                                if fr and str(fr) not in ('STOP', 'FinishReason.STOP', '1', 'None'):
                                    print(f"[LLM] ⚠ Gemini/{model_name} finish_reason={fr}")
                            try:
                                reply_text = response.text
                            except Exception:
                                if hasattr(response, 'candidates') and response.candidates:
                                    parts = response.candidates[0].content.parts
                                    if parts:
                                        reply_text = ''.join(
                                            p.text for p in parts if hasattr(p, 'text')
                                        )
                        if reply_text and reply_text.strip():
                            print(f"[LLM] ✓ Gemini/{model_name} responded "
                                  f"({len(reply_text)} chars)")
                            self.response_ready.emit(reply_text.strip())
                            return
                        else:
                            print(f"[LLM] ✗ Gemini/{model_name}: empty response")
                            errors.append(f"Gemini/{model_name}: empty response")
                            break  # try next model
                    except Exception as e:
                        err_str = str(e)

                        # API key invalid → skip ALL Gemini models
                        if any(k in err_str for k in (
                            "API_KEY_INVALID", "API key not valid",
                            "401", "403", "PERMISSION_DENIED",
                            "invalid api key", "could not validate",
                        )):
                            errors.append(f"Gemini: API key invalid – {e}")
                            print("[LLM] ✗ Gemini: API key invalid, skipping all Gemini models")
                            _gemini_key_invalid = True
                            break

                        # Rate limit (429) → retry with back-off
                        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                            delay_match = re.search(
                                r"retry in ([\d.]+)s", err_str, re.IGNORECASE
                            )
                            if not delay_match:
                                delay_match = re.search(
                                    r"retryDelay.*?'(\d+)s'", err_str
                                )
                            wait_secs = (
                                min(float(delay_match.group(1)), 30)
                                if delay_match
                                else 10 * (attempt + 1)
                            )
                            if attempt < 2:
                                print(
                                    f"[LLM] ⏳ Gemini/{model_name} rate limited, "
                                    f"retrying in {wait_secs:.0f}s "
                                    f"(attempt {attempt + 1}/3)..."
                                )
                                time.sleep(wait_secs)
                                continue
                        errors.append(f"Gemini/{model_name}: {e}")
                        print(f"[LLM] ✗ Gemini/{model_name}: {e}")
                        break  # non-retryable error, try next model
        else:
            errors.append("Gemini: GEMINI_API_KEY not set")
            print("[LLM] ✗ Gemini: GEMINI_API_KEY not set")

        # ======================================================
        # Priority 2: Groq (free tier, fast)
        # ======================================================
        groq_key = os.environ.get("GROQ_API_KEY", "")
        if groq_key:
            groq_models = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
            for model_name in groq_models:
                try:
                    from openai import OpenAI

                    client = OpenAI(
                        api_key=groq_key,
                        base_url="https://api.groq.com/openai/v1",
                    )
                    response = client.chat.completions.create(
                        model=model_name,
                        messages=chat_messages,
                        temperature=0.3,
                        max_tokens=2048,
                    )
                    reply = response.choices[0].message.content
                    if reply:
                        print(f"[LLM] ✓ Groq/{model_name} responded")
                        self.response_ready.emit(reply.strip())
                        return
                except Exception as e:
                    errors.append(f"Groq/{model_name}: {e}")
                    print(f"[LLM] ✗ Groq/{model_name}: {e}")
        else:
            errors.append("Groq: GROQ_API_KEY not set")
            print("[LLM] ✗ Groq: GROQ_API_KEY not set (get free key at console.groq.com)")

        # ======================================================
        # Priority 3: OpenAI
        # ======================================================
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        if openai_key:
            openai_models = ["gpt-4o-mini", "gpt-3.5-turbo"]
            for model_name in openai_models:
                try:
                    from openai import OpenAI

                    client = OpenAI(api_key=openai_key)
                    response = client.chat.completions.create(
                        model=model_name,
                        messages=chat_messages,
                        temperature=0.3,
                        max_tokens=2048,
                    )
                    reply = response.choices[0].message.content
                    if reply:
                        print(f"[LLM] ✓ OpenAI/{model_name} responded")
                        self.response_ready.emit(reply.strip())
                        return
                except Exception as e:
                    errors.append(f"OpenAI/{model_name}: {e}")
                    print(f"[LLM] ✗ OpenAI/{model_name}: {e}")
        else:
            errors.append("OpenAI: OPENAI_API_KEY not set")
            print("[LLM] ✗ OpenAI: OPENAI_API_KEY not set")

        # ======================================================
        # Priority 4: DeepSeek
        # ======================================================
        deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if deepseek_key:
            try:
                from openai import OpenAI

                client = OpenAI(
                    api_key=deepseek_key,
                    base_url="https://api.deepseek.com",
                )
                response = client.chat.completions.create(
                    model="deepseek-chat",
                    messages=chat_messages,
                    temperature=0.3,
                    max_tokens=2048,
                )
                reply = response.choices[0].message.content
                if reply:
                    print("[LLM] ✓ DeepSeek/deepseek-chat responded")
                    self.response_ready.emit(reply.strip())
                    return
            except Exception as e:
                errors.append(f"DeepSeek: {e}")
                print(f"[LLM] ✗ DeepSeek: {e}")
        else:
            errors.append("DeepSeek: DEEPSEEK_API_KEY not set")
            print("[LLM] ✗ DeepSeek: DEEPSEEK_API_KEY not set")

        # ======================================================
        # Priority 5: Ollama (local fallback)
        # ======================================================
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

        # All backends exhausted
        summary = "\n".join(f"  • {e.split(':')[0]}" for e in errors)
        self.error_occurred.emit(
            f"All AI models failed. Tried:\n{summary}\n\n"
            "Fix: Add a valid API key in .env file.\n"
            "Free option: Get a Groq key at https://console.groq.com"
        )

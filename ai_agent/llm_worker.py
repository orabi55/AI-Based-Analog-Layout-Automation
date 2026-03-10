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

Multi-Agent additions:
    - run_llm(chat_messages, full_prompt) → str  (module-level, no Qt)
    - OrchestratorWorker subclass with process_orchestrated_request slot
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
# Module-level helper — pure Python, no Qt, reusable by Orchestrator
# -----------------------------------------------------------------
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
    print(f"[LLM] run_llm: {len(chat_messages)} msgs, prompt={len(full_prompt)} chars")

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
                    }
                    
                    # Gemma-3 rejects `system_instruction` in the config entirely.
                    if "gemma" in model_name.lower():
                        if sys_text:
                            user_text = f"{sys_text}\n\n{user_text}"
                    else:
                        config_kwargs["system_instruction"] = sys_text or None
                        
                    response = client.models.generate_content(
                        model=model_name,
                        contents=user_text,
                        config=genai_types.GenerateContentConfig(**config_kwargs),
                    )
                    reply_text = None
                    if response:
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
                        print(f"[LLM] ✓ Gemini/{model_name}")
                        return reply_text.strip()
                    else:
                        errors.append(f"Gemini/{model_name}: empty response")
                        break
                except Exception as e:
                    import traceback
                    e_str = str(e)  # Only check the exception message, not the traceback
                    err_str = f"[{type(e).__name__}] {e}\n{traceback.format_exc()}"
                    if any(k in e_str for k in (
                        "API_KEY_INVALID", "API key not valid",
                        "401", "403", "PERMISSION_DENIED",
                        "invalid api key", "could not validate",
                    )):
                        errors.append(f"Gemini: API key invalid – {e}")
                        _gemini_key_invalid = True
                        break
                    if "429" in e_str or "RESOURCE_EXHAUSTED" in e_str:
                        retry_s = _parse_retry_delay(e)
                        wait = min(retry_s + 2.0, 120.0)  # cap at 120s
                        if attempt < 2:
                            print(f"[LLM] ⏳ Gemini/{model_name} rate-limited "
                                  f"(retry in {retry_s:.1f}s per API). "
                                  f"Waiting {wait:.1f}s...")
                            time.sleep(wait)
                            continue
                    errors.append(f"Gemini/{model_name}: {err_str}")
                    break
    else:
        errors.append("Gemini: GEMINI_API_KEY not set")

    # ---- 2. Groq ----
    groq_key = os.environ.get("GROQ_API_KEY", "")

    summary = "\n".join(f"  • {e}" for e in errors)
    print(f"[LLM] All models failed. Falling back to prescriptive logic.\n{summary}")
    return ""
    return ""


def _parse_retry_delay(exc: Exception) -> float:
    """Extract retryDelay seconds from a 429 ClientError response body."""
    try:
        # For google.genai, the dictionary might be in exc.args[0]
        if hasattr(exc, 'args') and len(exc.args) > 0 and isinstance(exc.args[0], dict):
            details = exc.args[0].get('error', {}).get('details', [])
            for detail in details:
                if detail.get('@type', '').endswith('RetryInfo'):
                    delay_str = detail.get('retryDelay', '2s')
                    return float(re.sub(r'[^0-9.]', '', delay_str))
    except Exception:
        pass
    
    # Fallback to general error string
    delay_match = re.search(r"retry in ([\d.]+)s", str(exc), re.IGNORECASE)
    if delay_match:
        return float(delay_match.group(1))
        
    return 2.0   # safe default


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
        """Execute a cascading LLM request (blocking) via run_llm().

        This slot is invoked from the GUI thread via a cross-thread
        signal connection; Qt guarantees it runs on *this* object's
        thread (the worker QThread).

        Args:
            full_prompt:    Complete prompt string for non-chat APIs.
            chat_messages:  List of {"role": ..., "content": ...} dicts.
        """
        try:
            reply = run_llm(chat_messages, full_prompt)
            self.response_ready.emit(reply)
        except RuntimeError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Unexpected error: {exc}")

    # ------ keep old inline body unreachable below for reference ------
    # (deleted — now delegated to run_llm)


# -----------------------------------------------------------------
# OrchestratorWorker — drives the 4-stage multi-agent pipeline
# -----------------------------------------------------------------
class OrchestratorWorker(LLMWorker):
    """Extends LLMWorker with an orchestrated multi-agent slot.

    # NOTE: A new Orchestrator is created per request intentionally —
    # it is stateless. All human-in-the-loop state lives here on the
    # OrchestratorWorker and is passed into the Orchestrator as arguments.


    Extra signals compared to LLMWorker:
        stage_completed(int, str): emitted after each pipeline stage.
            int  = stage index (0=Analyst, 1=Placer, 2=DRC, 3=Router)
            str  = stage name
    """

    # stage_index (0-3), stage_name → received by main.py for canvas highlights
    stage_completed = Signal(int, str)
    
    # Emitted when Stage 1 completes so the UI can pause and ask the user
    topology_ready_for_review = Signal(str)

    def __init__(self):
        super().__init__()
        self.pending_topology = None
        self.pending_layout_context = None

    @Slot(str, str)
    def process_orchestrated_request(self, user_message, layout_context_json):
        """Run the full multi-agent pipeline (blocking).

        Args:
            user_message (str): the user's request from the chat panel.
            layout_context_json (str): JSON-serialised layout context dict.
        """
        import json as _json
        try:
            layout_context = _json.loads(layout_context_json)
        except (_json.JSONDecodeError, ValueError):
            layout_context = {}

        try:
            from ai_agent.orchestrator import Orchestrator
            from ai_agent.classifier_agent import classify_intent

            # Locate the SPICE file relative to the project root
            from pathlib import Path
            project_root = Path(__file__).resolve().parent.parent
            sp_candidates = sorted(project_root.glob("*.sp"))  # sorted = deterministic
            if len(sp_candidates) > 1:
                print(f"[ORCH] Warning: {len(sp_candidates)} .sp files found, "
                      f"using: {sp_candidates[0].name}")
            sp_file = str(sp_candidates[0]) if sp_candidates else None

            # Stage callback: emit stage_completed signal (safe cross-thread)
            def _on_stage(idx, name, data):
                try:
                    self.stage_completed.emit(idx, name)
                except Exception:
                    pass

            orch = Orchestrator(
                run_llm_fn=run_llm,
                sp_file_path=sp_file,
                gap_px=layout_context.get("gap_px", 0.0),
                max_drc_retries=2,
                stage_callback=_on_stage,
            )

            # ── Classifier routing ────────────────────────────────────
            # Only classify on FRESH requests (not pipeline resumes).
            is_resuming = self.pending_topology is not None
            if not is_resuming:
                intent = classify_intent(user_message, run_llm)
            else:
                intent = None  # resuming, skip classify

            if intent == "question":
                # QUESTION: answer without touching any pipeline stages.
                print("[ORCH] QUESTION intent → single-agent reply")
                # build_system_prompt is defined at module level in this file.
                system_prompt = build_system_prompt(layout_context)
                chat_msgs = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_message},
                ]
                reply = run_llm(chat_msgs, system_prompt + "\n" + user_message)
                self.response_ready.emit(reply)

            elif intent == "concrete":
                # CONCRETE: skip topology/strategy, go straight to Stage 2-4.
                print("[ORCH] CONCRETE intent → skipping to Stage 2")
                result = orch.continue_placement(
                    user_message, layout_context,
                    constraint_text="[Direct command — no topology analysis needed]",
                )
                self.response_ready.emit(result)

            elif is_resuming:
                # Resume from Stage 1 (user confirmed topology or chose strategy)
                constraint_text = self.pending_topology
                
                # If the user provided feedback like "No, this is X", append it
                # to the constraint text so the placement specialist sees it!
                if user_message and user_message.lower() not in ("yes", "y", "correct", "looks good", "ok", "go"):
                    constraint_text += f"\n[User Feedback/Corrections]\n{user_message}"
                
                self.pending_topology = None
                self.pending_layout_context = None
                
                result = orch.continue_placement(user_message, layout_context, constraint_text)
                self.response_ready.emit(result)

            else:
                # ABSTRACT (or default): full 4-stage pipeline with topology question.
                question, constraint_text = orch.run_topology_analysis(user_message, layout_context)
                
                # Save state for the next turn
                self.pending_topology = constraint_text
                self.pending_layout_context = layout_context
                
                # Signal the UI that we have a question (topology + strategy options)
                self.topology_ready_for_review.emit(question)


        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            print(f"[ORCH] Pipeline error:\n{tb}")
            self.error_occurred.emit(
                f"Orchestrator error: {exc}\n"
                "Falling back to single-agent mode."
            )
            # Graceful fallback: run as a normal single-agent request
            system_prompt = (
                "You are an expert analog IC layout engineer. "
                "Suggest improvements using [CMD] blocks."
            )
            chat_messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_message},
            ]
            try:
                reply = run_llm(chat_messages, f"{system_prompt}\n{user_message}")
                self.response_ready.emit(reply)
            except RuntimeError as inner_exc:
                self.error_occurred.emit(str(inner_exc))

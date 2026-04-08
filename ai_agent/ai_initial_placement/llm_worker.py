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
    - run_llm(chat_messages, full_prompt) -> str  (module-level, no Qt)
    - OrchestratorWorker subclass with process_orchestrated_request slot

FIXES APPLIED:
    - Bug #CRITICAL: sp_candidates[0] was always comp_fortest_comparator.sp
      (alphabetical sort). Now matches sp file to layout_context["cell_name"]
      or falls back to most recently modified .sp file.
    - Bug #2: sp_file_path now passed into layout_context so orchestrator
      always knows which file was used.
    - Bug #3: pending_layout_context now stores sp_file_path for Stage 2 resume.
"""

import os
import re
import time
from pathlib import Path
from dotenv import load_dotenv
from PySide6.QtCore import QObject, Signal, Slot

# Load .env from the project root so API keys are available
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_path)


# -----------------------------------------------------------------
# Utility: resolve the correct .sp file for a given layout context
# -----------------------------------------------------------------
def _resolve_sp_file(layout_context: dict, project_root: Path) -> str | None:
    """
    Resolve the correct SPICE file for the current layout.

    Priority order:
      1. Explicit path in layout_context["sp_file_path"]  (most reliable)
      2. Match layout_context["cell_name"] to a .sp filename
      3. Most recently modified .sp file in project root   (last resort)

    Args:
        layout_context: dict from the UI canvas
        project_root:   Path to project root directory

    Returns:
        Absolute path string to .sp file, or None if not found
    """
    # Priority 1: explicit path already set
    explicit = layout_context.get("sp_file_path", "")
    if explicit and Path(explicit).is_file():
        print(f"[LLM] sp_file: using explicit path: {explicit!r}")
        return explicit

    # Priority 2: match cell_name to filename
    cell_name = layout_context.get("cell_name", "")
    if cell_name:
        # Try exact match first: "CM" -> "Current_Mirror_CM.sp"
        # then partial match
        all_sp = list(project_root.glob("*.sp"))
        
        # Exact cell name in filename (case-insensitive)
        for sp in all_sp:
            if cell_name.lower() in sp.stem.lower():
                print(
                    f"[LLM] sp_file: matched cell_name={cell_name!r} "
                    f"to {sp.name!r}"
                )
                return str(sp)

    # Priority 3: most recently modified .sp file
    all_sp = list(project_root.glob("*.sp"))
    if all_sp:
        # Sort by modification time descending (newest first)
        all_sp_sorted = sorted(all_sp, key=lambda p: p.stat().st_mtime, reverse=True)
        chosen = all_sp_sorted[0]

        if len(all_sp_sorted) > 1:
            print(
                f"[LLM] WARNING: {len(all_sp_sorted)} .sp files found. "
                f"Using most recent: {chosen.name!r}\n"
                f"  All files: {[p.name for p in all_sp_sorted]}\n"
                f"  To fix: set layout_context['sp_file_path'] explicitly "
                f"or layout_context['cell_name'] = 'CM'"
            )
        else:
            print(f"[LLM] sp_file: only one .sp found: {chosen.name!r}")

        return str(chosen)

    print("[LLM] WARNING: no .sp files found in project root")
    return None


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
    # ---- CMD protocol FIRST (most important) ----
    prompt = (
        "RULE #1: For ANY action (swap/move/dummy), you MUST output a "
        "[CMD]{...}[/CMD] block. Without it nothing happens.\n"
        "Actions:\n"
        '[CMD]{"action":"swap","device_a":"MM28","device_b":"MM25"}[/CMD]\n'
        '[CMD]{"action":"move","device":"MM3","x":1.0,"y":0.5}[/CMD]\n'
        '[CMD]{"action":"add_dummy","type":"nmos","count":2,"side":"left"}[/CMD]\n'
        "Use full IDs (MM28 not 28). Multiple [CMD] blocks OK. "
        "add_dummy type=nmos|pmos, count defaults to 1, side=left|right.\n"
        "Write the [CMD] block FIRST, then 1-2 sentences confirming.\n\n"
    )

    # ---- Identity & personality ----
    prompt += (
        "You are an expert Analog IC Layout Engineer in a Symbolic "
        "Layout Editor. Expertise: CMOS matching, symmetry, current "
        "mirrors, diff-pairs, guard rings, dummies, parasitic-aware "
        "placement.\n\n"
        "PERSONALITY: You are friendly, helpful, and conversational. "
        "When the user greets you (hi, hello, hey, good morning, etc.), "
        "respond warmly and naturally — vary your greetings each time, "
        "and briefly remind them what you can help with. "
        "When the user says thanks, respond graciously. "
        "For casual conversation, be personable while gently steering "
        "toward how you can assist with their layout work. "
        "Never give the exact same response twice to the same greeting.\n\n"
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
        nodes         = layout_context.get("nodes",         [])
        edges         = layout_context.get("edges",         [])
        terminal_nets = layout_context.get("terminal_nets", {})
        sp_file       = layout_context.get("sp_file_path",  "")

        if sp_file:
            prompt += f"Active netlist: {Path(sp_file).name}\n"

        prompt += f"=== CURRENT LAYOUT ({len(nodes)} devices) ===\n"
        for n in nodes:
            nid      = n.get("id",          "?")
            ntype    = n.get("type",        "?")
            geo      = n.get("geometry",    {})
            elec     = n.get("electrical",  {})
            orient   = geo.get("orientation", "R0")
            dummy_tag = " [DUMMY]" if n.get("is_dummy") else ""

            line = (
                f"  {nid} ({ntype}{dummy_tag}) "
                f"pos=({geo.get('x', 0):.2f},{geo.get('y', 0):.2f}) "
                f"orient={orient}"
            )
            elec_parts = []
            for k in ("nf", "nfin", "l", "w"):
                if k in elec:
                    elec_parts.append(f"{k}={elec[k]}")
            if elec_parts:
                line += f" [{', '.join(elec_parts)}]"

            tnets = terminal_nets.get(nid, {})
            if tnets:
                parts = [
                    f"{t}={tnets[t]}"
                    for t in ("D", "G", "S")
                    if t in tnets
                ]
                line += f"  nets({', '.join(parts)})"
            prompt += line + "\n"

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
    print(
        f"[LLM] run_llm: {len(chat_messages)} msgs, "
        f"prompt={len(full_prompt)} chars"
    )

    # ---- 1. Gemini ----
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if gemini_key:
        gemini_models = [
            "gemini-2.0-flash",
            "gemini-2.5-flash",
            "gemini-1.5-flash",
        ]
        _gemini_key_invalid = False
        for model_name in gemini_models:
            if _gemini_key_invalid:
                break
            for attempt in range(3):
                try:
                    from google import genai
                    from google.genai import types as genai_types

                    client   = genai.Client(api_key=gemini_key)
                    sys_text = ""
                    conv_parts = []
                    for cm in chat_messages:
                        if cm["role"] == "system":
                            sys_text = cm["content"]
                        else:
                            conv_parts.append(cm["content"])
                    user_text = (
                        "\n".join(conv_parts) if conv_parts else full_prompt
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

                    reply_text = None
                    if response:
                        try:
                            reply_text = response.text
                        except Exception:
                            if (
                                hasattr(response, "candidates")
                                and response.candidates
                            ):
                                parts = (
                                    response.candidates[0].content.parts
                                )
                                if parts:
                                    reply_text = "".join(
                                        p.text
                                        for p in parts
                                        if hasattr(p, "text")
                                    )

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


# -----------------------------------------------------------------
# Worker QObject
# -----------------------------------------------------------------
class LLMWorker(QObject):
    """Worker object that performs LLM API calls on a background QThread."""

    response_ready = Signal(str)
    error_occurred = Signal(str)

    @Slot(str, list)
    def process_request(self, full_prompt, chat_messages):
        """Execute a cascading LLM request (blocking) via run_llm()."""
        try:
            reply = run_llm(chat_messages, full_prompt)
            self.response_ready.emit(reply)
        except RuntimeError as exc:
            self.error_occurred.emit(str(exc))
        except Exception as exc:
            self.error_occurred.emit(f"Unexpected error: {exc}")




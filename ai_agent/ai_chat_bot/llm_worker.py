"""
LLM Worker using the Worker-Object Pattern (QThread + QObject).

Handles all LLM API calls in a dedicated QThread, communicating
with the GUI exclusively via Qt Signals and Slots.
"""

import os
import re
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
# Utility: build the system prompt that tells the LLM how to behave
# -----------------------------------------------------------------
def build_system_prompt(layout_context):
    """Build a system prompt that includes layout context."""
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

    prompt += (
        "You are an expert Analog IC Layout Engineer in a Symbolic "
        "Layout Editor. Expertise: CMOS matching, symmetry, current "
        "mirrors, diff-pairs, guard rings, dummies, parasitic-aware "
        "placement.\n\n"
        "PERSONALITY: You are friendly, helpful, and conversational. "
        "When the user greets you, respond warmly and remind them what you can help with.\n\n"
    )

    prompt += (
        "Editor: Rows (PMOS top, NMOS bottom), auto-compacted by net "
        "adjacency. Toolbar: Undo/Redo, Swap, Dummy mode(D), Delete, Grid snapping.\n"
        "Orientations: R0, R0_FH, R0_FV, R0_FH_FV.\n\n"
    )

    prompt += (
        "Advice style: SHORT (1-3 sentences), actionable. "
        "Prioritise matched pairs and symmetry. "
        "NEVER dump full JSON.\n\n"
    )

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
# Module-level helper — pure Python, no Qt
# -----------------------------------------------------------------
def run_llm(chat_messages, full_prompt, selected_model):
    """Execute the chosen LLM request and return the reply text."""
    
    print(f"[LLM] run_llm: model={selected_model}, msgs={len(chat_messages)}, prompt={len(full_prompt)}")

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
            
    elif selected_model == "Ollama":
        try:
            import requests
            response = requests.post(
                "http://localhost:11434/api/chat",
                json={
                    "model": "llama3.2",
                    "messages": chat_messages,
                    "stream": False
                }
            )
            response.raise_for_status()
            return response.json().get("message", {}).get("content", "").strip()
        except Exception as e:
            return f"Ollama Error: Could not connect or generate response. ({str(e)})\nEnsure 'ollama serve' is running locally on port 11434."

    return f"Error: Unknown model selected ('{selected_model}')."


# -----------------------------------------------------------------
# Worker QObject
# -----------------------------------------------------------------
class LLMWorker(QObject):
    """Worker object that performs LLM API calls on a background QThread."""

    response_ready = Signal(str)
    error_occurred = Signal(str)

    @Slot(str, list, str)
    def process_request(self, full_prompt, chat_messages, selected_model):
        """Execute the LLM request via run_llm()."""
        try:
            reply = run_llm(chat_messages, full_prompt, selected_model)
            self.response_ready.emit(reply)
        except Exception as exc:
            self.error_occurred.emit(f"Unexpected error: {exc}")

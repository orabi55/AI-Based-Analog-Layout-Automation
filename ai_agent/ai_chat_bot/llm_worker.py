"""
LLM Worker using the Worker-Object Pattern (QThread + QObject).

Handles all LLM API calls in a dedicated QThread, communicating
with the GUI exclusively via Qt Signals and Slots.

Multi-Agent Architecture (LayoutCopilot):
    Uses MultiAgentOrchestrator to route user messages through
    specialised agents: Classifier -> Analyzer -> Refiner ->
    Adapter -> CodeGen.

OrchestratorWorker (LangGraph pipeline):
    Drives the 4-stage LangGraph pipeline:
    Topology Analyst -> Placement Specialist -> DRC Critic -> Routing Pre-Viewer.
    Uses human-in-the-loop interrupts for strategy selection and visual review.
"""

import os
import re
import uuid
from pathlib import Path
from typing import cast
from dotenv import load_dotenv
from PySide6.QtCore import QObject, Signal, Slot

from ai_agent.ai_chat_bot.agents.orchestrator import MultiAgentOrchestrator
from ai_agent.ai_chat_bot.run_llm import run_llm

# Load .env – walk upward from this file to find the repo root .env
_this_file = Path(__file__).resolve()
_env_loaded = False
for _parent in _this_file.parents:
    if (_parent / "README.md").is_file() and (_parent / "ai_agent").is_dir():
        _env_path = _parent / ".env"
        if _env_path.is_file():
            load_dotenv(_env_path)
            _env_loaded = True
        break
if not _env_loaded:
    for _parent in _this_file.parents:
        _env_path = _parent / ".env"
        if _env_path.is_file():
            load_dotenv(_env_path)
            break


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


# -----------------------------------------------------------------
# OrchestratorWorker — LangGraph multi-agent pipeline driver
# -----------------------------------------------------------------
class OrchestratorWorker(LLMWorker):
    """Drives the 4-stage LangGraph pipeline (Topology → Placement → DRC → Routing).

    Extends LLMWorker with additional signals and slots for:
    - process_orchestrated_request: start the pipeline
    - resume_with_strategy: resume after strategy-selection interrupt
    - resume_from_viewer: resume after visual-review interrupt
    """

    stage_completed          = Signal(int, str)   # (stage_index, stage_name)
    topology_ready_for_review = Signal(str)        # question text for chat panel
    visual_viewer_signal     = Signal(dict)        # placement + routing payload

    def __init__(self):
        super().__init__()
        try:
            from langchain_core.runnables import RunnableConfig
            self.thread_config = cast(RunnableConfig, {
                "configurable": {
                    "thread_id": str(uuid.uuid4())
                }
            })
        except ImportError:
            self.thread_config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    @Slot(str, str, list, str, str)
    def process_orchestrated_request(
        self,
        user_message,
        layout_context_json,
        chat_history=None,
        selected_model="Gemini",
        ollama_model="llama3.2",
    ):
        import json as _json

        if chat_history is None:
            chat_history = []

        try:
            layout_context = _json.loads(layout_context_json)
        except (_json.JSONDecodeError, ValueError):
            layout_context = {}

        try:
            from ai_agent.ai_chat_bot.agents.classifier import classify_intent
            from ai_agent.ai_chat_bot.run_llm import run_llm as _run_llm

            project_root = Path(__file__).resolve().parent.parent
            sp_file = _resolve_sp_file(layout_context, project_root)
            layout_context["sp_file_path"] = sp_file or ""
            classify_run_llm = lambda msgs, prompt, sel: _run_llm(
                msgs, prompt, sel, ollama_model
            )
            run_selected_llm = lambda msgs, prompt: _run_llm(
                msgs, prompt, selected_model, ollama_model
            )

            # ── Intent Classification ──────────────────────────────────
            intent = classify_intent(user_message, classify_run_llm, selected_model)

            if intent == "chat":
                print("[ORCH] CHAT intent -> conversational reply")
                chat_system = (
                    build_system_prompt(layout_context)
                    + "\n\n"
                    + "For this turn, the user requested general conversation. "
                    + "Reply conversationally and do not emit [CMD] blocks unless "
                    + "the user explicitly asks for an edit action."
                )
                chat_msgs = [{"role": "system", "content": chat_system}] + chat_history
                if not chat_history or chat_history[-1].get("content") != user_message:
                    chat_msgs.append({"role": "user", "content": user_message})
                reply = run_selected_llm(chat_msgs, f"{chat_system}\n\nUser: {user_message}")
                self.response_ready.emit(reply)

            elif intent == "question":
                print("[ORCH] QUESTION intent -> single-agent reply")
                system_prompt = build_system_prompt(layout_context)
                chat_msgs = [{"role": "system", "content": system_prompt}] + chat_history
                if not chat_history or chat_history[-1].get("content") != user_message:
                    chat_msgs.append({"role": "user", "content": user_message})
                reply = run_selected_llm(chat_msgs, f"{system_prompt}\n\nUser: {user_message}")
                self.response_ready.emit(reply)

            elif intent == "concrete":
                print("[ORCH] CONCRETE intent -> Directly editing layout")
                system_prompt = (
                    build_system_prompt(layout_context)
                    + "\n\n"
                    + "For this turn, return ONLY a JSON list of command dicts "
                    + "(no markdown, no prose)."
                )
                reply = run_selected_llm(
                    [{"role": "system", "content": system_prompt},
                     {"role": "user",   "content": user_message}],
                    system_prompt
                )
                try:
                    clean_reply = reply.replace("```json", "").replace("```", "").strip()
                    edits = _json.loads(clean_reply)
                    if isinstance(edits, dict):
                        edits = [edits]
                    elif not isinstance(edits, list):
                        edits = []
                    edits = [c for c in edits if isinstance(c, dict)]
                    self.visual_viewer_signal.emit(
                        {"type": "visual_review", "placement": edits, "routing": {}}
                    )
                except Exception as e:
                    self.error_occurred.emit(f"Failed to parse concrete command: {str(e)}")

            else:
                print("[ORCH] ABSTRACT intent -> Starting LangGraph Pipeline")
                initial_state = {
                    "user_message":    user_message,
                    "chat_history":    chat_history,
                    "nodes":           layout_context.get("nodes", []),
                    "sp_file_path":    layout_context.get("sp_file_path", ""),
                    "pending_cmds":    [],
                    "constraints":     [],
                    "constraint_text": "",
                    "strategy_question": "",
                    "edges":           [],
                    "terminal_nets":   layout_context.get("terminal_nets", {}),
                    "placement_nodes": layout_context.get("nodes", []),
                    "deterministic_snapshot": [],
                    "drc_flags":       [],
                    "drc_pass":        False,
                    "approved":        False,
                    "routing_result":  {},
                    "selected_strategy": "auto",
                    "gap_px":          layout_context.get("gap_px", 0.0),
                    "drc_retry_count": 0,
                    "routing_pass_count": 0,
                }
                if isinstance(layout_context.get("edges"), list):
                    initial_state["edges"] = layout_context["edges"]

                try:
                    from langchain_core.runnables import RunnableConfig
                    self.thread_config = cast(RunnableConfig, {
                        "configurable": {"thread_id": str(uuid.uuid4())}
                    })
                except ImportError:
                    self.thread_config = {"configurable": {"thread_id": str(uuid.uuid4())}}

                self._stream_graph(initial_state)

        except Exception as exc:
            import traceback
            print(f"[ORCH] Pipeline error:\n{traceback.format_exc()}")
            self.error_occurred.emit(f"Orchestrator error: {exc}")

    def _stream_graph(self, input_data):
        try:
            from ai_agent.ai_chat_bot.graph import app as langgraph_app
            from langgraph.types import Command

            interrupted = False
            for event in langgraph_app.stream(input_data, self.thread_config, stream_mode="updates"):
                if "__interrupt__" in event:
                    interrupt_data = event["__interrupt__"][0].value

                    if interrupt_data["type"] == "strategy_selection":
                        self.topology_ready_for_review.emit(interrupt_data["question"])
                    elif interrupt_data["type"] == "visual_review":
                        placement = interrupt_data.get("placement", [])
                        if not isinstance(placement, list):
                            placement = []
                        routing = interrupt_data.get("routing", {})
                        if not isinstance(routing, dict):
                            routing = {}
                        self.visual_viewer_signal.emit({
                            "type": "visual_review",
                            "placement": placement,
                            "routing": routing,
                        })

                    interrupted = True
                    return

            if not interrupted:
                self._finalize_pipeline()

        except Exception as e:
            self.error_occurred.emit(f"Graph Execution Error: {str(e)}")

    def _finalize_pipeline(self):
        try:
            from ai_agent.ai_chat_bot.graph import app as langgraph_app
        except ImportError:
            self.error_occurred.emit("Could not import LangGraph app for finalization.")
            return

        final_state = langgraph_app.get_state(self.thread_config).values

        placement_nodes = final_state.get("placement_nodes", [])
        final_cmds = []
        for n in placement_nodes:
            if n.get("is_dummy"):
                continue
            try:
                x = round(float(n["geometry"]["x"]), 3)
                y = round(float(n["geometry"]["y"]), 3)
            except (TypeError, KeyError, ValueError) as exc:
                print(f"[FINALIZE] Skipping {n.get('id', '?')}: bad geometry ({exc})")
                continue
            final_cmds.append({"action": "move", "device": n["id"], "x": x, "y": y})

        pending_cmds = final_state.get("pending_cmds", [])
        if not final_cmds and pending_cmds:
            print("[FINALIZE] placement_nodes empty — falling back to pending_cmds")
            final_cmds = pending_cmds

        drc_pass    = final_state.get("drc_pass", False)
        drc_flags   = final_state.get("drc_flags", [])
        drc_status  = "✅ Pass" if drc_pass else f"⚠ {len(drc_flags)} violation(s)"
        routing_result  = final_state.get("routing_result", {})
        routing_score   = routing_result.get("score", "N/A")
        routing_cost    = routing_result.get("placement_cost", None)
        constraint_count = len(final_state.get("constraints", []))

        summary_header = (
            f"**[Multi-Agent Pipeline Complete]**\n\n"
            f"• Topology: {constraint_count} constraint lines\n"
            f"• DRC: {drc_status}\n"
            f"• Routing Score: {routing_score}"
            + (f" (cost: {routing_cost:.2f})" if routing_cost is not None else "")
            + f"\n• Commands: {len(final_cmds)} emitted\n\n"
        )

        if final_cmds:
            self.visual_viewer_signal.emit({
                "type": "final_layout",
                "placement": final_cmds,
                "routing": routing_result,
            })

        self.response_ready.emit(summary_header)

    @Slot(str)
    def resume_with_strategy(self, user_choice: str):
        print(f"[ORCH] Resuming graph with strategy: {user_choice}")
        try:
            from langgraph.types import Command
            self._stream_graph(Command(resume=user_choice))
        except Exception as exc:
            self.error_occurred.emit(f"Resume error: {exc}")

    @Slot(dict)
    def resume_from_viewer(self, viewer_response: dict):
        print(f"[ORCH] Resuming from visual viewer. Approved: {viewer_response.get('approved')}")
        try:
            from langgraph.types import Command
            self._stream_graph(Command(resume=viewer_response))
        except Exception as exc:
            self.error_occurred.emit(f"Resume error: {exc}")

"""
File Description:
This module implements the Worker-Object pattern to handle LLM API calls and the multi-agent LangGraph pipeline in background threads. It includes workers for general chat requests and orchestrated layout placement tasks.

Functions:
- _resolve_sp_file:
    - Role: Resolves the appropriate SPICE netlist file for the current layout context.
    - Inputs: 
        - layout_context (dict): Dictionary containing layout metadata.
        - project_root (Path): Root directory of the project.
    - Outputs: (str | None) Path to the resolved .sp file.
- build_system_prompt:
    - Role: Legacy helper to construct a system prompt with layout context (now largely superseded by agent-specific prompts).
    - Inputs: 
        - layout_context (dict): Layout metadata.
    - Outputs: (str) Formatted system prompt.
- LLMWorker.process_request:
    - Role: Executes the multi-agent chat pipeline (LayoutCopilot) to process user queries.
    - Inputs: 
        - full_prompt (str): Combined prompt string.
        - chat_messages (list): History of chat messages.
        - selected_model (str): The model provider to use.
    - Outputs: None (emits signals response_ready, command_ready).
- LLMWorker.set_layout_context:
    - Role: Updates the worker with current layout information.
    - Inputs: 
        - context (dict | None): Layout metadata.
    - Outputs: None
- LLMWorker.reset_pipeline:
    - Role: Resets the internal orchestrator state.
    - Inputs: None
    - Outputs: None
- OrchestratorWorker.process_orchestrated_request:
    - Role: Initiates the 4-stage LangGraph pipeline (Topology -> Placement -> DRC -> Routing) for automated layout.
    - Inputs: 
        - user_message (str), layout_context_json (str), chat_history (list), selected_model (str), task_weight (str).
    - Outputs: None (emits status and result signals).
- OrchestratorWorker._stream_graph:
    - Role: Manages the streaming execution of the LangGraph and handles human-in-the-loop interrupts.
    - Inputs: 
        - input_data (dict | Command): Initial state or resume command.
    - Outputs: None
- OrchestratorWorker._finalize_pipeline:
    - Role: Extracts the final layout state and emits the combined result summary and commands.
    - Inputs: None
    - Outputs: None
- OrchestratorWorker.resume_with_strategy:
    - Role: Resumes pipeline execution after a strategy selection interrupt.
    - Inputs: 
        - user_choice (str): The strategy chosen by the user.
    - Outputs: None
- OrchestratorWorker.resume_from_viewer:
    - Role: Resumes pipeline execution after a visual review interrupt.
    - Inputs: 
        - viewer_response (dict): Approval and feedback from the visual viewer.
    - Outputs: None
"""

import os
import copy
import re
import uuid
from pathlib import Path
from typing import cast
from dotenv import load_dotenv
from PySide6.QtCore import QObject, Signal, Slot
from ai_agent.utils.logging import vprint


# -----------------------------------------------------------------
# Fix 9 — Response deduplication helper
# -----------------------------------------------------------------

class ResponseDeduper:
    """Prevent duplicate ``response_ready`` emissions in a single graph run.

    The worker may emit ``assistant_text`` from both the interrupt handler
    and ``_finalize_pipeline``.  This class ensures the same text is never
    emitted twice in a row.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        """Call at the start of each new (non-resume) graph run."""
        self._last_text: str | None = None
        self._emitted: bool = False

    def should_emit(self, text: str) -> bool:
        """Return True if *text* should be emitted (not a duplicate)."""
        if not text:
            return False
        if text == self._last_text:
            return False
        self._last_text = text
        self._emitted = True
        return True

    @property
    def has_emitted(self) -> bool:
        return self._emitted

from ai_agent.agents.orchestrator import MultiAgentOrchestrator
from ai_agent.llm.runner import run_llm

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
    from ai_agent.agents.prompts import build_chat_prompt
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

    @Slot(str, list, str)
    def process_request(self, full_prompt, chat_messages, selected_model):
        """Execute the multi-agent pipeline.

        Args:
            full_prompt:    complete prompt string (contains system + user).
            chat_messages:  list of {"role", "content"} dicts.
            selected_model: 'Gemini' | 'OpenAI' | 'Ollama'
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
                run_llm_fn=lambda sys, msg, sel: run_llm(sys, msg, sel, task_weight="light"),
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
# extract_assistant_text  (testable helper)
# -----------------------------------------------------------------

def extract_assistant_text(state: dict) -> str:
    """Extract a user-facing response from a graph state snapshot.

    Priority:
    1. ``assistant_text`` — set by session_finalizer, session_chat, or
       specialist nodes that already produce readable text.
    2. Route-specific fallbacks for DRC, routing, topology, strategy.
    3. Empty string (caller decides what to do).

    The worker uses this so it **never** needs to understand specialist
    internals — it simply reads the already-standardised field.
    """
    text = str(state.get("assistant_text") or "").strip()
    if text:
        return text

    # Fallback: DRC
    if state.get("drc_pass") is True:
        return "DRC check passed — no violations found."
    drc_flags = state.get("drc_flags")
    if isinstance(drc_flags, list) and drc_flags:
        n = len(drc_flags)
        return f"DRC check found {n} violation(s)."

    # Fallback: routing
    routing = state.get("routing_result")
    if isinstance(routing, dict):
        rt = routing.get("log_text") or routing.get("summary")
        if rt:
            return str(rt)

    # Fallback: topology / strategy
    for key in ("Analysis_result", "strategy_result"):
        val = state.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()[:2000]

    return ""


# -----------------------------------------------------------------
# Session-route extraction from graph streaming events
# -----------------------------------------------------------------

#: Human-friendly labels shown in the chat panel while routing runs.
SESSION_ROUTE_LABELS: dict[str, str] = {
    "answer_only":   "Answering",
    "command_edit":   "Preparing edit",
    "need_drc":       "Checking DRC",
    "fix_drc":        "Fixing DRC",
    "need_routing":   "Previewing routing",
    "fix_routing":    "Optimizing routing",
    "need_strategy":  "Checking strategy",
    "need_topology":  "Analyzing topology",
    "need_placement": "Computing placement",
    "clarify":        "Clarifying",
}


def extract_session_route_from_event(event: dict) -> str | None:
    """Extract ``session_route`` from a LangGraph streaming event.

    The streaming mode ``"updates"`` produces dicts keyed by node name.
    This function checks for the ``node_session_chat`` key and reads
    the ``session_route`` from its output.

    Returns:
        The route string (e.g. ``"need_drc"``) or ``None`` if the event
        does not contain a session route.
    """
    if not isinstance(event, dict):
        return None
    for node_name in ("node_session_chat", "session_chat"):
        node_output = event.get(node_name)
        if isinstance(node_output, dict):
            route = node_output.get("session_route")
            if route and isinstance(route, str):
                return route
    return None


# -----------------------------------------------------------------
# Graph-app selector  (testable helper used by _stream_graph)
# -----------------------------------------------------------------

def select_graph_app(mode: str | None, *, is_resume: bool = False):
    """Return the correct compiled LangGraph app for the given execution mode.

    Backward-compatibility routing table::

        mode             Graph app              Use case
        ─────────────────────────────────────────────────────────────────
        "chat"           session_chat_app       New session chatbot (default
                                                for layout-aware chat)
        "legacy_chat"    chat_app               Old chatbot graph (kept for
                                                backward compatibility)
        "initial"        app                    Full initial-placement pipeline
        None / unknown   app                    Preserves previous default

    The old ``build_chat_graph()`` and its ``chat_app`` export remain
    fully functional.  To invoke them, pass ``mode="legacy_chat"``.

    Args:
        mode:      ``"initial"``, ``"chat"``, or ``"legacy_chat"``.
        is_resume: True when resuming from an interrupt (Command(resume=…)).

    Returns:
        A compiled LangGraph ``CompiledStateGraph``.
    """
    from ai_agent.graph.builder import (
        app            as initial_graph_app,
        chat_app       as legacy_chat_app,
        session_chat_app,
    )

    if mode == "chat":
        return session_chat_app
    if mode == "legacy_chat":
        return legacy_chat_app
    # Default: initial graph (preserves pre-session-chatbot behavior)
    return initial_graph_app


def _graph_app_label(graph_app) -> str:
    """Human-readable label for a compiled graph app (for logging)."""
    from ai_agent.graph.builder import (
        app            as initial_graph_app,
        chat_app       as legacy_chat_app,
        session_chat_app,
    )
    if graph_app is session_chat_app:
        return "session_chat_app"
    if graph_app is legacy_chat_app:
        return "legacy_chat_app"
    if graph_app is initial_graph_app:
        return "initial_graph_app"
    return "unknown_graph_app"


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
    intent_classified        = Signal(str)         # 'chat'|'question'|'concrete'|'abstract'

    def __init__(self):
        super().__init__()
        self._active_graph_app = None   # set by _stream_graph, reused on resume
        self._response_deduper = ResponseDeduper()  # Fix 9
        try:
            from langchain_core.runnables import RunnableConfig
            self.thread_config = cast(RunnableConfig, {
                "configurable": {
                    "thread_id": str(uuid.uuid4())
                }
            })
        except ImportError:
            self.thread_config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    @Slot(str, str, list, str)
    def process_orchestrated_request(
        self,
        user_message,
        layout_context_json,
        chat_history=None,
        selected_model="Gemini",
        task_weight="light",
    ):
        import json as _json
        import time as _time
        _t0 = _time.time()

        vprint("\n" + "\u2588"*60, flush=True)
        vprint("  CHATBOT ORCHESTRATOR REQUEST", flush=True)
        vprint("\u2588"*60, flush=True)
        vprint(f"[ORCH] Model: {selected_model} | Weight: {task_weight}", flush=True)
        vprint(f"[ORCH] Message: {user_message[:80]!r}", flush=True)
        vprint(f"[ORCH] History: {len(chat_history or [])} messages", flush=True)

        if chat_history is None:
            chat_history = []

        try:
            layout_context = _json.loads(layout_context_json)
        except (_json.JSONDecodeError, ValueError):
            layout_context = {}

        try:
            from ai_agent.agents.classifier import classify_intent
            from ai_agent.llm.placement_worker import get_last_initial_state

            project_root = Path(__file__).resolve().parent.parent
            sp_file = _resolve_sp_file(layout_context, project_root)
            layout_context["sp_file_path"] = sp_file or ""
            vprint(f"[ORCH] SP file: {sp_file or 'N/A'}", flush=True)

            last_state = get_last_initial_state()
            initial_state = copy.deepcopy(last_state) if isinstance(last_state, dict) else {}

            from ai_agent.graph.state_utils import build_initial_agent_trace

            # Resolve the agent trace: prefer the one already embedded in
            # the snapshot; fall back to computing it on-the-fly; or
            # use an empty dict so the graph never crashes.
            _existing_trace = initial_state.get("initial_agent_trace")
            if not _existing_trace and initial_state:
                _existing_trace = build_initial_agent_trace(initial_state)

            initial_state.update({
                "mode":                "chat",
                "intent":              "",
                "router_target":       "",
                "last_agent":          initial_state.get("last_agent", ""),
                "user_message":        user_message,
                "chat_history":        chat_history,
                "selected_model":      selected_model,
                "initial_agent_trace": _existing_trace or {},
                # ── Session chatbot fields (Tasks 5-8) ────────────────
                "assistant_text":      None,
                "session_route":       None,
                "route_confidence":    None,
                "session_reason":      None,
                "requires_specialist": False,
                "specialist_target":   None,
                "session_commands":    None,
            })

            if isinstance(layout_context.get("nodes"), list):
                initial_state["nodes"] = layout_context.get("nodes", [])
                initial_state["placement_nodes"] = layout_context.get("nodes", [])

            if isinstance(layout_context.get("edges"), list):
                initial_state["edges"] = layout_context.get("edges", [])

            if isinstance(layout_context.get("terminal_nets"), dict):
                initial_state["terminal_nets"] = layout_context.get("terminal_nets", {})

            initial_state["sp_file_path"] = layout_context.get("sp_file_path", "")

            initial_state.setdefault("nodes", [])
            initial_state.setdefault("placement_nodes", [])
            initial_state.setdefault("edges", [])
            initial_state.setdefault("terminal_nets", {})
            initial_state.setdefault("sp_file_path", "")

            initial_state.setdefault("pending_cmds", [])
            initial_state.setdefault("constraint_text", "")
            initial_state.setdefault("Analysis_result", "")
            initial_state.setdefault("deterministic_snapshot", [])
            initial_state.setdefault("original_placement_cmds", [])
            initial_state.setdefault("placement_text", "")
            initial_state.setdefault("drc_flags", [])
            initial_state.setdefault("drc_pass", False)
            initial_state.setdefault("drc_retry_count", 0)
            initial_state.setdefault("gap_px", layout_context.get("gap_px", 0.0))
            initial_state.setdefault("routing_pass_count", 0)
            initial_state.setdefault("routing_result", {})
            initial_state.setdefault("strategy_result", "")
            initial_state.setdefault("approved", False)
            initial_state.setdefault("no_abutment", bool(layout_context.get("no_abutment", False)))
            initial_state.setdefault("abutment_candidates", layout_context.get("abutment_candidates", []))

            try:
                from langchain_core.runnables import RunnableConfig
                self.thread_config = cast(RunnableConfig, {
                    "configurable": {"thread_id": str(uuid.uuid4())}
                })
            except ImportError:
                self.thread_config = {"configurable": {"thread_id": str(uuid.uuid4())}}

            vprint(
                f"[ORCH] Initial state ready | {len(initial_state.get('nodes', []))} devices | "
                f"{len(initial_state.get('edges', []))} edges",
                flush=True,
            )

            self._stream_graph(initial_state)

        except Exception as exc:
            import traceback
            vprint(f"[ORCH] Pipeline error:\n{traceback.format_exc()}", flush=True)
            self.error_occurred.emit(f"Orchestrator error: {exc}")

    def _stream_graph(self, input_data):
        try:
            from langgraph.types import Command

            langgraph_app = self._resolve_graph_app(input_data)
            vprint(f"[GRAPH] Using {_graph_app_label(langgraph_app)}", flush=True)

            # Fix 9: reset deduper at the start of each new graph run
            # (but not on resume, since _stream_graph is also called on resume)
            from langgraph.types import Command as _Cmd
            if not isinstance(input_data, _Cmd):
                self._response_deduper.reset()

            vprint(f"\n[GRAPH] ▶ Streaming LangGraph...", flush=True)
            interrupted = False
            event_count = 0
            emitted_route = False
            for event in langgraph_app.stream(input_data, self.thread_config, stream_mode="updates"):
                event_count += 1
                event_keys = list(event.keys())
                vprint(f"[GRAPH]   Event #{event_count}: {event_keys}", flush=True)

                # Emit session route signal once (for UI animation)
                if not emitted_route:
                    _route = extract_session_route_from_event(event)
                    if _route:
                        vprint(f"[GRAPH]   📡 session_route={_route}", flush=True)
                        self.intent_classified.emit(_route)
                        emitted_route = True

                if "__interrupt__" in event:
                    interrupt_data = event["__interrupt__"][0].value
                    itype = interrupt_data.get('type', '?')
                    vprint(f"[GRAPH]   ⏸ INTERRUPT: type={itype}", flush=True)

                    if itype == "visual_review":
                        pending_cmds = interrupt_data.get("pending_cmds", [])
                        if not isinstance(pending_cmds, list):
                            pending_cmds = []

                        # Prefer assistant_text from the graph state
                        try:
                            _snap = langgraph_app.get_state(self.thread_config).values
                            text = extract_assistant_text(_snap)
                        except Exception:
                            text = ""

                        # Legacy fallback: agent-specific payload keys
                        if not text:
                            last = interrupt_data.get("last_agent", "")
                            text = (
                                interrupt_data.get("Analysis", "")
                                if last == "topology_analyst"
                                else interrupt_data.get("Strategy", "")
                                if last == "strategy_selector"
                                else interrupt_data.get("Placement", "")
                                if last == "placement_specialist"
                                else ""
                            )

                        if text and self._response_deduper.should_emit(text):
                            self.response_ready.emit(text)

                        if pending_cmds:
                            self.visual_viewer_signal.emit({
                                "type": "visual_review",
                                "pending_cmds": pending_cmds,
                            })
                        try:
                            from ai_agent.llm.placement_worker import set_last_initial_state
                            snapshot = langgraph_app.get_state(self.thread_config).values
                            set_last_initial_state(snapshot)
                            vprint("[GRAPH]   ✓ Saved state after visual review", flush=True)
                        except Exception as exc:
                            vprint(f"[GRAPH]   ✗ Failed to save state: {exc}", flush=True)
                        self._finalize_pipeline(langgraph_app)
                        interrupted = True
                        return

                    interrupted = True
                    return

            vprint(f"[GRAPH] ✓ Stream complete ({event_count} events)", flush=True)
            if not interrupted:
                self._finalize_pipeline(langgraph_app)

        except Exception as e:
            vprint(f"[GRAPH] ✗ Error: {e}", flush=True)
            self.error_occurred.emit(f"Graph Execution Error: {str(e)}")

    def _finalize_pipeline(self, langgraph_app=None):
        vprint("\n" + "═"*60, flush=True)
        vprint("  PIPELINE FINALIZATION", flush=True)
        vprint("═"*60, flush=True)

        if self._active_graph_app is None:
            vprint("[FINALIZE] ⚠ No active graph app stored.", flush=True)

        # Emit assistant_text from the final graph state (if not already sent)
        _app = langgraph_app or self._active_graph_app
        if _app is not None:
            try:
                final_state = _app.get_state(self.thread_config).values
                text = extract_assistant_text(final_state)
                if text and self._response_deduper.should_emit(text):
                    vprint(f"[FINALIZE] Emitting assistant_text ({len(text)} chars)", flush=True)
                    self.response_ready.emit(text)
                elif text:
                    vprint(f"[FINALIZE] Skipping duplicate assistant_text ({len(text)} chars)", flush=True)
            except Exception as exc:
                vprint(f"[FINALIZE] ⚠ Could not read state: {exc}", flush=True)

        vprint("[FINALIZE] ✓ Pipeline complete — signals emitted.", flush=True)

    # ── Graph-app resolution (instance method) ────────────────────

    def _resolve_graph_app(self, input_data):
        """Pick the right graph app and store it for later resume calls.

        * **Initial invoke** (``input_data`` is a ``dict``): select the graph
          app via :func:`select_graph_app`, store it in ``_active_graph_app``.
        * **Resume** (``input_data`` is a ``Command``): reuse the previously
          stored ``_active_graph_app``.  Falls back to the initial graph
          app if nothing was stored (should not happen in normal flow).
        """
        from langgraph.types import Command

        if isinstance(input_data, Command):
            if self._active_graph_app is not None:
                return self._active_graph_app
            vprint(
                "[GRAPH] ⚠ Resume without stored active graph — "
                "falling back to select_graph_app(None)",
                flush=True,
            )
            self._active_graph_app = select_graph_app(None)
            return self._active_graph_app

        mode = input_data.get("mode") if isinstance(input_data, dict) else None
        self._active_graph_app = select_graph_app(mode)
        return self._active_graph_app

    @Slot(str)
    def resume_with_strategy(self, user_choice: str):
        vprint(f"[ORCH] Resuming graph with strategy: {user_choice}", flush=True)
        try:
            from langgraph.types import Command
            self._stream_graph(Command(resume=user_choice))
        except Exception as exc:
            self.error_occurred.emit(f"Resume error: {exc}")

    @Slot(dict)
    def resume_from_viewer(self, viewer_response: dict):
        vprint(f"[ORCH] Resuming from visual viewer. Approved: {viewer_response.get('approved')}", flush=True)
        try:
            from langgraph.types import Command
            self._stream_graph(Command(resume=viewer_response))
        except Exception as exc:
            self.error_occurred.emit(f"Resume error: {exc}")

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

from ai_agent.agents.orchestrator import MultiAgentOrchestrator
from ai_agent.llm.runner import run_llm, stream_llm

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

    # ── Legacy signals (kept for backward compat: CMD mode + simple paths) ──
    response_ready  = Signal(str)
    command_ready   = Signal(dict)
    error_occurred  = Signal(str)
    # Per-tool one-line progress (state ∈ {"starting","success","failed"})
    tool_progress   = Signal(str, str, str)

    # ── New streaming signals (FC mode + LangGraph orchestrator path) ──
    response_started = Signal(str)          # message_id
    response_delta   = Signal(str, str)     # message_id, text_delta
    response_done    = Signal(str, str)     # message_id, final_text
    stage_started    = Signal(str, str)     # stage_key, display_title
    stage_delta      = Signal(str, str)     # stage_key, short_update
    stage_done       = Signal(str, str)     # stage_key, summary
    tool_started     = Signal(str, dict)    # tool_name, args
    tool_done        = Signal(str, dict)    # tool_name, result_dict

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

            message_id = str(uuid.uuid4())
            self.response_started.emit(message_id)

            def _streaming_run_llm(msgs, prompt, model):
                try:
                    from ai_agent.llm.factory import get_langchain_llm
                    llm = get_langchain_llm(model, task_weight="light")
                    text, _ = stream_llm(
                        msgs or [{"role": "user", "content": prompt or user_message}],
                        llm,
                        message_id,
                        self,
                        emit_done=False,
                    )
                    return text
                except Exception as exc:
                    vprint(f"[LLM Worker] streaming fallback: {exc}", flush=True)
                    return run_llm(msgs, prompt, model, task_weight="light")

            # The layout_context is stored by ChatPanel and injected
            # into the system prompt. We parse it from there for the
            # orchestrator. The ChatPanel sets _layout_context on us
            # via set_layout_context().
            layout_context = getattr(self, '_layout_context', None)

            result = self._orchestrator.process(
                user_message=user_message,
                layout_context=layout_context,
                chat_history=chat_messages,
                run_llm_fn=_streaming_run_llm,
                selected_model=selected_model,
            )

            reply = result.get("reply", "")
            commands = result.get("commands", [])

            if not reply:
                reply = (
                    "I processed your request but had nothing to say. "
                    "Could you try rephrasing?"
                )

            self.response_done.emit(message_id, reply)
            self.response_ready.emit(reply)

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

    @Slot(str, list, str)
    def process_request_with_tools(self, full_prompt: str, chat_messages: list,
                                   selected_model: str):
        """Tool-enabled chat path with token streaming.

        Streaming events emitted (in order):
            response_started(mid)
            response_delta(mid, chunk)*       — text token deltas
            tool_started(name, args)
            tool_done(name, result_dict)      — once per tool call
            response_done(mid, final_text)
            command_ready(replace_layout)     — once if any tool changed nodes

        Falls back silently to one-shot via stream_llm's invoke() path if the
        provider doesn't support .stream().  Alibaba/Qwen still skips
        bind_tools (handled inside run_llm_with_tools).
        """
        try:
            from ai_agent.llm.tool_runner import run_llm_with_tools

            message_id = str(uuid.uuid4())

            ctx = getattr(self, "_layout_context", None) or {}
            nodes = ctx.get("nodes", []) if isinstance(ctx, dict) else []
            terminal_nets = ctx.get("terminal_nets", {}) if isinstance(ctx, dict) else {}

            # Legacy progress callback (kept for non-streaming consumers)
            def _progress(name, success, message):
                if success is None:
                    self.tool_progress.emit(str(name), "starting", "")
                elif success:
                    self.tool_progress.emit(str(name), "success", str(message or ""))
                else:
                    self.tool_progress.emit(str(name), "failed", str(message or ""))

            # Open the streaming bubble before any deltas land
            self.response_started.emit(message_id)

            result = run_llm_with_tools(
                chat_messages, full_prompt, selected_model,
                task_weight="light",
                nodes=nodes,
                terminal_nets=terminal_nets,
                progress_cb=_progress,
                worker=self,
                message_id=message_id,
            )

            final_text = result.get("text") or "(no response)"
            if result.get("fc_used") and not result.get("_commands_emitted"):
                for cmd in result.get("cmd_blocks", []):
                    self.command_ready.emit(cmd)
                result["_commands_emitted"] = True

            # Tool mode suppresses stream_llm's early done event, then
            # finalizes here after tool execution and command propagation.
            try:
                self.response_done.emit(message_id, final_text)
            except Exception:
                pass

        except Exception as exc:
            import traceback
            print(f"[LLM Worker] tool path error:\n{traceback.format_exc()}")
            self.error_occurred.emit(f"Tool-enabled LLM failed: {exc}")


# -----------------------------------------------------------------
# OrchestratorWorker — LangGraph multi-agent pipeline driver
# -----------------------------------------------------------------
class OrchestratorWorker(LLMWorker):
    """Drives the 4-stage LangGraph pipeline (Topology → Placement → DRC → Routing).

    Extends LLMWorker with additional signals and slots for:
    - process_orchestrated_request: start the pipeline
    """

    stage_completed          = Signal(int, str)   # (stage_index, stage_name)
    topology_ready_for_review = Signal(str)        # question text for chat panel
    visual_viewer_signal     = Signal(dict)        # placement + routing payload
    intent_classified        = Signal(str)         # 'chat'|'question'|'concrete'|'abstract'

    def __init__(self):
        super().__init__()
        self._last_routing_report = None
        try:
            from langchain_core.runnables import RunnableConfig
            self.thread_config = cast(RunnableConfig, {
                "configurable": {
                    "thread_id": str(uuid.uuid4())
                }
            })
        except ImportError:
            self.thread_config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    @Slot(dict)
    def process_routing_preview(self, layout_context: dict | None):
        """Run the routing preview directly and emit the report once."""
        try:
            ctx = layout_context or {}
            nodes = ctx.get("nodes", []) if isinstance(ctx, dict) else []
            edges = ctx.get("edges", []) if isinstance(ctx, dict) else []
            terminal_nets = ctx.get("terminal_nets", {}) if isinstance(ctx, dict) else {}

            if not nodes:
                self.response_ready.emit("No layout loaded. Load a layout to generate a routing report.")
                return

            from ai_agent.nodes.routing_previewer import node_routing_previewer

            state = {
                "placement_nodes": nodes,
                "edges": edges,
                "terminal_nets": terminal_nets,
            }
            result = node_routing_previewer(state)
            routing = result.get("routing_result", {}) if isinstance(result, dict) else {}
            report_text = routing.get("log_text") or routing.get("summary") or ""
            report_text = report_text.strip() if isinstance(report_text, str) else ""
            if report_text:
                self._last_routing_report = report_text
                self.response_ready.emit(report_text)
            else:
                self.response_ready.emit("Routing preview completed, but no report text was produced.")
        except Exception as exc:
            self.error_occurred.emit(f"Routing preview failed: {exc}")

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

            initial_state.update({
                "mode":            "chat",
                "intent":          "",
                "router_target":   "",
                "last_agent":      initial_state.get("last_agent", ""),
                "user_message":    user_message,
                "chat_history":    chat_history,
                "selected_model":  selected_model,
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
            initial_state.setdefault("general_response", "")
            initial_state.setdefault("drc_flags", [])
            initial_state.setdefault("drc_pass", True)
            initial_state.setdefault("drc_retry_count", 0)
            initial_state.setdefault("gap_px", layout_context.get("gap_px", 0.0))
            initial_state.setdefault("routing_pass_count", 0)
            initial_state.setdefault("routing_result", {})
            initial_state.setdefault("strategy_result", "")
            initial_state.setdefault("approved", False)
            initial_state.setdefault("no_abutment", bool(layout_context.get("no_abutment", False)))
            initial_state.setdefault("abutment_candidates", layout_context.get("abutment_candidates", []))
            initial_state.setdefault("groups", {})

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

    # ── Mapping: LangGraph node key → user-facing stage title ──────────
    NODE_DISPLAY = {
        "router":                            "Classifying request",
        "topology_analyst":                  "Topology Analyst",
        "node_topology_analyst":             "Topology Analyst",
        "strategy_selector":                 "Strategy Selector",
        "node_strategy_selector":            "Strategy Selector",
        "placement_specialist":              "Placement Specialist",
        "node_placement_specialist":         "Placement Specialist",
        "node_placement_specialist_chatbot": "Placement Specialist",
        "node_finger_expansion":             "Finger Expansion",
        "node_symmetry_enforcer":            "Symmetry Enforcer",
        "routing_previewer":                 "Routing Previewer",
        "node_routing_previewer":            "Routing Previewer",
        "drc_critic":                        "DRC Critic",
        "node_drc_critic":                   "DRC Critic",
        "human_viewer":                      "Visual Review",
        "node_human_viewer":                 "Visual Review",
        "general":                           "General Chat",
    }

    @staticmethod
    def _short_summary(value, limit: int = 120) -> str:
        """Build a one-line summary for the stage_delta from a node's output."""
        if not isinstance(value, dict):
            return ""
        # Order matters: pick the first populated field
        target = value.get("router_target")
        if isinstance(target, str) and target.strip():
            return f"Routed to {target.strip()}"
        for key in ("Analysis_result", "strategy_result", "placement_text",
                    "general_response"):
            v = value.get(key)
            if isinstance(v, str) and v.strip():
                s = v.strip().replace("\n", " ")
                return s[:limit] + ("…" if len(s) > limit else "")
        rr = value.get("routing_result")
        if isinstance(rr, dict):
            log_text = rr.get("log_text") or rr.get("summary") or ""
            if log_text:
                s = str(log_text).strip().replace("\n", " ")
                return s[:limit] + ("…" if len(s) > limit else "")
        drc_pass = value.get("drc_pass")
        if isinstance(drc_pass, bool) and not drc_pass:
            return "DRC check failed"
        if isinstance(drc_pass, bool) and drc_pass:
            return "DRC check passed"
        flags = value.get("drc_flags")
        if isinstance(flags, list) and flags:
            return f"{len(flags)} DRC violation(s)"
        pending = value.get("pending_cmds")
        if isinstance(pending, list) and pending:
            return f"{len(pending)} command(s) pending"
        return ""

    def _stream_graph(self, input_data):
        try:
            from ai_agent.graph.builder import app as initial_graph_app
            from ai_agent.graph.builder import chat_app as chat_graph_app
            from langgraph.types import Command

            langgraph_app = chat_graph_app if isinstance(input_data, dict) and input_data.get("mode") == "chat" else initial_graph_app

            vprint(f"\n[GRAPH] ▶ Streaming LangGraph...", flush=True)
            interrupted = False
            event_count = 0
            active_stage_key = None   # last stage_started we emitted

            for event in langgraph_app.stream(input_data, self.thread_config, stream_mode="updates"):
                event_count += 1
                event_keys = list(event.keys())
                vprint(f"[GRAPH]   Event #{event_count}: {event_keys}", flush=True)

                # ── Interrupt handling — preserved exactly ───────────────────
                if "__interrupt__" in event:
                    # close any active stage cleanly first
                    if active_stage_key is not None:
                        try:
                            self.stage_done.emit(active_stage_key, "")
                        except Exception:
                            pass
                        active_stage_key = None

                    interrupt_data = event["__interrupt__"][0].value
                    itype = interrupt_data.get('type', '?')
                    vprint(f"[GRAPH]   ⏸ INTERRUPT: type={itype}", flush=True)

                    if itype == "visual_review":
                        pending_cmds = interrupt_data.get("pending_cmds", [])
                        if not isinstance(pending_cmds, list):
                            pending_cmds = []

                        last_agent = interrupt_data.get("last_agent")
                        if last_agent == "topology_analyst": text = interrupt_data.get("Analysis", "")
                        elif last_agent == "strategy_selector": text = interrupt_data.get("Strategy", "")
                        elif last_agent == "placement_specialist": text = interrupt_data.get("Placement", "")
                        elif last_agent == "routing_previewer":
                            if self._last_routing_report:
                                text = ""
                            else:
                                text = self._short_summary({"routing_result": interrupt_data.get("Routing", {})}, 500)
                        elif last_agent == "drc_critic":
                            text = interrupt_data.get("Placement", "")
                            text += "\n" + self._short_summary({"drc_pass": interrupt_data.get("DRC pass", False)}, 500)
                            text += "\n" + self._short_summary({"drc_flags": interrupt_data.get("DRC violations", [])}, 500)
                        elif last_agent == "general": text = interrupt_data.get("General", "")
                        else: text = ""

                        if text:
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
                        self._finalize_pipeline()
                        interrupted = True
                        return

                    interrupted = True
                    return

                # ── Per-node stage events ───────────────────────────────────
                for node_key, node_value in event.items():
                    if node_key == "__interrupt__":
                        continue
                    title = self.NODE_DISPLAY.get(node_key, node_key)

                    # Close any previously-active stage
                    if active_stage_key is not None and active_stage_key != node_key:
                        try:
                            self.stage_done.emit(active_stage_key, "")
                        except Exception:
                            pass

                    # Open the new stage
                    try:
                        self.stage_started.emit(node_key, title)
                    except Exception:
                        pass
                    active_stage_key = node_key

                    # Emit a delta if we can find a meaningful field
                    summary = self._short_summary(node_value)
                    if summary:
                        try:
                            self.stage_delta.emit(node_key, summary)
                        except Exception:
                            pass

                    # Emit full routing report as a normal response bubble
                    if node_key in ("routing_previewer", "node_routing_previewer"):
                        rr = node_value.get("routing_result") if isinstance(node_value, dict) else None
                        if isinstance(rr, dict):
                            report_text = rr.get("log_text")
                            if isinstance(report_text, str) and report_text.strip():
                                report_text = report_text.strip()
                                if report_text == self._last_routing_report:
                                    report_text = ""
                                else:
                                    self._last_routing_report = report_text
                            if report_text:
                                try:
                                    self.response_ready.emit(report_text)
                                except Exception:
                                    pass

                    # Mark stage done immediately — LangGraph emits each node's
                    # output as a single update, so the node has already finished.
                    try:
                        self.stage_done.emit(node_key, summary)
                    except Exception:
                        pass
                    active_stage_key = None

                    # Emit stage_completed for legacy listeners
                    try:
                        self.stage_completed.emit(event_count, node_key)
                    except Exception:
                        pass

            vprint(f"[GRAPH] ✓ Stream complete ({event_count} events)", flush=True)
            if active_stage_key is not None:
                try:
                    self.stage_done.emit(active_stage_key, "")
                except Exception:
                    pass
            if not interrupted:
                self._finalize_pipeline()

        except Exception as e:
            vprint(f"[GRAPH] ✗ Error: {e}", flush=True)
            self.error_occurred.emit(f"Graph Execution Error: {str(e)}")

    def _finalize_pipeline(self):
        vprint("\n" + "═"*60, flush=True)
        vprint("  PIPELINE FINALIZATION", flush=True)
        vprint("═"*60, flush=True)
        try:
            from ai_agent.graph.builder import app as langgraph_app
        except ImportError:
            self.error_occurred.emit("Could not import LangGraph app for finalization.")
            return

        vprint("[FINALIZE] ✓ Pipeline complete — signals emitted.", flush=True)


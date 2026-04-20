"""
PlacerGraph Worker (QThread + QObject friendly).

Streams ai_initial_placement.placer_graph similarly to OrchestratorWorker in
ai_chat_bot.llm_worker, including support for LangGraph interrupts and resume.
"""

import json
import uuid
from typing import cast

from PySide6.QtCore import QObject, Signal, Slot


class PlacerGraphWorker(QObject):
    """Run the initial-placement LangGraph and stream updates to the UI."""

    response_ready = Signal(str)
    error_occurred = Signal(str)

    # Stage/stream updates
    stage_completed = Signal(int, str)   # (stage_index, stage_name)

    # Interrupt channels (same semantics as llm_worker)
    topology_ready_for_review = Signal(str)
    visual_viewer_signal = Signal(dict)

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

    @Slot(str, str, list)
    def process_initial_placement_request(
        self,
        layout_context_json: str,
        user_message: str = "Optimize initial placement.",
        chat_history=None,
    ):
        """Start a new graph run from a serialized layout context JSON."""
        if chat_history is None:
            chat_history = []

        try:
            layout_context = json.loads(layout_context_json) if layout_context_json else {}
            if not isinstance(layout_context, dict):
                layout_context = {}
        except (json.JSONDecodeError, ValueError):
            layout_context = {}

        initial_state = {
            "user_message": user_message,
            "chat_history": chat_history,
            "nodes": layout_context.get("nodes", []),
            "sp_file_path": layout_context.get("sp_file_path", ""),
            "pending_cmds": [],
            "constraint_text": "",
            "Analysis_result": "",
            "edges": layout_context.get("edges", []) if isinstance(layout_context.get("edges"), list) else [],
            "terminal_nets": layout_context.get("terminal_nets", {}),
            "placement_nodes": layout_context.get("nodes", []),
            "deterministic_snapshot": [],
            "drc_flags": [],
            "drc_pass": False,
            "approved": False,
            "routing_result": {},
            "strategy_result": "",
            "gap_px": layout_context.get("gap_px", 0.0),
            "drc_retry_count": 0,
            "routing_pass_count": 0,
        }

        try:
            from langchain_core.runnables import RunnableConfig
            self.thread_config = cast(RunnableConfig, {
                "configurable": {"thread_id": str(uuid.uuid4())}
            })
        except ImportError:
            self.thread_config = {"configurable": {"thread_id": str(uuid.uuid4())}}

        self._stream_graph(initial_state)

    def _stream_graph(self, input_data):
        """Stream LangGraph updates, stop on interrupt, finalize on completion."""
        try:
            from ai_agent.ai_initial_placement.placer_graph import app as placer_app

            interrupted = False
            stage_index = 0

            for event in placer_app.stream(input_data, self.thread_config, stream_mode="updates"):
                if "__interrupt__" in event:
                    interrupt_data = event["__interrupt__"][0].value

                    if isinstance(interrupt_data, dict) and interrupt_data.get("type") == "strategy_selection":
                        self.topology_ready_for_review.emit(str(interrupt_data.get("question", "")))
                    elif isinstance(interrupt_data, dict) and interrupt_data.get("type") == "visual_review":
                        placement = interrupt_data.get("placement", [])
                        routing = interrupt_data.get("routing", {})
                        if not isinstance(placement, list):
                            placement = []
                        if not isinstance(routing, dict):
                            routing = {}
                        self.visual_viewer_signal.emit({
                            "type": "visual_review",
                            "placement": placement,
                            "routing": routing,
                        })
                    else:
                        # Unknown interrupt type: still forward to viewer channel.
                        payload = interrupt_data if isinstance(interrupt_data, dict) else {"value": str(interrupt_data)}
                        self.visual_viewer_signal.emit(payload)

                    interrupted = True
                    return

                for stage_name in event.keys():
                    if stage_name == "__interrupt__":
                        continue
                    stage_index += 1
                    self.stage_completed.emit(stage_index, stage_name)

            if not interrupted:
                self._finalize_pipeline()

        except Exception as exc:
            self.error_occurred.emit(f"Graph Execution Error: {exc}")

    def _finalize_pipeline(self):
        """Build a summary and emit final placement commands for the viewer."""
        try:
            from ai_agent.ai_initial_placement.placer_graph import app as placer_app
        except ImportError:
            self.error_occurred.emit("Could not import placer_graph app for finalization.")
            return

        final_state = placer_app.get_state(self.thread_config).values

        placement_nodes = final_state.get("placement_nodes", [])
        final_cmds = []
        for node in placement_nodes:
            if node.get("is_dummy"):
                continue
            try:
                x = round(float(node["geometry"]["x"]), 3)
                y = round(float(node["geometry"]["y"]), 3)
            except (TypeError, KeyError, ValueError):
                continue
            final_cmds.append({
                "action": "move",
                "device": node.get("id", ""),
                "x": x,
                "y": y,
            })

        pending_cmds = final_state.get("pending_cmds", [])
        if not final_cmds and isinstance(pending_cmds, list):
            final_cmds = [c for c in pending_cmds if isinstance(c, dict)]

        drc_pass = final_state.get("drc_pass", False)
        drc_flags = final_state.get("drc_flags", [])
        drc_status = "Pass" if drc_pass else f"{len(drc_flags)} violation(s)"

        summary = (
            "[Initial Placement Graph Complete]\n"
            f"- DRC: {drc_status}\n"
            f"- Commands: {len(final_cmds)} emitted"
        )

        self.visual_viewer_signal.emit({
            "type": "final_layout",
            "placement": final_cmds,
            "routing": final_state.get("routing_result", {}),
        })
        self.response_ready.emit(summary)

    @Slot(str)
    def resume_with_strategy(self, user_choice: str):
        """Resume graph execution with a strategy choice after interrupt."""
        try:
            from langgraph.types import Command
            self._stream_graph(Command(resume=user_choice))
        except Exception as exc:
            self.error_occurred.emit(f"Resume error: {exc}")

    @Slot(dict)
    def resume_from_viewer(self, viewer_response: dict):
        """Resume graph execution with visual-review response."""
        try:
            from langgraph.types import Command
            self._stream_graph(Command(resume=viewer_response))
        except Exception as exc:
            self.error_occurred.emit(f"Resume error: {exc}")

"""
File Description:
This module implements the PlacementWorker, a specialized Qt-compatible worker that drives the automated initial-placement LangGraph pipeline. It manages the execution lifecycle, streams progress updates via signals, and computes layout metrics like DRC status, area, and utilization.

Functions:
- PlacementWorker.__init__:
    - Role: Initializes the placement worker with a unique thread configuration.
    - Inputs: None
    - Outputs: None
- PlacementWorker.process_initial_placement_request:
    - Role: Starts a new LangGraph run for automated initial placement using the provided layout context and user parameters.
    - Inputs: 
        - layout_context_json (str): Serialized JSON of the current layout state.
        - user_message (str): Optional instruction for the placement.
        - chat_history (list): Previous message history.
        - selected_model (str): LLM provider to use.
        - no_abutment (bool): Flag to disable diffusion sharing.
        - abutment_candidates (list): List of specific device pairs for abutment.
    - Outputs: None (emits stage_completed, response_ready, visual_viewer_signal, or error_occurred).
- PlacementWorker._stream_graph:
    - Role: Builds and streams the layout graph, emitting stage signals for each completed node.
    - Inputs: 
        - input_data (dict): Initial state for the LangGraph.
    - Outputs: None
- PlacementWorker._finalize_pipeline:
    - Role: Extracts the final state from the graph, computes placement statistics (DRC, area, etc.), and emits the final layout payload.
    - Inputs: None
    - Outputs: None
"""

import json
import copy
import os
import uuid
from typing import cast

from PySide6.QtCore import QObject, Signal, Slot


class PlacementWorker(QObject):
    """Run the initial-placement LangGraph and stream updates to the UI."""

    _last_initial_state = None

    response_ready = Signal(str)
    error_occurred = Signal(str)
    stage_completed = Signal(int, str)
    visual_viewer_signal = Signal(dict)

    def __init__(self):
        super().__init__()
        try:
            from langchain_core.runnables import RunnableConfig
            self.thread_config = cast(RunnableConfig, {
                "configurable": {"thread_id": str(uuid.uuid4())}
            })
        except ImportError:
            self.thread_config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    @Slot(str, str, list, str, bool, list)
    def process_initial_placement_request(
        self,
        layout_context_json: str,
        user_message: str = "Optimize initial placement.",
        chat_history=None,
        selected_model: str = "Gemini",
        no_abutment: bool = False,
        abutment_candidates: list = None,
    ):
        """Start a new graph run from a serialized layout context JSON."""
        if chat_history is None:
            chat_history = []
        if abutment_candidates is None:
            abutment_candidates = []

        try:
            layout_context = json.loads(layout_context_json) if layout_context_json else {}
            if not isinstance(layout_context, dict):
                layout_context = {}
        except (json.JSONDecodeError, ValueError):
            layout_context = {}

        initial_state = {
            "mode": "initial",  # NEW: unified graph mode
            "user_message": user_message,
            "chat_history": chat_history,
            "nodes": layout_context.get("nodes", []),
            "sp_file_path": layout_context.get("sp_file_path", ""),
            "selected_model": selected_model,
            "intent": "",
            "router_target": "",
            "last_agent": "",
            "pending_cmds": [],
            "constraint_text": "",
            "Analysis_result": "",
            "edges": layout_context.get("edges", []) if isinstance(layout_context.get("edges"), list) else [],
            "terminal_nets": layout_context.get("terminal_nets", {}),
            "placement_nodes": [],          # always start fresh — node_placement_specialist populates this
            "deterministic_snapshot": [],
            "original_placement_cmds": [],
            "drc_flags": [],
            "drc_pass": False,
            "approved": False,
            "routing_result": {},
            "strategy_result": "",
            "gap_px": layout_context.get("gap_px", 0.0),
            "drc_retry_count": 0,
            "routing_pass_count": 0,
            "no_abutment": no_abutment,
            "abutment_candidates": abutment_candidates,
            "placement_mode": "auto",   # symmetry_enforcer may upgrade to "two_half"
            "placement_quality": {},        # populated by node_placement_specialist
            "placement_text": "",
            "placement_goals": layout_context.get("placement_goals", {}),  # user priorities
        }

        try:
            from langchain_core.runnables import RunnableConfig
            self.thread_config = cast(RunnableConfig, {
                "configurable": {"thread_id": str(uuid.uuid4())}
            })
        except ImportError:
            self.thread_config = {"configurable": {"thread_id": str(uuid.uuid4())}}

        if os.environ.get("PLACEMENT_DEBUG_FULL_LOG", "0").lower() not in ("1", "true", "yes"):
            os.environ["PLACEMENT_STEPS_ONLY"] = "1"

        import logging as _logging
        for _noisy in ("google_genai", "google.genai", "google.auth",
                        "google.api_core", "google", "httpx", "httpcore",
                        "openai", "dashscope", "grpc"):
            _logging.getLogger(_noisy).setLevel(_logging.WARNING)

        nodes = layout_context.get("nodes", [])
        n_pmos = sum(1 for n in nodes if str(n.get("type", "")).lower() == "pmos")
        n_nmos = sum(1 for n in nodes if str(n.get("type", "")).lower() == "nmos")

        from ai_agent.utils.logging import pipeline_start
        pipeline_start("LangGraph Pipeline", 5, {
            "model": selected_model,
            "devices": len(nodes),
            "n_pmos": n_pmos,
            "n_nmos": n_nmos,
            "abutment": not no_abutment,
        })

        self._stream_graph(initial_state)

    def _stream_graph(self, input_data):
        """Stream LangGraph updates and finalize on completion."""
        try:
            from ai_agent.graph.builder import build_layout_graph

            # Build a FRESH graph for each run to prevent state leaks
            placer_app, _ = build_layout_graph(mode="initial")
            self._placer_app = placer_app

            stage_index = 0
            for event in placer_app.stream(input_data, self.thread_config, stream_mode="updates"):
                for stage_name in event.keys():
                    if stage_name == "__interrupt__":
                        continue
                    stage_index += 1
                    self.stage_completed.emit(stage_index, stage_name)

            self._finalize_pipeline()

        except Exception as exc:
            self.error_occurred.emit(f"Graph Execution Error: {exc}")
        finally:
            os.environ.pop("PLACEMENT_STEPS_ONLY", None)

    def _finalize_pipeline(self):
        """Build a summary and emit final placement payload."""
        placer_app = getattr(self, "_placer_app", None)
        if placer_app is None:
            self.error_occurred.emit("Could not access graph app for finalization.")
            return

        final_state = placer_app.get_state(self.thread_config).values
        try:
            snapshot = copy.deepcopy(final_state)
        except Exception:
            snapshot = dict(final_state) if isinstance(final_state, dict) else None
        self._last_initial_state = snapshot
        PlacementWorker._last_initial_state = snapshot
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
            final_cmds.append({"action": "move", "device": node.get("id", ""), "x": x, "y": y})

        pending_cmds = final_state.get("pending_cmds", [])
        if not final_cmds and isinstance(pending_cmds, list):
            final_cmds = [c for c in pending_cmds if isinstance(c, dict)]

        drc_pass = final_state.get("drc_pass", False)
        drc_flags = final_state.get("drc_flags", [])
        n_violations = len(drc_flags)
        drc_status = "✓ Clean" if drc_pass else f"✗ {n_violations} violation(s)"

        min_x = min_y = float('inf')
        max_x = max_y = float('-inf')
        active_area_sum = 0.0
        for node in placement_nodes:
            geo = node.get("geometry", {})
            try:
                x = float(geo.get("x", 0))
                y = float(geo.get("y", 0))
                w = float(geo.get("width", 0))
                h = float(geo.get("height", 0))
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x + w)
                max_y = max(max_y, y + h)
                if not node.get("is_dummy", False) and not str(node.get("id", "")).startswith("EDGE_DUMMY"):
                    active_area_sum += (w * h)
            except (TypeError, ValueError):
                continue

        width_um = max_x - min_x if max_x > min_x else 0.0
        height_um = max_y - min_y if max_y > min_y else 0.0
        area = width_um * height_um
        aspect = f"{width_um/height_um:.2f}" if height_um > 0 else "?"
        utilization = f"{(active_area_sum / area) * 100:.1f}%" if area > 0 else "?"

        from ai_agent.utils.logging import pipeline_end
        benchmarks_text = pipeline_end({
            "drc_status": drc_status,
            "n_placed": len(placement_nodes),
            "pmos_nmos_sep": "✓ OK" if drc_pass else "Check editor",
            "width": f"{width_um:.3f}",
            "height": f"{height_um:.3f}",
            "aspect": aspect,
            "area": f"{area:.3f} um²",
            "utilization": utilization,
            "quality": final_state.get("placement_quality", {}),
            "placement_goals": final_state.get("placement_goals", {}),
        })

        routing_text = final_state.get("routing_result", {}).get("log_text", "")
        print(f"[PlacementWorker] Benchmark text length: {len(benchmarks_text)}")
        print(f"[PlacementWorker] Routing text length: {len(routing_text)}")

        summary = (
            "[Initial Placement Complete]\n"
            f"- DRC: {drc_status}\n"
            f"- Nodes: {len(placement_nodes)} placed\n\n"
            f"{benchmarks_text}\n"
        )
        if routing_text:
            summary += f"\n{routing_text}\n"
        
        print(f"[PlacementWorker] Final summary length: {len(summary)}")

        self.visual_viewer_signal.emit({
            "type": "final_layout",
            "placement_nodes": placement_nodes,
            "placement": final_cmds,
            "routing": final_state.get("routing_result", {}),
        })
        self.response_ready.emit(summary)


def get_last_initial_state():
    """Return the most recent initial-placement graph state snapshot, if any."""
    return PlacementWorker._last_initial_state


def set_last_initial_state(state_snapshot) -> None:
    """Update the cached initial-placement graph state snapshot."""
    try:
        snapshot = copy.deepcopy(state_snapshot)
    except Exception:
        snapshot = dict(state_snapshot) if isinstance(state_snapshot, dict) else None
    PlacementWorker._last_initial_state = snapshot

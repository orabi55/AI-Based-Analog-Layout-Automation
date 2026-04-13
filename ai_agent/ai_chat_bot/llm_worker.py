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

from pathlib import Path
import uuid
from typing import cast
from langgraph.types import Command
from ai_agent.ai_chat_bot.graph import app as langgraph_app
from dotenv import load_dotenv
from PySide6.QtCore import QObject, Signal, Slot
from langchain_core.runnables import RunnableConfig
from ai_agent.ai_chat_bot.run_llm import run_llm

# Load .env from repository root so API keys are available regardless of
# package depth after folder moves.
_this_file = Path(__file__).resolve()
_env_loaded = False

# Prefer a parent that looks like repo root in the current layout.
for _parent in _this_file.parents:
    if (_parent / "README.md").is_file() and (_parent / "ai_agent").is_dir():
        _env_path = _parent / ".env"
        if _env_path.is_file():
            load_dotenv(_env_path)
            _env_loaded = True
        break

# Fallback: first .env found while walking upward.
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


# -----------------------------------------------------------------
# -----------------------------------------------------------------
class OrchestratorWorker(LLMWorker):
    """Drives the multi-agent pipeline using LangGraph."""

    stage_completed = Signal(int, str)
    topology_ready_for_review = Signal(str)
    visual_viewer_signal = Signal(dict)

    def __init__(self):
        super().__init__()
        self.thread_config = cast(RunnableConfig, {
             "configurable": {
                   "thread_id": str(uuid.uuid4())
              }
         })
    @Slot(str, str, list)
    def process_orchestrated_request(self, user_message, layout_context_json, chat_history=None):
        import json as _json

        if chat_history is None:
            chat_history = []
        
        try:
            layout_context = _json.loads(layout_context_json)
        except (_json.JSONDecodeError, ValueError):
            layout_context = {}

        try:
            from ai_agent.ai_chat_bot.agents.classifier_agent import classify_intent
            project_root = Path(__file__).resolve().parent.parent

            sp_file = _resolve_sp_file(layout_context, project_root)
            layout_context["sp_file_path"] = sp_file or ""

            # ── Intent Classification ──────────────────────────────────
            intent = classify_intent(user_message, run_llm)

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
                reply = run_llm(chat_msgs, f"{chat_system}\n\nUser: {user_message}")
                self.response_ready.emit(reply)

            elif intent == "question":
                print("[ORCH] QUESTION intent -> single-agent reply")
                system_prompt = build_system_prompt(layout_context)
                chat_msgs = [{"role": "system", "content": system_prompt}] + chat_history
                if not chat_history or chat_history[-1].get("content") != user_message:
                    chat_msgs.append({"role": "user", "content": user_message})
                reply = run_llm(chat_msgs, f"{system_prompt}\n\nUser: {user_message}")
                self.response_ready.emit(reply)

            elif intent == "concrete":
                print("[ORCH] CONCRETE intent -> Directly editing layout")
                system_prompt = (
                    build_system_prompt(layout_context)
                    + "\n\n"
                    + "For this turn, return ONLY a JSON list of command dicts "
                    + "(no markdown, no prose)."
                )
                reply = run_llm([{"role": "system", "content": system_prompt}, {"role": "user", "content": user_message}], system_prompt)
                
                try:
                    # Clean up markdown if LLM wrapped in ```json
                    clean_reply = reply.replace("```json", "").replace("```", "").strip()
                    edits = _json.loads(clean_reply)
                    if isinstance(edits, dict):
                        edits = [edits]
                    elif not isinstance(edits, list):
                        edits = []
                    edits = [c for c in edits if isinstance(c, dict)]
                    # Directly inject edits into the viewer, skipping Stage 1 & 2
                    self.visual_viewer_signal.emit({"type": "visual_review", "placement": edits, "routing": {}})
                except Exception as e:
                    self.error_occurred.emit(f"Failed to parse concrete command: {str(e)}")

            else:
                print("[ORCH] ABSTRACT intent -> Starting LangGraph Pipeline")
                initial_state = {
                    "user_message": user_message,
                    "chat_history": chat_history,
                    "nodes": layout_context.get("nodes", []),
                    "sp_file_path": layout_context.get("sp_file_path", ""),
                    "pending_cmds": [],
                    "constraints": [],
                    "constraint_text": "",
                    "strategy_question": "",
                    "edges": [],
                    "terminal_nets": layout_context.get("terminal_nets", {}),
                    "placement_nodes": layout_context.get("nodes", []),
                    "drc_flags": [],
                    "drc_pass": False,
                    "approved": False,
                    "routing_result": {},
                    "selected_strategy": "auto",
                    "gap_px": layout_context.get("gap_px", 0.0),
                    "drc_retry_count": 0,
                    "routing_pass_count": 0,
                    
                }

                if isinstance(layout_context.get("edges"), list):
                    initial_state["edges"] = layout_context.get("edges", [])

                self.thread_config = cast(RunnableConfig, {
                    "configurable": {
                        "thread_id": str(uuid.uuid4())
                    }
                })
                self._stream_graph(initial_state)

        except Exception as exc:
            import traceback
            print(f"[ORCH] Pipeline error:\n{traceback.format_exc()}")
            self.error_occurred.emit(f"Orchestrator error: {exc}")

    def _stream_graph(self, input_data):
        try:
            interrupted = False
            for event in langgraph_app.stream(input_data, self.thread_config, stream_mode="updates"):
                if "__interrupt__" in event:
                    interrupt_data = event["__interrupt__"][0].value

                    if interrupt_data["type"] == "strategy_selection":
                        self.topology_ready_for_review.emit(interrupt_data["question"])
                    elif interrupt_data["type"] == "visual_review":
                        # Signal is declared as dict, so wrap placement/routing
                        # into a structured payload instead of emitting a raw list.
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
        final_state = langgraph_app.get_state(self.thread_config).values

        # ── Collect final placement commands ─────────────────────────────────────
        placement_nodes = final_state.get("placement_nodes", [])
        pending_cmds    = final_state.get("pending_cmds", [])

        # Build the authoritative final command list from placement_nodes,
        # pending_cmds may be stale/partial after loops, so we recompile from state.
        final_cmds = []
        for n in placement_nodes:
            if n.get("is_dummy"):
                continue
            try:
                x = round(float(n["geometry"]["x"]), 3)
                y = round(float(n["geometry"]["y"]), 3)
            except (TypeError, KeyError, ValueError) as exc:
                print(f"[FINALIZE] ⚠ Skipping device {n.get('id', '?')}: bad geometry ({exc})")
                continue
            final_cmds.append({
                "action": "move",
                "device": n["id"],
                "x": x,
                "y": y,
            })

        # Fallback: if placement_nodes was empty, use pending_cmds as-is.
        if not final_cmds and pending_cmds:
            print("[FINALIZE] ⚠ placement_nodes empty — falling back to pending_cmds")
            final_cmds = pending_cmds

        if not final_cmds:
            print("[FINALIZE] ⚠ CRITICAL: no commands to emit. Canvas will not update.")

        # ── Build summary header──
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

        # ── Emit commands to the canvas, then the summary to the chat ────────────
        # visual_viewer_signal sends the placement to the canvas widget,
        # exactly as node_human_viewer does mid-pipeline via interrupt.
        # response_ready emits the text summary to the chat window.
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
        self._stream_graph(Command(resume=user_choice))

    @Slot(dict)
    def resume_from_viewer(self, viewer_response: dict):
        print(f"[ORCH] Resuming graph from visual viewer. Approved: {viewer_response.get('approved')}")
        self._stream_graph(Command(resume=viewer_response))
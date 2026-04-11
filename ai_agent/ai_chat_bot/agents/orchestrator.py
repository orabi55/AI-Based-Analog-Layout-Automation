"""
ai_agent/ai_chat_bot/agents/orchestrator.py
=============================================
Multi-agent orchestrator implementing the LayoutCopilot pipeline.

State machine flow:
  User msg -> Classifier -> route:
    CHAT/QUESTION -> Chat Agent -> reply
    CONCRETE      -> CodeGen Agent -> [CMD] blocks
    ABSTRACT      -> Analyzer -> Refiner (pause) -> user approval
                  -> Adapter -> CodeGen -> [CMD] blocks
"""

from enum import Enum, auto


class PipelineState(Enum):
    """Tracks where we are in the multi-agent pipeline."""
    IDLE = auto()
    WAITING_FOR_REFINER_FEEDBACK = auto()


class MultiAgentOrchestrator:
    """Drives the multi-agent pipeline without LangGraph.

    Uses a simple state enum to track whether we are mid-conversation
    (waiting for the user to approve/reject a Refiner proposal).
    """

    def __init__(self):
        self.state = PipelineState.IDLE
        self._pending_analyzer_output = ""  # cached for Adapter use
        self._layout_context = None

    def reset(self):
        """Clear state (e.g. when user clears chat)."""
        self.state = PipelineState.IDLE
        self._pending_analyzer_output = ""

    # ─────────────────────────────────────────────────────────────
    # Main entry point — called by LLMWorker on the background thread
    # ─────────────────────────────────────────────────────────────
    def process(self, user_message: str, layout_context: dict | None,
                chat_history: list, run_llm_fn, selected_model: str) -> dict:
        """Process one turn of the conversation.

        Args:
            user_message:   raw text from the chat input.
            layout_context: dict with nodes/edges/terminal_nets.
            chat_history:   list of {"role", "content"} dicts.
            run_llm_fn:     callable(msgs, full_prompt, model) -> str
            selected_model: 'Gemini' | 'OpenAI' | 'Ollama'

        Returns:
            dict with keys:
                "reply":    str   — text to display in the chat bubble.
                "commands": list  — [CMD] dicts to execute (may be empty).
                "waiting":  bool  — True if we paused for user feedback.
        """
        self._layout_context = layout_context

        # ── If we're mid-pipeline (waiting for Refiner feedback) ──
        if self.state == PipelineState.WAITING_FOR_REFINER_FEEDBACK:
            return self._handle_refiner_feedback(
                user_message, layout_context, run_llm_fn, selected_model
            )

        # ── Fresh turn: classify intent ───────────────────────────
        from ai_agent.ai_chat_bot.agents.classifier import classify_intent
        intent = classify_intent(user_message, run_llm_fn, selected_model)
        print(f"[ORCHESTRATOR] Intent: {intent}")

        if intent == "chat":
            return self._handle_chat(
                user_message, layout_context, chat_history,
                run_llm_fn, selected_model
            )

        elif intent == "question":
            return self._handle_question(
                user_message, layout_context, chat_history,
                run_llm_fn, selected_model
            )

        elif intent == "concrete":
            return self._handle_concrete(
                user_message, layout_context,
                run_llm_fn, selected_model
            )

        else:  # abstract
            return self._handle_abstract(
                user_message, layout_context,
                run_llm_fn, selected_model
            )

    # ─────────────────────────────────────────────────────────────
    # CHAT / QUESTION handlers (single LLM call, no commands)
    # ─────────────────────────────────────────────────────────────
    def _handle_chat(self, msg, ctx, history, run_llm_fn, model):
        from ai_agent.ai_chat_bot.agents.prompts import build_chat_prompt
        system = build_chat_prompt(ctx)
        msgs = [{"role": "system", "content": system}]
        # Include last few history items for context
        for h in (history or [])[-4:]:
            msgs.append({"role": h["role"], "content": h["content"]})
        if not history or history[-1].get("content") != msg:
            msgs.append({"role": "user", "content": msg})
        reply = run_llm_fn(msgs, f"{system}\n\nUser: {msg}", model)
        return {"reply": reply, "commands": [], "waiting": False}

    def _handle_question(self, msg, ctx, history, run_llm_fn, model):
        from ai_agent.ai_chat_bot.agents.prompts import build_chat_prompt
        system = build_chat_prompt(ctx)
        msgs = [{"role": "system", "content": system}]
        for h in (history or [])[-4:]:
            msgs.append({"role": h["role"], "content": h["content"]})
        if not history or history[-1].get("content") != msg:
            msgs.append({"role": "user", "content": msg})
        reply = run_llm_fn(msgs, f"{system}\n\nUser: {msg}", model)
        return {"reply": reply, "commands": [], "waiting": False}

    # ─────────────────────────────────────────────────────────────
    # CONCRETE handler (single CodeGen call -> [CMD] blocks)
    # ─────────────────────────────────────────────────────────────
    def _handle_concrete(self, msg, ctx, run_llm_fn, model):
        from ai_agent.ai_chat_bot.agents.prompts import build_codegen_prompt
        system = build_codegen_prompt(ctx)
        msgs = [
            {"role": "system", "content": system},
            {"role": "user",   "content": msg},
        ]
        reply = run_llm_fn(msgs, f"{system}\n\nUser: {msg}", model)
        return {"reply": reply, "commands": [], "waiting": False}

    # ─────────────────────────────────────────────────────────────
    # ABSTRACT handler (Analyzer -> Refiner -> pause)
    # ─────────────────────────────────────────────────────────────
    def _handle_abstract(self, msg, ctx, run_llm_fn, model):
        # Step 1: Analyzer Agent
        from ai_agent.ai_chat_bot.agents.prompts import (
            build_analyzer_prompt, build_refiner_prompt
        )
        analyzer_system = build_analyzer_prompt(ctx)
        analyzer_msgs = [
            {"role": "system", "content": analyzer_system},
            {"role": "user",   "content": msg},
        ]
        analyzer_output = run_llm_fn(
            analyzer_msgs,
            f"{analyzer_system}\n\nUser: {msg}",
            model
        )
        print(f"[ORCHESTRATOR] Analyzer output: {analyzer_output[:200]}")

        # Step 2: Refiner Agent — formats the proposals for the user
        refiner_system = build_refiner_prompt()
        refiner_msgs = [
            {"role": "system", "content": refiner_system},
            {"role": "user",   "content": (
                f"The Analyzer proposed these strategies:\n\n"
                f"{analyzer_output}\n\n"
                f"Format them for the designer to choose."
            )},
        ]
        refiner_output = run_llm_fn(
            refiner_msgs,
            f"{refiner_system}\n\n{analyzer_output}",
            model
        )
        print(f"[ORCHESTRATOR] Refiner output: {refiner_output[:200]}")

        # Cache the analyzer output for the Adapter step
        self._pending_analyzer_output = analyzer_output
        self.state = PipelineState.WAITING_FOR_REFINER_FEEDBACK

        return {"reply": refiner_output, "commands": [], "waiting": True}

    # ─────────────────────────────────────────────────────────────
    # REFINER FEEDBACK handler (Adapter -> CodeGen -> [CMD])
    # ─────────────────────────────────────────────────────────────
    def _handle_refiner_feedback(self, user_choice, ctx, run_llm_fn, model):
        from ai_agent.ai_chat_bot.agents.prompts import (
            build_adapter_prompt, build_codegen_prompt
        )

        # Step 3: Adapter Agent — maps approved plan to device IDs
        adapter_system = build_adapter_prompt(ctx)
        adapter_msgs = [
            {"role": "system", "content": adapter_system},
            {"role": "user",   "content": (
                f"The Analyzer proposed:\n{self._pending_analyzer_output}\n\n"
                f"The designer chose: {user_choice}\n\n"
                f"Map this to concrete directives using REAL device IDs "
                f"from the layout data."
            )},
        ]
        adapter_output = run_llm_fn(
            adapter_msgs,
            f"{adapter_system}\n\nDesigner chose: {user_choice}",
            model
        )
        print(f"[ORCHESTRATOR] Adapter output: {adapter_output[:200]}")

        # Step 4: CodeGen Agent — produces [CMD] blocks
        codegen_system = build_codegen_prompt(ctx)
        codegen_msgs = [
            {"role": "system", "content": codegen_system},
            {"role": "user",   "content": (
                f"Convert these directives into [CMD] blocks:\n\n"
                f"{adapter_output}"
            )},
        ]
        codegen_output = run_llm_fn(
            codegen_msgs,
            f"{codegen_system}\n\nDirectives:\n{adapter_output}",
            model
        )
        print(f"[ORCHESTRATOR] CodeGen output: {codegen_output[:200]}")

        # Reset state — pipeline complete
        self.state = PipelineState.IDLE
        self._pending_analyzer_output = ""

        return {"reply": codegen_output, "commands": [], "waiting": False}

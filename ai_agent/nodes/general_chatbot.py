"""
General Chatbot Node
====================
Handles general user questions that do not fit the specialized intent targets.
Builds a placement context snapshot and forwards the question to the LLM.
"""

import time
from ai_agent.agents.placement_specialist import build_placement_context_chatbot
from ai_agent.nodes._shared import (
    _build_llm_messages,
    _invoke_with_retry,
    _split_content_and_thinking,
    _strip_thinking_text,
    _print_thinking_block,
    _update_and_save_chat_history,
    vprint,
    ip_step,
)


GENERAL_CHATBOT_PROMPT = """\
You are a helpful assistant for an analog IC layout tool.
Answer the user's question or request using the provided placement context.
Be concise and practical. If the request is ambiguous, ask one clarifying question.
Do not output command blocks or tool calls.
"""

GENERAL_CHAT_SYSTEM_PROMPT = """\
You are Antigravity, a helpful conversational AI assistant.
You can discuss any topic (both general and technical/coding theory) naturally.
Keep your response concise, interactive, and clear.
Do not output command blocks or tool calls.
"""


def node_general_chatbot(state):
    t0 = time.time()
    vprint("\n" + "═" * 60, flush=True)
    vprint("  CHAT: GENERAL ASSISTANT", flush=True)
    vprint("═" * 60, flush=True)

    nodes = state.get("placement_nodes", []) or state.get("nodes", [])
    edges = state.get("edges", [])
    terminal_nets = state.get("terminal_nets", {})
    constraint_text = state.get("constraint_text", "")
    user_message = state.get("user_message", "")
    chat_history = state.get("chat_history", [])
    selected_model = state.get("selected_model", "Gemini")
    no_abutment_flag = state.get("no_abutment", False)
    intent = state.get("intent", "general_chat")

    if intent == "general_chat":
        context_text = "No active layout context needed."
        system_prompt = GENERAL_CHAT_SYSTEM_PROMPT
        user_prompt = user_message
    else:
        context_text = build_placement_context_chatbot(
            nodes,
            constraint_text,
            terminal_nets=terminal_nets,
            edges=edges,
            no_abutment=no_abutment_flag,
        )
        system_prompt = GENERAL_CHATBOT_PROMPT
        user_prompt = f"User request: {user_message}\n\n{context_text}"

    messages = _build_llm_messages(system_prompt, chat_history, user_prompt)
    vprint(f"[GENERAL] Calling LLM ({selected_model}, weight=light)...", flush=True)

    response_text = ""
    try:
        response = _invoke_with_retry(messages, selected_model, "light", "GENERAL")
        response_text, response_thinking = _split_content_and_thinking(response.content)
        response_text = _strip_thinking_text(response_text)
        _print_thinking_block("GENERAL", response_thinking)
    except Exception as exc:
        vprint(f"[GENERAL] ✗ LLM failed: {exc}", flush=True)
        response_text = "General assistant failed to respond."

    updated_chat_history = _update_and_save_chat_history(
        chat_history=chat_history,
        user_content=user_message,
        node_role="General Assistant",
        node_content=response_text,
    )

    elapsed = time.time() - t0
    nchar = len(response_text) if response_text else 0
    ip_step("Chat General", f"ok ({elapsed:.1f}s, {nchar} chars)")

    return {
        "chat_history": updated_chat_history,
        "general_response": response_text,
        "last_agent": "general",
        "pending_cmds": [],
    }

"""
Shared Node Utilities
=====================
Common utility functions for LangGraph nodes, including chat history management, 
thinking block handling, and LLM invocation with retry logic.

Functions:
- _canonicalize_role: Normalizes chat roles to a standard set.
- _split_content_and_thinking: Separates model content into visible text and thinking text.
- _strip_thinking_text: Removes thinking blocks from text.
- _print_thinking_block: Prints thinking blocks for debugging.
- _normalize_chat_history: Cleans and normalizes chat history entries.
- _append_chat_message: Adds a message to chat history with optional deduplication.
- _save_chat_history_json: Persists chat history to a JSON file.
- _update_and_save_chat_history: Updates history with new messages and saves.
- _build_llm_messages: Constructs message lists for LangChain LLM calls.
- _content_to_text: Extracts and cleans text from content objects.
- _invoke_with_retry: Invokes the LLM with retry logic on timeouts.
- _extract_agent_output_content: Extracts final assistant content from agent results.
- _invoke_react_agent_with_retry: Invokes a ReAct agent with timeout-aware retries.
"""

import copy
import json
import re
import time
import logging
from pathlib import Path
from ai_agent.knowledge.skill_injector import SkillMiddleware

from ai_agent.graph.state import LayoutState
from ai_agent.llm.factory import get_langchain_llm
from ai_agent.utils.logging import steps_only, vprint, ip_step

CHAT_HISTORY_JSON_PATH = Path(__file__).resolve().parents[2] / "chat_history.json"
MAX_CHAT_HISTORY = 50

_VALID_CHAT_ROLES = {
    "human", "user", "ai", "assistant", "function", "tool", "system", "developer"
}


def _canonicalize_role(role):
    role_text = str(role or "").strip()
    if not role_text:
        return ""
    lowered = role_text.lower()
    if lowered in _VALID_CHAT_ROLES:
        return lowered
    if "assistant" in lowered or lowered.startswith("ai"):
        return "assistant"
    if lowered in {"human", "client"}:
        return "user"
    return "assistant"


def _split_content_and_thinking(content):
    """Split model content into visible text and hidden thinking text."""
    visible_chunks = []
    thinking_chunks = []

    def _walk(obj):
        if obj is None:
            return
        if isinstance(obj, str):
            visible_chunks.append(obj)
            return
        if isinstance(obj, list):
            for part in obj:
                _walk(part)
            return
        if isinstance(obj, dict):
            part_type = str(obj.get("type", "")).strip().lower()
            if part_type == "thinking":
                thinking_text = obj.get("thinking")
                if thinking_text is None:
                    thinking_text = obj.get("text")
                if thinking_text is None:
                    thinking_text = json.dumps(obj, ensure_ascii=False)
                thinking_chunks.append(str(thinking_text))
                return
            if isinstance(obj.get("text"), str):
                visible_chunks.append(obj["text"])
                return
            visible_chunks.append(json.dumps(obj, ensure_ascii=False))
            return
        visible_chunks.append(str(obj))

    _walk(content)
    visible_text = "\n".join(s for s in visible_chunks if str(s).strip()).strip()
    thinking_text = "\n\n".join(s for s in thinking_chunks if str(s).strip()).strip()
    return visible_text, thinking_text


def _strip_thinking_text(text: str) -> str:
    """Remove thinking blocks from plain text before sending prompts."""
    if not text:
        return ""
    cleaned = str(text)
    cleaned = re.sub(r"<thinking>[\s\S]*?</thinking>", "", cleaned, flags=re.IGNORECASE)
    stripped = cleaned.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
            visible, _ = _split_content_and_thinking(parsed)
            cleaned = visible
        except (json.JSONDecodeError, ValueError):
            logging.debug("Failed to parse thinking content as JSON", exc_info=True)
    cleaned = re.sub(
        r'\{\s*"type"\s*:\s*"thinking"[\s\S]*?\}\s*',
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _print_thinking_block(stage_tag: str, thinking_text: str):
    if not thinking_text or steps_only():
        return
    print(f"[{stage_tag}] Thinking Block:", flush=True)
    print(thinking_text, flush=True)


def _normalize_chat_history(chat_history):
    normalized = []
    if not isinstance(chat_history, list):
        return normalized
    for msg in chat_history:
        if not isinstance(msg, dict):
            continue
        role = _canonicalize_role(msg.get("role", ""))
        content = _strip_thinking_text(str(msg.get("content", "")).strip())
        if not role or not content:
            continue
        normalized.append({"role": role, "content": content})
    return normalized


def _append_chat_message(chat_history, role, content, dedupe_last=False):
    if not content:
        return chat_history
    role_text = _canonicalize_role(role)
    content_text = _strip_thinking_text(str(content).strip())
    if not role_text or not content_text:
        return chat_history
    if dedupe_last and chat_history:
        last = chat_history[-1]
        if (
            isinstance(last, dict)
            and str(last.get("role", "")).strip() == role_text
            and str(last.get("content", "")).strip() == content_text
        ):
            return chat_history
    chat_history.append({"role": role_text, "content": content_text})
    return chat_history


def _save_chat_history_json(chat_history):
    serializable = _normalize_chat_history(chat_history)
    if len(serializable) > MAX_CHAT_HISTORY:
        serializable = serializable[-MAX_CHAT_HISTORY:]
    try:
        CHAT_HISTORY_JSON_PATH.write_text(
            json.dumps(serializable, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"[CHAT] Failed to save chat history: {exc}")


def _update_and_save_chat_history(chat_history, user_content, node_role=None, node_content=None):
    updated_chat_history = _normalize_chat_history(chat_history)
    if user_content:
        updated_chat_history = _append_chat_message(updated_chat_history, "user", user_content, dedupe_last=True)
    if node_role:
        _append_chat_message(updated_chat_history, node_role, node_content)
    _save_chat_history_json(updated_chat_history)
    return updated_chat_history


def _build_llm_messages(system_prompt, chat_history, user_prompt, max_history=8):
    messages = [{"role": "system", "content": _strip_thinking_text(str(system_prompt))}]
    for msg in _normalize_chat_history(chat_history)[-max_history:]:
        messages.append({"role": msg["role"], "content": _strip_thinking_text(msg["content"])})
    messages.append({"role": "user", "content": _strip_thinking_text(str(user_prompt).strip())})
    return messages


def _content_to_text(content):
    visible_text, _ = _split_content_and_thinking(content)
    return _strip_thinking_text(visible_text)


def _invoke_with_retry(messages, selected_model: str, task_weight: str, stage_tag: str):
    max_retries = 1 if task_weight == "light" else 2
    for attempt in range(max_retries + 1):
        try:
            llm = get_langchain_llm(selected_model, task_weight=task_weight)
            try:
                prompt_text = json.dumps(messages, indent=2, ensure_ascii=False, default=str)
            except Exception:
                prompt_text = str(messages)
            vprint(f"[{stage_tag}] Prompt:\n{prompt_text}")

            response = llm.invoke(messages)
            response_payload = getattr(response, "content", response)
            response_text = _content_to_text(response_payload) or str(response_payload)
            vprint(f"[{stage_tag}] Response:\n{response_text}")
            return response
        except Exception as exc:
            msg = str(exc).lower()
            is_timeout = "timed out" in msg or "timeout" in msg or "read operation timed out" in msg
            if is_timeout and attempt < max_retries:
                print(
                    f"[{stage_tag}] ⚠ Timeout from provider; retrying ({attempt + 1}/{max_retries})...",
                    flush=True,
                )
                continue
            raise


def _extract_agent_output_content(agent_result):
    """Extract the final assistant content from a ReAct agent result payload."""
    if isinstance(agent_result, dict):
        messages = agent_result.get("messages", [])
        if isinstance(messages, list):
            for msg in reversed(messages):
                if isinstance(msg, dict):
                    role = str(msg.get("role", msg.get("type", ""))).strip().lower()
                    content = msg.get("content")
                else:
                    role = str(getattr(msg, "type", getattr(msg, "role", ""))).strip().lower()
                    content = getattr(msg, "content", None)
                if role in ("assistant", "ai") and content:
                    return content
            for msg in reversed(messages):
                if isinstance(msg, dict):
                    content = msg.get("content")
                else:
                    content = getattr(msg, "content", None)
                if content:
                    return content
        output = agent_result.get("output")
        if output:
            return output
    return agent_result


def _invoke_react_agent_with_retry(
    system_prompt: str,
    chat_history,
    user_prompt: str,
    selected_model: str,
    task_weight: str,
    stage_tag: str,
    tools,
):
    """Invoke placement agent via ReAct framework with timeout-aware retries."""
    max_retries = 1 if task_weight == "light" else 2
    for attempt in range(max_retries + 1):
        try:
            from langchain.agents import create_agent
            llm = get_langchain_llm(selected_model, task_weight=task_weight)
            react_agent = create_agent(
                model=llm,
                tools=list(tools or []),
                system_prompt=_strip_thinking_text(str(system_prompt)),
                middleware=[SkillMiddleware()],                                   
            )
            history_messages = _normalize_chat_history(chat_history)[-8:]
            input_messages = [
                {"role": "system", "content": _strip_thinking_text(str(system_prompt))}
            ]
            input_messages.extend(
                {"role": msg["role"], "content": _strip_thinking_text(msg["content"])}
                for msg in history_messages
            )
            input_messages.append(
                {"role": "user", "content": _strip_thinking_text(str(user_prompt).strip())}
            )
            try:
                prompt_text = json.dumps(input_messages, indent=2, ensure_ascii=False, default=str)
            except Exception:
                prompt_text = str(input_messages)
            vprint(f"[{stage_tag}] ReAct Prompt (attempt {attempt + 1}):\n{prompt_text}")

            result = react_agent.invoke({"messages": input_messages})
            response_content = _extract_agent_output_content(result)
            response_text = _content_to_text(response_content) if response_content is not None else str(result)
            vprint(f"[{stage_tag}] ReAct Response (attempt {attempt + 1}):\n{response_text}")
            return result
        except Exception as exc:
            msg = str(exc).lower()
            is_timeout = "timed out" in msg or "timeout" in msg or "read operation timed out" in msg
            if is_timeout and attempt < max_retries:
                print(
                    f"[{stage_tag}] ⚠ Timeout from provider; retrying ({attempt + 1}/{max_retries})...",
                    flush=True,
                )
                continue
            raise

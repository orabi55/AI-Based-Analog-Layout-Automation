"""
Tool-Enabled LLM Runner
=======================
Adds Function Calling (FC) as a parallel path alongside the existing
text-based [CMD]-block flow.

How it relates to the existing path
-----------------------------------
- run_llm() in runner.py is the simple text-only path.
- run_llm_with_tools() in this file binds TOOL_REGISTRY to the LLM and
  routes any tool_calls in the response through ai_agent.tools.dispatcher.
- If the LLM does NOT emit tool_calls (or its provider doesn't support
  bind_tools — Alibaba/Qwen), the response.content is returned untouched
  and the caller can fall back to the existing [CMD]-block parser.

Provider matrix
---------------
- Gemini, VertexGemini, VertexClaude → bind_tools → FC available
- Alibaba (Qwen-max / Qwen-plus)     → bind_tools SKIPPED → text-only
  (LangChain's ChatOpenAI wrapper for the dashscope endpoint does not
   reliably surface tool_calls; the LLM is asked to emit [CMD] blocks
   in plain text instead.)
"""

from __future__ import annotations

import logging
import time
from typing import Any, List, Tuple

logger = logging.getLogger("ai_agent")

# Providers that DO NOT support reliable tool binding — text-only path
PROVIDERS_WITHOUT_TOOLS: frozenset = frozenset({"Alibaba"})


# ---------------------------------------------------------------------------
# Schema conversion
# ---------------------------------------------------------------------------

def _to_openai_tool(anthropic_tool: dict) -> dict:
    """Convert an Anthropic-format tool dict into the OpenAI-format dict that
    LangChain's bind_tools accepts and translates per provider.

    Anthropic format:
        {"name": ..., "description": ..., "input_schema": {...}}

    OpenAI format (LangChain's lingua franca):
        {"type": "function",
         "function": {"name": ..., "description": ..., "parameters": {...}}}
    """
    return {
        "type": "function",
        "function": {
            "name":        anthropic_tool["name"],
            "description": anthropic_tool["description"],
            "parameters":  anthropic_tool["input_schema"],
        },
    }


# ---------------------------------------------------------------------------
# LLM construction with optional tool binding
# ---------------------------------------------------------------------------

def _build_tool_enabled_llm(selected_model: str, task_weight: str) -> Tuple[Any, bool]:
    """Return (llm, tools_bound).

    - tools_bound=True  → tools were successfully bound; FC is available
    - tools_bound=False → bind_tools was skipped or failed; text-only path
    """
    from ai_agent.llm.factory import get_langchain_llm
    from ai_agent.tools.schemas import TOOL_REGISTRY

    llm = get_langchain_llm(selected_model, task_weight)

    if selected_model in PROVIDERS_WITHOUT_TOOLS:
        logger.info("[TOOL_RUNNER] %s in PROVIDERS_WITHOUT_TOOLS — skipping bind_tools",
                    selected_model)
        return llm, False

    try:
        tools_lc = [_to_openai_tool(t) for t in TOOL_REGISTRY]
        bound = llm.bind_tools(tools_lc)
        return bound, True
    except Exception as exc:
        logger.warning("[TOOL_RUNNER] bind_tools failed for %s: %s — using text-only path",
                       selected_model, exc)
        return llm, False


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _extract_text(response: Any) -> str:
    """Pull text content out of a LangChain AIMessage, handling both string and
    block-list `.content` formats (Anthropic returns a list of content blocks)."""
    if response is None:
        return ""
    content = getattr(response, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content)


def _extract_tool_calls(response: Any) -> List[dict]:
    """Normalize tool_calls from the response into a list of {name, args} dicts.

    Sources checked, in order:
      1. response.tool_calls          — LangChain's normalized field
      2. response.additional_kwargs.tool_calls — older OpenAI-style payload
    Returns [] when none are present.
    """
    if response is None:
        return []

    raw = getattr(response, "tool_calls", None)
    if not raw:
        ak = getattr(response, "additional_kwargs", None) or {}
        if isinstance(ak, dict):
            raw = ak.get("tool_calls") or []
    if not raw:
        return []

    normalised = []
    for tc in raw:
        if isinstance(tc, dict):
            name = tc.get("name") or (tc.get("function") or {}).get("name", "")
            args = tc.get("args")
            if args is None:
                args = (tc.get("function") or {}).get("arguments", {})
                if isinstance(args, str):
                    # OpenAI delivers function arguments as a JSON-encoded string
                    import json as _json
                    try:
                        args = _json.loads(args)
                    except Exception:
                        args = {}
        else:
            name = getattr(tc, "name", "")
            args = getattr(tc, "args", {}) or {}
        if name:
            normalised.append({"name": name, "args": args or {}})
    return normalised


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_llm_with_tools(
    chat_messages: list,
    full_prompt: str = "",
    selected_model: str = "Gemini",
    task_weight: str = "light",
    nodes: list = None,
    pdk: dict = None,
    terminal_nets: dict = None,
) -> dict:
    """Execute an LLM call with tool-binding and dispatcher routing.

    Args:
        chat_messages:  list of {"role", "content"} dicts.
        full_prompt:    fallback prompt when chat_messages is empty.
        selected_model: provider key ("Gemini" / "Alibaba" / etc.).
        task_weight:    "light" or "heavy".
        nodes:          current layout node list.
        pdk:            PDK dict; defaults to load_pdk("saed14nm") in dispatcher.
        terminal_nets:  current {device_id: {D, G, S}} map for topology-aware tools.

    Returns:
        dict with keys:
            text          str            — the LLM's free-form text content
            fc_used       bool           — True iff at least one tool_call was dispatched
            tool_results  list[LayoutToolResult]
                                          — results from each dispatched call (in order)
            updated_nodes list           — final node list after all dispatches
                                          (== nodes when fc_used is False)
            cmd_blocks    list[dict]     — synthesized [CMD]-style dicts for chat_panel compatibility
            tools_bound   bool           — whether bind_tools was actually applied
    """
    from ai_agent.tools.tool_executor import ToolExecutor

    nodes = list(nodes) if nodes is not None else []

    # Build LLM with tools (or without, for Alibaba)
    llm, tools_bound = _build_tool_enabled_llm(selected_model, task_weight)

    # Build LangChain-style messages
    lc_messages = []
    for cm in (chat_messages or []):
        if not isinstance(cm, dict):
            continue
        role = cm.get("role", "user")
        if role not in ("system", "user", "assistant"):
            role = "user"
        content = cm.get("content", "")
        if content:
            lc_messages.append({"role": role, "content": content})
    if not lc_messages:
        lc_messages = [{"role": "user", "content": full_prompt or "Hello"}]

    # Invoke
    t0 = time.time()
    try:
        response = llm.invoke(lc_messages)
    except Exception as exc:
        logger.error("[TOOL_RUNNER] llm.invoke failed: %s", exc)
        return {
            "text":          f"Error: {exc}",
            "fc_used":       False,
            "tool_results":  [],
            "updated_nodes": nodes,
            "cmd_blocks":    [],
            "tools_bound":   tools_bound,
        }
    elapsed = time.time() - t0
    logger.debug("[TOOL_RUNNER] invoke took %.2fs (tools_bound=%s)", elapsed, tools_bound)

    text       = _extract_text(response)
    tool_calls = _extract_tool_calls(response) if tools_bound else []

    # No FC → return text for [CMD]-block fallback
    if not tool_calls:
        return {
            "text":          text,
            "fc_used":       False,
            "tool_results":  [],
            "updated_nodes": nodes,
            "cmd_blocks":    [],
            "tools_bound":   tools_bound,
        }

    # FC path — dispatch every call in order, threading updated nodes through
    tool_results = []
    executor     = ToolExecutor(nodes, terminal_nets=terminal_nets, pdk=pdk)
    changed_any  = False
    changed_calls = []

    for tc in tool_calls:
        name = tc.get("name", "")
        args = tc.get("args", {}) or {}

        result = executor.execute(name, args)
        tool_results.append(result)

        if result.success and result.changed:
            changed_any = True
            changed_calls.append(name)

    cmd_blocks = []
    if changed_any:
        # Synthesize one GUI command carrying the deterministic tool output.
        # This avoids reinterpreting each tool name in symbolic_editor/layout_tab.py
        # and lets primitive, block, and circuit-level tools share the same path.
        cmd_blocks.append({
            "action": "replace_layout",
            "nodes": executor.nodes,
            "source_actions": changed_calls,
            "message": _summarize_changed_calls(tool_results),
        })

    return {
        "text":          text or _summarize_calls(tool_calls, tool_results),
        "fc_used":       True,
        "tool_results":  tool_results,
        "updated_nodes": executor.nodes,
        "cmd_blocks":    cmd_blocks,
        "tools_bound":   tools_bound,
    }


def _summarize_calls(tool_calls: List[dict], tool_results: list) -> str:
    """Build a one-line summary per tool call when the LLM emitted no text."""
    lines = ["Tool calls executed:"]
    for tc, result in zip(tool_calls, tool_results):
        status  = "✓" if result.success else "✗"
        message = getattr(result, "message", "")
        lines.append(f"  {status} {tc.get('name', '?')} — {message}")
    return "\n".join(lines)


def _summarize_changed_calls(tool_results: list) -> str:
    """Compact message for the GUI after applying changed tool output."""
    changed = [r.message for r in tool_results if r.success and r.changed]
    if not changed:
        return "Applied layout tool result."
    if len(changed) == 1:
        return changed[0]
    return f"Applied {len(changed)} layout tool result(s)."

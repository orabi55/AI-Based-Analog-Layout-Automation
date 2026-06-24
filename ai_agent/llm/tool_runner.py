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
import re
import time
from typing import Any, List, Tuple

from ai_agent.utils.logging import vprint, dprint

logger = logging.getLogger("ai_agent")

# Providers that DO NOT support reliable tool binding — text-only path
PROVIDERS_WITHOUT_TOOLS: frozenset = frozenset({"Alibaba"})

# Maximum DRC violation entries kept per block when scrubbing chat history.
# Older violation lists in accumulated context can fill the LLM context window
# on iterative tool-calling loops; this hard cap prevents that.
_MAX_DRC_VIOLATIONS_IN_HISTORY: int = 5

# Pre-compiled pattern: numbered violation lines produced by format_drc_violations_for_llm
# look like "  [1] OVERLAP: ..." or "  [12] ROW_ERROR: ..."
_DRC_VIOLATION_LINE_RE = re.compile(r"^\s+\[\d+\]")


# ---------------------------------------------------------------------------
# DRC context guard
# ---------------------------------------------------------------------------

def _scrub_drc_from_messages(
    messages: list,
    max_violations: int = _MAX_DRC_VIOLATIONS_IN_HISTORY,
) -> list:
    """Truncate DRC violation blocks in accumulated chat history.

    Iterative tool-calling loops append check_overlaps results to chat_messages
    on every pass. Without trimming, hundreds of violation strings from prior
    passes fill the LLM context window and pollute the chat window display.
    This keeps at most *max_violations* numbered entries per DRC block and
    replaces the rest with a single suppression note.
    """
    _DRC_HEADER = "═══ DRC VIOLATIONS"
    scrubbed = []
    for msg in messages:
        content = msg.get("content", "")
        if not isinstance(content, str) or _DRC_HEADER not in content:
            scrubbed.append(msg)
            continue

        lines = content.splitlines()
        out_lines: list[str] = []
        in_block = False
        kept = 0
        suppressed = 0

        for line in lines:
            if _DRC_HEADER in line:
                in_block = True
                kept = 0
                suppressed = 0
                out_lines.append(line)
                continue

            if in_block and _DRC_VIOLATION_LINE_RE.match(line):
                kept += 1
                if kept <= max_violations:
                    out_lines.append(line)
                else:
                    suppressed += 1
            else:
                if in_block and suppressed:
                    out_lines.append(
                        f"  ... ({suppressed} more suppressed from context history)"
                    )
                    suppressed = 0
                    in_block = False
                out_lines.append(line)

        if suppressed:
            out_lines.append(
                f"  ... ({suppressed} more suppressed from context history)"
            )

        scrubbed.append({**msg, "content": "\n".join(out_lines)})
    return scrubbed


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
    progress_cb=None,
    worker=None,
    message_id: str = "",
    oas_path: str = None,
    sp_path: str = None,
) -> dict:
    """Execute an LLM call with tool-binding, optional streaming, and dispatch.

    Args:
        chat_messages:  list of {"role", "content"} dicts.
        full_prompt:    fallback prompt when chat_messages is empty.
        selected_model: provider key ("Gemini" / "Alibaba" / etc.).
        task_weight:    "light" or "heavy".
        nodes:          current layout node list.
        pdk:            PDK dict; defaults to load_pdk("saed14nm") in dispatcher.
        terminal_nets:  current {device_id: {D, G, S}} map for topology-aware tools.
        progress_cb:    legacy per-tool callback (name, success, message).
        worker:         QObject exposing the new streaming/tool signals.  When
                        provided, response_delta + response_done are emitted
                        via stream_llm and tool_started/tool_done bracket each
                        executor.execute() call.
        message_id:     stream identifier matching what the worker emitted in
                        response_started.

    Returns:
        dict with keys:
            text          str            — the LLM's free-form text content
            fc_used       bool           — True iff at least one tool_call was dispatched
            tool_results  list[LayoutToolResult]
            updated_nodes list           — final node list after all dispatches
            cmd_blocks    list[dict]     — replace_layout + any GUI passthroughs
            tools_bound   bool           — whether bind_tools was actually applied
    """
    from ai_agent.tools.tool_executor import ToolExecutor
    from ai_agent.llm.runner import stream_llm

    dprint("[TOOL_RUNNER] run_llm_with_tools start")
    dprint(f"[TOOL_RUNNER] selected_model={selected_model}, task_weight={task_weight}")
    vprint(f"[TOOL_RUNNER] Initiating tool-enabled LLM call (Model: {selected_model})...")

    nodes = list(nodes) if nodes is not None else []

    # Build LLM with tools (or without, for Alibaba)
    llm, tools_bound = _build_tool_enabled_llm(selected_model, task_weight)
    dprint(f"[TOOL_RUNNER] tools_bound={tools_bound}")

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

    lc_messages = _scrub_drc_from_messages(lc_messages)
    dprint("[TOOL_RUNNER] prompts sent to LLM:")
    for idx, msg in enumerate(lc_messages):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        dprint(f"  [{idx}] {role}: {content}")

    # ── 1. Stream the LLM text response (tool_calls land in the merged AIMessage) ──
    t0 = time.time()
    text     = ""
    response = None
    try:
        if worker is not None:
            text, response = stream_llm(
                lc_messages,
                llm,
                message_id,
                worker,
                emit_done=False,
            )
        else:
            response = llm.invoke(lc_messages)
            text     = _extract_text(response)
    except Exception as exc:
        logger.error("[TOOL_RUNNER] llm call failed: %s", exc)
        vprint(f"[TOOL_RUNNER] llm call failed: {exc}")
        return {
            "text":          f"Error: {exc}",
            "fc_used":       False,
            "tool_results":  [],
            "updated_nodes": nodes,
            "cmd_blocks":    [],
            "tools_bound":   tools_bound,
        }
    elapsed = time.time() - t0
    logger.debug("[TOOL_RUNNER] llm took %.2fs (tools_bound=%s, streamed=%s)",
                 elapsed, tools_bound, worker is not None)
    vprint(f"[TOOL_RUNNER] LLM responded in {elapsed:.2f}s (streamed={worker is not None}).")
    dprint(f"[TOOL_RUNNER] raw response: {response}")
    dprint(f"[TOOL_RUNNER] extracted text: {text}")

    tool_calls = _extract_tool_calls(response) if tools_bound else []
    dprint(f"[TOOL_RUNNER] tool_calls extracted: {tool_calls}")

    # ── 2. No FC → return streamed text for [CMD]-block fallback ──
    if not tool_calls:
        vprint("[TOOL_RUNNER] no tool calls; returning text-only response")
        return {
            "text":          text,
            "fc_used":       False,
            "tool_results":  [],
            "updated_nodes": nodes,
            "cmd_blocks":    [],
            "tools_bound":   tools_bound,
        }

    # ── 3. FC path — dispatch every call in order, threading updated nodes ──
    tool_results = []
    executor     = ToolExecutor(
        nodes,
        terminal_nets=terminal_nets,
        pdk=pdk,
        oas_path=oas_path,
        sp_path=sp_path,
    )
    changed_any  = False
    changed_calls = []

    for tc in tool_calls:
        name = tc.get("name", "")
        args = tc.get("args", {}) or {}

        vprint(f"[TOOL_RUNNER] tool call -> {name}")
        dprint(f"[TOOL_RUNNER] tool args -> {args}")

        # Legacy tool_progress callback (string-typed signal)
        if progress_cb:
            try:
                progress_cb(name, None, "")
            except Exception:
                pass
        # New tool_started signal (typed dict args)
        if worker is not None:
            try:
                worker.tool_started.emit(
                    str(name),
                    args if isinstance(args, dict) else {"_raw": str(args)},
                )
            except Exception:
                pass

        result = executor.execute(name, args)
        tool_results.append(result)

        vprint(
            "[TOOL_RUNNER] tool response -> "
            f"success={bool(result.success)} "
            f"changed={bool(result.changed)}"
        )
        dprint(
            "[TOOL_RUNNER] tool detailed response -> "
            f"message={result.message} "
            f"metrics={result.metrics}"
        )

        if progress_cb:
            try:
                progress_cb(name, result.success, result.message)
            except Exception:
                pass
        if worker is not None:
            try:
                worker.tool_done.emit(
                    str(name),
                    {
                        "success": bool(result.success),
                        "message": str(result.message or ""),
                        "changed": bool(result.changed),
                        "metrics": dict(result.metrics or {}),
                    },
                )
            except Exception:
                pass

        if result.success and result.changed:
            changed_any = True
            changed_calls.append(name)

    cmd_blocks = []
    if changed_any:
        # Synthesize one GUI command carrying the deterministic tool output.
        cmd_blocks.append({
            "action":         "replace_layout",
            "nodes":          executor.nodes,
            "source_actions": changed_calls,
            "message":        _summarize_changed_calls(tool_results),
        })
        vprint(f"[TOOL_RUNNER] replace_layout cmd_blocks added: {changed_calls}")

    # Any tool can embed extra GUI-only commands via metrics["gui_commands"].
    # Emit them directly so the editor handles them without going through
    # replace_layout (e.g. create_group which doesn't mutate node positions).
    for result in tool_results:
        for gui_cmd in (result.metrics.get("gui_commands") or []):
            if isinstance(gui_cmd, dict) and gui_cmd.get("action"):
                cmd_blocks.append(gui_cmd)
                vprint(f"[TOOL_RUNNER] gui command emitted: {gui_cmd}")

    # ── 4. Second LLM round: feed tool outputs back to LLM to get a conversational reply ──
    final_text = ""
    if tools_bound and tool_calls:
        try:
            from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
            vprint("[TOOL_RUNNER] Building message history for second LLM turn...")

            second_messages = []
            for msg in lc_messages:
                role = msg.get("role")
                content = msg.get("content", "")
                if role == "system":
                    second_messages.append(SystemMessage(content=content))
                elif role == "user":
                    second_messages.append(HumanMessage(content=content))
                elif role == "assistant":
                    second_messages.append(AIMessage(content=content))

            # Append the first AIMessage containing the tool calls
            second_messages.append(response)

            # Retrieve raw tool calls to align tool_call_id
            raw_tool_calls = getattr(response, "tool_calls", []) or []
            if not raw_tool_calls:
                ak = getattr(response, "additional_kwargs", None) or {}
                if isinstance(ak, dict):
                    raw_tool_calls = ak.get("tool_calls") or []

            # Append a ToolMessage for each tool call
            for idx, (tc, res) in enumerate(zip(tool_calls, tool_results)):
                tc_id = None
                if idx < len(raw_tool_calls):
                    raw_tc = raw_tool_calls[idx]
                    if isinstance(raw_tc, dict):
                        tc_id = raw_tc.get("id") or raw_tc.get("tool_call_id")
                    else:
                        tc_id = getattr(raw_tc, "id", None) or getattr(raw_tc, "tool_call_id", None)

                if not tc_id:
                    tc_id = f"call_{idx}_{tc.get('name')}"

                tool_output = str(res.message or "")
                if not tool_output.strip():
                    tool_output = "Success" if res.success else "Failed"

                second_messages.append(ToolMessage(
                    content=tool_output,
                    tool_call_id=tc_id,
                    name=tc.get("name")
                ))

            vprint("[TOOL_RUNNER] Invoking second-round LLM for final text...")
            if worker is not None:
                final_text, _ = stream_llm(
                    second_messages,
                    llm,
                    message_id,
                    worker,
                    emit_done=False,
                )
            else:
                second_response = llm.invoke(second_messages)
                final_text = _extract_text(second_response)
            vprint(f"[TOOL_RUNNER] Second-round LLM response: {final_text}")
        except Exception as exc:
            import traceback
            vprint(f"[TOOL_RUNNER] Second round LLM call failed: {exc}\n{traceback.format_exc()}")
            final_text = ""

    return {
        "text":          final_text or text or _summarize_calls(tool_calls, tool_results),
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
    changed = [_compact_result_message(r.message) for r in tool_results if r.success and r.changed]
    if not changed:
        return "Applied layout tool result."
    if len(changed) == 1:
        return changed[0]
    return f"Applied {len(changed)} layout tool result(s)."


def _compact_result_message(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return "Layout updated."
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) <= 3 and len(text) <= 240:
        return text
    first = lines[0] if lines else "Layout updated."
    completed = sum(1 for line in lines[1:] if line.startswith("✓") or line.startswith("  ✓"))
    failed = sum(1 for line in lines[1:] if line.startswith("✗") or line.startswith("  ✗"))
    if completed or failed:
        suffix = f"{completed} step(s)"
        if failed:
            suffix += f", {failed} failed"
        return f"{first} ({suffix})."
    return first if len(first) <= 240 else first[:237] + "..."

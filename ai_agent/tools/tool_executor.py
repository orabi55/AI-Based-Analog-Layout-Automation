"""
Tool Executor
=============
Small stateful wrapper around dispatcher.dispatch.

Both the built-in chatbot and the MCP server use this class so function calls
thread the same updated node list through a sequence of deterministic tools.
"""
from __future__ import annotations

from ai_agent.core.interfaces import LayoutToolResult
from ai_agent.tools.dispatcher import dispatch


class ToolExecutor:
    """Execute layout tools while carrying the current node state forward."""

    def __init__(
        self,
        nodes: list | None = None,
        *,
        terminal_nets: dict | None = None,
        pdk: dict | None = None,
    ):
        self.nodes = list(nodes) if nodes is not None else []
        self.terminal_nets = terminal_nets if isinstance(terminal_nets, dict) else {}
        self.pdk = pdk

    def execute(self, tool_name: str, arguments: dict | None = None) -> LayoutToolResult:
        result = dispatch(
            tool_name,
            arguments or {},
            self.nodes,
            self.pdk,
            terminal_nets=self.terminal_nets,
        )
        if result.success and result.changed:
            self.nodes = list(result.nodes)
        return result

    def execute_many(self, tool_calls: list) -> dict:
        """Execute a list of {name, args} dicts in order, threading node state.

        Returns:
            {
              "tool_results":  list[LayoutToolResult],
              "updated_nodes": list,
              "changed":       bool,
              "replace_layout": dict | None  — GUI command when layout changed
            }
        """
        results   = []
        changed   = False
        changed_names: list = []

        for tc in (tool_calls or []):
            name = tc.get("name") or tc.get("tool_name", "")
            args = tc.get("args") or tc.get("arguments") or {}
            r    = self.execute(name, args)
            results.append(r)
            if r.success and r.changed:
                changed = True
                changed_names.append(name)

        replace_cmd = None
        if changed:
            msg = "; ".join(
                f"✓ {n}" for n, r in zip(changed_names, results)
                if r.success and r.changed
            )
            replace_cmd = {
                "action":         "replace_layout",
                "nodes":          list(self.nodes),
                "source_actions": changed_names,
                "message":        msg or "Layout updated",
            }

        return {
            "tool_results":   results,
            "updated_nodes":  list(self.nodes),
            "changed":        changed,
            "replace_layout": replace_cmd,
        }

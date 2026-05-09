"""
MCP server for the analog layout tool layer.

Claude Desktop path:
    Claude Desktop -> this server -> ToolExecutor -> dispatcher/core tools
    -> layout_state.json

The server exposes the same TOOL_REGISTRY used by the built-in chatbot. Each
tool reads the current layout state, executes through ToolExecutor, and writes
layout_state.json back when the tool changes the node list.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ai_agent.core.layout_state import load_layout_state, save_layout_state
from ai_agent.tools.schemas import TOOL_REGISTRY
from ai_agent.tools.tool_executor import ToolExecutor

server = Server("analog-layout-tools")


def _default_state_path() -> Path:
    env_path = os.environ.get("LAYOUT_STATE_PATH")
    if env_path:
        return Path(env_path).expanduser().resolve()

    candidates = [
        Path.cwd() / "layout_state.json",
        _REPO_ROOT.parent / "layout_state.json",
        _REPO_ROOT / "layout_state.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def _state_path(arguments: dict[str, Any] | None) -> Path:
    if isinstance(arguments, dict) and arguments.get("layout_state_path"):
        return Path(str(arguments["layout_state_path"])).expanduser().resolve()
    return _default_state_path()


def _schema_for_mcp(schema: dict) -> dict:
    input_schema = copy.deepcopy(schema.get("input_schema") or {})
    input_schema.setdefault("type", "object")
    props = input_schema.setdefault("properties", {})
    props.setdefault(
        "layout_state_path",
        {
            "type": "string",
            "description": (
                "Optional path to layout_state.json. Defaults to LAYOUT_STATE_PATH, "
                "then the nearest workspace layout_state.json."
            ),
        },
    )
    input_schema.setdefault("required", [])
    return input_schema


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name=schema["name"],
            description=schema.get("description", ""),
            inputSchema=_schema_for_mcp(schema),
        )
        for schema in TOOL_REGISTRY
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
    args = dict(arguments or {})
    state_path = _state_path(args)
    args.pop("layout_state_path", None)

    state = load_layout_state(str(state_path))
    nodes = state.get("nodes") if isinstance(state.get("nodes"), list) else []
    terminal_nets = state.get("terminal_nets") if isinstance(state.get("terminal_nets"), dict) else {}

    executor = ToolExecutor(nodes, terminal_nets=terminal_nets)
    result = executor.execute(name, args)

    if result.success and result.changed:
        updated_state = dict(state)
        updated_state["nodes"] = executor.nodes
        updated_state["terminal_nets"] = terminal_nets
        save_layout_state(updated_state, str(state_path))

    payload: dict[str, Any] = {
        "success": result.success,
        "message": result.message,
        "changed": result.changed,
        "layout_state_path": str(state_path),
        "node_count": len(executor.nodes),
        "metrics": result.metrics,
        "warnings": result.warnings,
    }
    if name == "read_layout":
        payload["nodes"] = executor.nodes
    elif name == "save_layout" and result.metrics.get("serialized"):
        payload["serialized"] = result.metrics["serialized"]

    return [
        TextContent(
            type="text",
            text=json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        )
    ]


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())

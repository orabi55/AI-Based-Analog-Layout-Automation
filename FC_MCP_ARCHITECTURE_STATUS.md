# Function Calling and MCP Architecture Status

Date: 2026-05-09

## Short Answer

Function calling is being used now for the concrete chat path.

When a user asks a direct layout command such as "move MM3", "swap MM3 and MM5",
"detect the differential pair", or "place the current mirror", the GUI routes
to the FC-enabled worker path:

```text
ChatPanel
  -> OrchestratorWorker.process_request_with_tools
  -> run_llm_with_tools
  -> ToolExecutor
  -> dispatcher.py
  -> ai_agent/core deterministic layout tools
  -> replace_layout command
  -> LayoutEditorTab refresh
```

The LangGraph pipeline path is still separate. Requests matching pipeline
keywords such as "optimize", "auto-place", "fix DRC", "improve", or "pipeline"
still route to the existing orchestrator:

```text
ChatPanel
  -> OrchestratorWorker.process_orchestrated_request
  -> LangGraph chat_app
  -> topology / strategy / placement / DRC / routing nodes
```

That means the final desired architecture is partially implemented: the GUI
concrete-command path and Claude MCP path now share the same deterministic
hands, but the full LangGraph pipeline has not yet been converted to formal
tool_calls internally.

## What Was Already Present From The Previous Chat

- `symbolic_editor/chat_panel.py` had been changed to add
  `request_inference_with_tools`.
- `ChatPanel` connects that signal to
  `OrchestratorWorker.process_request_with_tools`.
- `ChatPanel.set_layout_context()` syncs nodes and terminal nets to the worker.
- Non-pipeline requests route to `_call_llm()`, which emits the FC-enabled
  signal.
- `ai_agent/llm/tool_runner.py` binds `TOOL_REGISTRY` where the provider
  supports tools.
- `ai_agent/tools/schemas.py` defines 39 tools:
  primitive tools, block tools, advanced circuit-level tools, physical-cell
  tools, passive-device tools, validation tools, and persistence.
- `ai_agent/tools/dispatcher.py` routes those tool names to deterministic
  implementations under `ai_agent/core`.

## Fixes Made In This Pass

- Passed `terminal_nets` from `LLMWorker.process_request_with_tools()` into
  `run_llm_with_tools()`. Without this, topology-aware tools such as
  `detect_differential_pairs`, `detect_current_mirrors`,
  `detect_cross_coupled_pairs`, and `detect_circuit_type` always saw an empty
  net map.
- Added `ai_agent/tools/tool_executor.py` as the shared stateful executor used
  by both the chatbot FC path and the MCP server.
- Changed the FC result handoff so changed tools emit one deterministic
  `replace_layout` GUI command containing the updated node list, instead of
  asking the GUI to reinterpret every tool name.
- Added `replace_layout` / `apply_layout_state` handling in
  `symbolic_editor/layout_tab.py`, so primitive, block, and circuit-level tools
  can all refresh the symbolic editor through the same route.
- Updated focused tests for the expanded registry, terminal-net forwarding,
  and the new `replace_layout` command shape.
- Added an MCP server at `mcp_server/server.py`.
- Created Claude Desktop config at:
  `C:\Users\abdoa\AppData\Roaming\Claude\claude_desktop_config.json`

## MCP Configuration

The Claude Desktop server entry is:

```json
{
  "mcpServers": {
    "analog-layout-tools": {
      "command": "py",
      "args": [
        "-B",
        "H:\\GP\\General\\AI-Based-Analog-Layout-Automation\\mcp_server\\server.py"
      ],
      "cwd": "H:\\GP\\General\\AI-Based-Analog-Layout-Automation",
      "env": {
        "LAYOUT_STATE_PATH": "H:\\GP\\General\\layout_state.json"
      }
    }
  }
}
```

Claude Desktop should be restarted after this config change.

The MCP path is now:

```text
Claude Desktop
  -> mcp_server/server.py
  -> ToolExecutor
  -> dispatcher.py
  -> ai_agent/core deterministic layout tools
  -> H:\GP\General\layout_state.json
```

## Verification

- Syntax check passed for the modified FC, GUI, dispatcher, core, and MCP files.
- MCP import smoke test passed:
  - server defaulted to `H:\GP\General\layout_state.json`
  - exposed 39 tools
  - `list_devices` returned 62 devices from the current state file
- Focused tests:
  - `py -m pytest tests/test_tool_runner.py tests/test_dispatcher.py -q -k "not test_save_layout_to_file"`
  - Result: 72 passed, 1 deselected
- The deselected test was skipped only because pytest's `tmp_path` fixture hit
  Windows permission errors in the temp directory. The code path itself was not
  the failing part.

## Important Notes

- The exact file from the pasted context,
  `C:\Users\abdoa\.claude\projects\h--GP-General\memory\MEMORY.md`, was not
  present on disk, and a recursive search under
  `C:\Users\abdoa\.claude\projects` found no `MEMORY.md`.
- Alibaba/Qwen is still intentionally text-only because that provider is in
  `PROVIDERS_WITHOUT_TOOLS`; it falls back to `[CMD]...[/CMD]` parsing.
- Source control has many broader pre-existing modified/untracked files in the
  agent pipeline, examples, tests, and `ai_agent/core`. This pass only fixed
  the FC/MCP handoff issues and did not normalize those unrelated changes.

## Remaining Architecture Gap

The fully desired built-in path says:

```text
OrchestratorWorker
  -> Existing LangGraph pipeline
  -> Router / Intent Classifier
  -> Topology Analyzer
  -> Strategy Selector
  -> Placement Specialist
  -> Formal tool_calls
  -> ToolExecutor
```

The current code does not yet make the LangGraph placement pipeline emit formal
tool_calls into `ToolExecutor`. The pipeline still has its own graph-state and
visual-review command flow. The concrete chatbot path and MCP path now share
`ToolExecutor`; the full LangGraph path is the next piece to unify.

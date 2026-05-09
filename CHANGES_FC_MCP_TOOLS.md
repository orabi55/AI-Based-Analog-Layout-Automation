# FC · MCP · Tool Layer — Change Log

> Branch: `new_main`  
> Date: 2026-05-10  
> All 73 automated tests pass (`py -m pytest tests/test_tool_runner.py tests/test_dispatcher.py`).

---

## 1  Overview

This set of changes unifies the chatbot, the LangGraph placement pipeline, and
Claude Desktop (MCP) around a shared deterministic tool layer.  
Three user-facing improvements:

| Before | After |
|--------|-------|
| Chatbot only had text-based `[CMD]` commands | Chatbot now has **FC mode** (function-calling) **and** CMD mode — user can toggle |
| No MCP server | **Claude Desktop** can call all 39 layout tools via MCP |
| Detection / placement APIs were internal-only | **39 tools** exposed at every level (chat · MCP · tests) |

---

## 2  New Files

### `ai_agent/core/` — pure-Python deterministic layout logic

| File | What it does |
|------|--------------|
| `__init__.py` | Package marker |
| `interfaces.py` | `LayoutToolResult` dataclass + `@wrap_tool` exception-safe decorator |
| `drc.py` | DRC engine extracted from `agents/drc_critic.py` (zero logic change) |
| `topology.py` | `analyze_json`, `extract_symmetry_block` from `agents/topology_analyst.py` |
| `routing.py` | `build_routing_report`, `score_routing` from `agents/routing_previewer.py` |
| `strategy.py` | `parse_placement_mode` from `agents/strategy_selector.py` |
| `physical_cells.py` | `insert_endcaps`, `insert_taps`, `insert_fillers`, `insert_all_physical_cells` |
| `common_centroid.py` | `place_common_centroid`, `place_common_centroid_2d`, `insert_dummies_around_group` |
| `passive_placer.py` | `place_resistor`, `place_mom_cap`, `place_mos_cap`, `reshape_passive` |
| `circuit_detection.py` | **New** — `detect_matched_pairs`, `detect_differential_pairs`, `detect_current_mirrors`, `detect_cross_coupled_pairs`, `detect_circuit_type`, `validate_symmetry`, `validate_dummy_presence` |
| `group_placer.py` | **New** — `place_matched_pair`, `place_differential_pair`, `place_current_mirror`, `add_dummy_group` |
| `circuit_orchestrator.py` | **New** — `place_comparator`, `place_tx_driver`, `run_full_layout_pipeline`, `optimize_layout_for_matching`, `optimize_layout_for_routing` |
| `layout_state.py` | `save_layout_state`, `load_layout_state`, `state_exists`, `clear_layout_state` |
| `handoff_report.py` | 5-section handoff report with HTML rendering |

### `ai_agent/pdks/`

| File | What it does |
|------|--------------|
| `__init__.py` | Package marker |
| `loader.py` | `load_pdk("saed14nm")`, `get_rule()`, confidence tiers (confirmed / literature / heuristic) |

### `ai_agent/tools/` — new tool layer

| File | What it does |
|------|--------------|
| `schemas.py` | **39 tools** in Anthropic tool-schema format (single source of truth for chat + MCP) |
| `dispatcher.py` | `dispatch(tool_name, args, nodes, pdk, terminal_nets)` — routes all 39 tool names to `core/` implementations, never raises |
| `tool_executor.py` | `ToolExecutor` — stateful wrapper that threads `nodes` through sequential `execute()` / `execute_many()` calls; shared by chatbot FC path and MCP server |

### `ai_agent/llm/tool_runner.py`

Function-calling runner:

- `run_llm_with_tools()` — binds `TOOL_REGISTRY` to LLM, dispatches `tool_calls` through `ToolExecutor`, emits **one** `replace_layout` GUI command when layout changes
- `PROVIDERS_WITHOUT_TOOLS = {"Alibaba"}` — Alibaba/Qwen skips `bind_tools`, falls back to `[CMD]` text parsing
- `_to_openai_tool()` — converts Anthropic schema → OpenAI format for LangChain `bind_tools`

### `mcp_server/server.py`

Async MCP server exposing all 39 tools to Claude Desktop:

- Reads / writes `layout_state.json` (path from `LAYOUT_STATE_PATH` env var)
- Each tool call goes through `ToolExecutor → dispatcher → core/`
- Adds optional `layout_state_path` parameter to every tool for per-call override
- Smoke-tested: lists 39 tools, reads 62 devices from the current state file

---

## 3  Modified Files

### `symbolic_editor/chat_panel.py`

| Change | Detail |
|--------|--------|
| **FC / CMD toggle button** | Green "FC" / amber "CMD" button in the chat header. Toggles `self._chat_mode`. |
| `request_inference_with_tools` signal | New signal → `process_request_with_tools` |
| `_llm_worker.command_ready` connected | FC tool-call results route directly to the editor |
| `set_layout_context` syncs worker | `terminal_nets` forwarded so topology-aware tools work |
| `_call_llm()` — FC path | Uses `build_fc_system_prompt`; emits `request_inference_with_tools` |
| `_call_llm_cmd()` — CMD path | **Restored** original path: uses `build_system_prompt`; emits `request_inference` → `MultiAgentOrchestrator` → `[CMD]` blocks |
| Routing in `send_message` | Pipeline keywords → orchestrator (always); otherwise routes by `_chat_mode` |

### `ai_agent/agents/prompts.py`

Added `build_fc_system_prompt(layout_context)`:

- Tells the LLM it has **direct tool access** and must call tools to fulfil requests
- Lists all tool categories with usage cues
- States the full device list is already in context → do NOT call `list_devices` first
- Includes `_format_layout_context` output (IDs, types, positions for all devices)

### `ai_agent/llm/workers.py`

- `process_request_with_tools` slot added to `LLMWorker`
- Forwards `terminal_nets` from `_layout_context` into `run_llm_with_tools`

### `symbolic_editor/layout_tab.py`

- `replace_layout` / `apply_layout_state` handler added to `_handle_ai_command`
- Does a wholesale node swap + undo push, then refreshes all panels

### `ai_agent/graph/state.py`

- Added `groups: List[Dict[str, Any]]` field to `LayoutState` TypedDict

### `ai_agent/agents/drc_critic.py` / `topology_analyst.py` / `routing_previewer.py` / `strategy_selector.py`

Converted to shims — all logic moved to `ai_agent/core/`; shims re-export for backward compatibility.

### `ai_agent/nodes/drc_critic.py` / `topology_analyst.py` / `routing_previewer.py` / `strategy_selector.py`

Updated imports to use `ai_agent.core.*` directly.

### `ai_agent/SKILLS/__init__.py`

Fixed import casing: `from ai_agent.skills.loader` → `from ai_agent.SKILLS.loader`.

### `ai_agent/matching/matching_engine.py`

Deleted — content merged into `ai_agent/matching/engine.py`.

---

## 4  MCP Configuration (Claude Desktop)

Both config paths are written to ensure Claude Desktop picks up the server:

**Packaged app** (active install on this machine):  
`C:\Users\abdoa\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`

**Plain roaming fallback**:  
`C:\Users\abdoa\AppData\Roaming\Claude\claude_desktop_config.json`

Both contain:

```json
{
  "mcpServers": {
    "analog-layout-tools": {
      "command": "py",
      "args": ["-B", "H:\\GP\\General\\AI-Based-Analog-Layout-Automation\\mcp_server\\server.py"],
      "cwd": "H:\\GP\\General\\AI-Based-Analog-Layout-Automation",
      "env": { "LAYOUT_STATE_PATH": "H:\\GP\\General\\layout_state.json" }
    }
  }
}
```

**Restart Claude Desktop after any config change.**

---

## 5  The 39 Tools

### Primitive (hands)
`read_layout` · `list_devices` · `get_device_info` · `score_layout`  
`move_device` · `swap_devices` · `flip_device` · `add_dummy` · `remove_dummies`  
`check_overlaps` · `run_legalizer`  
`insert_endcaps` · `insert_taps` · `insert_fillers` · `insert_all_physical_cells`  
`place_common_centroid` · `place_common_centroid_2d` · `insert_dummies_around_group`  
`place_resistor` · `place_mom_cap` · `place_mos_cap` · `reshape_passive`  
`save_layout`

### Block / Mid-level (layout skills)
`detect_matched_pairs` · `detect_differential_pairs` · `detect_current_mirrors`  
`detect_cross_coupled_pairs`  
`place_matched_pair` · `place_differential_pair` · `place_current_mirror`  
`add_dummy_group` · `validate_symmetry` · `validate_dummy_presence`

### Advanced / Circuit-level (full workflow)
`detect_circuit_type` · `place_comparator` · `place_tx_driver`  
`run_full_layout_pipeline` · `optimize_layout_for_matching` · `optimize_layout_for_routing`

---

## 6  Routing Summary

```
User message
  │
  ├─ "optimize / auto-layout / fix drc / …"  →  LangGraph pipeline (unchanged)
  │   └─ Topology → Strategy → Placement → DRC → Routing
  │
  ├─ FC mode (green button, default)
  │   └─ run_llm_with_tools → ToolExecutor → dispatcher → core/
  │       └─ replace_layout command → editor node swap
  │
  └─ CMD mode (amber button)
      └─ MultiAgentOrchestrator → run_llm (text-only)
          └─ [CMD]{…}[/CMD] blocks → extract_cmd_blocks → editor

Claude Desktop (MCP)
  └─ mcp_server/server.py → ToolExecutor → dispatcher → core/
      └─ layout_state.json  (read/write)
```

---

## 7  Tests

| Suite | Count |
|-------|-------|
| `tests/test_tool_runner.py` | 18 tests |
| `tests/test_dispatcher.py` | 55 tests |
| **Total** | **73 passed** |

New test files (untracked, not yet run in CI):  
`test_common_centroid.py` · `test_dispatcher.py` · `test_handoff_report.py`  
`test_layout_state.py` · `test_passive_placer.py` · `test_physical_cells.py`  
`test_tool_runner.py`

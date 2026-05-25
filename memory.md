# Project Memory — AI-Based Analog Layout Automation

## What This Project Does

An AI-driven pipeline that automates analog IC layout generation. A user describes a circuit (or loads a SPICE netlist) and the system places devices, checks DRC, applies matching/symmetry constraints, and produces a legal layout — without manual intervention.

---

## Golden Rules (Read Before Any Change)

1. **Use existing GUI functions — never rewrite them from scratch.**  
   The GUI already has correct, tested implementations for dummies, taps, and physical cells. If an AI tool needs to do the same thing, mirror the GUI's logic exactly (same node structure, same ID convention, same field names). A poorly written from-scratch version produces wrong geometry and mismatches the renderer.

2. **Never auto-insert structural dummies from the pipeline.**  
   `insert_dummies_around_group()` (common_centroid.py) and the structural dummy loop that was in `circuit_orchestrator.py` have been removed. The structural dummy approach produces wrong geometry. Dummies must be placed via the GUI interactive mode (`set_dummy_mode`) or via the `add_dummy` tool, which now mirrors `_build_dummy_node`.

3. **Before writing a new tool function, search reference books/papers first.**  
   If a new layout algorithm is needed (e.g., resistor folding, capacitor interleaving, shielding), look up the standard IC layout technique in a reference (Razavi, Hastings, Baker, etc.) before coding. A wrong implementation from intuition will produce layout that fails DRC or violates matching constraints.

4. **Tools operate on the backend; GUI operates on the frontend — keep them in sync.**  
   The backend tools (dispatcher → core/) and the GUI (layout_tab → editor_view) share the same node dict format. Any change to a node field in one must be reflected in the other. The canonical node structure is defined by the GUI's `_build_dummy_node` / `_build_tap_node` — those are the ground truth.

---

## Dual-Mode Architecture

The core layout engine is shared. Two entry points exist:

### 1. Embedded Qt Chatbot (Local App)
- User interacts via a chat panel inside a native Qt window
- Background workers (`LLMWorker`, `OrchestratorWorker`, `PlacementWorker`) run on QThreads
- Chat calls → `run_llm_with_tools()` in `ai_agent/llm/tool_runner.py`
- Tools dispatched via `ToolExecutor` → `dispatcher.dispatch()` → `ai_agent/core/*`
- Layout changes stream back to the canvas via `visual_viewer_signal`
- History accumulates in `chat_messages` list passed between iterations

### 2. MCP Server (External Clients — e.g. Claude Desktop)
- Launch `mcp_server/server.py`
- Exposes the full `TOOL_REGISTRY` as MCP tools over `stdio_server`
- External LLM calls tools → `ToolExecutor.execute()` → same core backend
- State persisted to/from `layout_state.json` (the rendezvous file)
- Returns JSON payload as `TextContent` back to the host interface

---

## Key File Map

| Path | Role |
|------|------|
| `ai_agent/llm/tool_runner.py` | FC entry point; binds TOOL_REGISTRY, streams LLM, dispatches tool calls |
| `ai_agent/llm/workers.py` | `LLMWorker`, `OrchestratorWorker` — QThread workers for chat & graph pipeline |
| `ai_agent/llm/placement_worker.py` | `PlacementWorker` — initial placement LangGraph run |
| `ai_agent/llm/runner.py` | Base text-only `run_llm()` / `stream_llm()` |
| `ai_agent/llm/factory.py` | Provider-agnostic LLM factory (Gemini, OpenAI, Alibaba, Ollama) |
| `mcp_server/server.py` | MCP stdio server — maps TOOL_REGISTRY to Claude Desktop |
| `ai_agent/tools/schemas.py` | `TOOL_REGISTRY` — 70+ Anthropic-format tool schemas |
| `ai_agent/tools/dispatcher.py` | `dispatch()` — routes tool name → core function |
| `ai_agent/tools/tool_executor.py` | `ToolExecutor` — holds nodes + terminal_nets, calls dispatcher |
| `ai_agent/core/drc.py` | `run_drc_check()`, `compute_prescriptive_fixes()`, `DRCViolation` |
| `ai_agent/core/layout_state.py` | `save_layout_state()`, `load_layout_state()` |
| `ai_agent/core/circuit_orchestrator.py` | High-level FC pipeline tools (comparator, tx_driver, full pipeline) |
| `ai_agent/graph/state.py` | `LayoutState` TypedDict — shared LangGraph pipeline state |
| `ai_agent/graph/builder.py` | `build_layout_graph()` — assembles the LangGraph app |
| `ai_agent/nodes/drc_critic.py` | `node_drc_critic()` — LangGraph node wrapping DRC + auto-fix loop |
| `ai_agent/agents/drc_critic.py` | `format_drc_violations_for_llm()` — formats violations for LLM prompt |
| `ai_agent/agents/placement_specialist.py` | `build_placement_context()` — system prompt builder |
| `symbolic_editor/main.py` | `MainWindow` — Qt app shell (1200+ lines) |
| `symbolic_editor/layout_tab.py` | `LayoutEditorTab` — per-circuit tab with canvas + chat panel |
| `symbolic_editor/editor_view.py` | `SymbolicEditor(QGraphicsView)` — canvas: zoom, pan, dummy/tap placement |
| `symbolic_editor/chat_panel.py` | Chat UI widget — emits `process_request` signal to worker |

---

## Existing GUI Dummy & Tap System (Use This — Do Not Rewrite)

### Interactive placement flow
1. User clicks toolbar button → `layout_tab.set_dummy_mode(True)` / `set_vdd_mode` / `set_gnd_mode`
2. `editor_view.set_dummy_mode(True)` enables mouse tracking and preview rendering
3. As mouse moves: `_compute_dummy_candidate()` / `_compute_tap_candidate()` snaps to grid
4. On click: callback fired → `layout_tab._add_dummy_device()` / `_add_tap_device()`
5. Node built by `_build_dummy_node()` / `_build_tap_node()` and appended to `self.nodes`

### Canonical dummy node format (from `_build_dummy_node`, layout_tab.py:1899)
```python
{
    "id":           "DUMMYN1",          # DUMMYP{n} for pmos, DUMMYN{n} for nmos
    "type":         "nmos",             # matches row type
    "is_dummy":     True,
    "dummy_source": "user",             # or "tool" for AI-placed
    "electrical":   {...},              # deep copy from first real device of same type
    "geometry":     {"x": ..., "y": ..., "width": ..., "height": ..., "orientation": "R0"},
    "template_layout_index": ...,       # if template has it
    "layout_cell":  ...,               # if template has it
}
```

### Canonical tap node format (from `_build_tap_node`, layout_tab.py:2009)
```python
{
    "id":           "NTAP1",            # NTAP{n} or PTAP{n}
    "type":         "tap",
    "subtype":      "ntap",             # "ntap" = VDD tap (goes in PMOS row); "ptap" = GND tap (NMOS row)
    "physical_only": True,
    "geometry":     {"x": ..., "y": ..., "width": ..., "height": ..., "orientation": "R0"},
    "template_layout_index": ...,       # if template has it
    "layout_cell":  ...,               # if template has it
}
```

### ID conventions
| Type | GUI ID format | Source file |
|------|---------------|-------------|
| NMOS dummy | `DUMMYN{n}` | `_next_dummy_id`, layout_tab.py:1891 |
| PMOS dummy | `DUMMYP{n}` | `_next_dummy_id`, layout_tab.py:1891 |
| VDD tap (ntap) | `NTAP{n}` | `_next_tap_id` |
| GND tap (ptap) | `PTAP{n}` | `_next_tap_id` |

### AI tool alignment
The `add_dummy` tool in `dispatcher.py` now mirrors `_build_dummy_node` exactly:
- Uses `_next_dummy_id()` helper for sequential `DUMMYP`/`DUMMYN` IDs
- Copies `electrical`, `template_layout_index`, `layout_cell` from the first matching real device
- Sets `dummy_source: "tool"` (vs `"user"` for GUI-placed)

---

## TOOL_REGISTRY Categories

70+ tools grouped as:
- **Layout Inspection**: `read_layout`, `list_devices`, `get_device_info`, `get_layout_bounds`
- **Device Manipulation**: `move_device`, `place_sequence`, `swap_devices`, `flip_device`, `align_devices`
- **Diffusion Sharing**: `abut_devices`, `merge_shared_source`, `merge_shared_drain`
- **DRC & Legalisation**: `check_overlaps`, `run_legalizer`
- **Physical Cells**: `insert_endcaps`, `insert_taps`, `insert_fillers`, `insert_all_physical_cells`
- **Matching / Common-Centroid**: `place_common_centroid`, `place_matched_pair`, `place_differential_pair`, `place_current_mirror`
- **Passives**: `place_resistor`, `place_mom_cap`, `place_mos_cap`, `reshape_passive`
- **Circuit Detection**: `detect_matched_pairs`, `detect_differential_pairs`, `detect_current_mirrors`
- **Advanced Pipelines**: `place_comparator`, `place_tx_driver`, `run_full_layout_pipeline`
- **Persistence**: `save_layout`, `read_layout`

Providers without tool-binding (text/CMD path only): `Alibaba` (Qwen).

---

## DRC System

**Core**: `ai_agent/core/drc.py`

- `run_drc_check(nodes, gap_px, terminal_nets, geometric_tags)` → dict with `pass`, `violations` (strings), `structured` (DRCViolation objects), `summary`
- `compute_prescriptive_fixes(drc_result, ...)` → list of `{action, device, x, y}` move commands
- **Sweep-line O(N log N)** overlap detection with dynamic gap based on `terminal_nets`
- Abutment allowed when devices share a non-power net (gap = 0)
- **Symmetry preserved**: matched group members in `DRCViolation.group_ids` receive the same Δ-vector
- **Prescriptive hints**: each violation includes `text` with exact corrective move coordinates
- `format_drc_violations_for_llm()` in `ai_agent/agents/drc_critic.py` truncates to 2000 chars

**LangGraph node**: `ai_agent/nodes/drc_critic.py::node_drc_critic()` — runs DRC, applies fixes, retries up to N times, tracks attempt count in `state["drc_retry_count"]`.

---

## LangGraph Pipeline (Embedded Path)

Nodes in order:
1. `node_topology_analyst` — detects matched pairs, critical nets, groups
2. `node_placement_specialist` — generates placement using `build_placement_context()`
3. `node_drc_critic` — DRC check + prescriptive fix loop
4. `node_routing_previewer` — routing report
5. `node_human_viewer` — human-in-the-loop interrupt

State flows through `LayoutState` TypedDict. Key fields: `nodes`, `placement_nodes`, `terminal_nets`, `drc_flags`, `drc_pass`, `drc_retry_count`, `geometric_tags`, `placement_goals`, `gap_px`.

---

## Layout Context JSON (passed through pipeline)

```json
{
  "nodes": [...],
  "edges": [...],
  "terminal_nets": { "device_id": {"D": "net", "G": "net", "S": "net"} },
  "sp_file_path": "...",
  "gap_px": 0.0,
  "no_abutment": false,
  "placement_goals": {
    "area": "minimize",
    "matching": {"technique": "common_centroid", "priority": "High"},
    "critical_nets": {"nets": ["input", "output"], "priority": "Medium"}
  },
  "placement_nodes": [...],
  "groups": {...}
}
```

---

## Guardrails (Implemented)

### 1. `tool_runner.py` — DRC history scrubber (embedded path)
**Problem**: Iterative tool-calling accumulates `check_overlaps` results in `chat_messages`. On each pass the full violation list is re-sent to the LLM, filling context and polluting the chat window.

**Fix**: `_scrub_drc_from_messages(lc_messages)` called before every LLM invoke.
- Detects `═══ DRC VIOLATIONS` header blocks in message content
- Keeps first `_MAX_DRC_VIOLATIONS_IN_HISTORY = 5` numbered violation lines
- Replaces remaining with `... (N more suppressed from context history)`
- Regex: `_DRC_VIOLATION_LINE_RE = re.compile(r"^\s+\[\d+\]")`

### 2. `server.py` — MCP payload capper (external path)
**Problem**: A layout off-grid can produce 100+ violations. Without capping, one `call_tool` response exhausts the external LLM's context window.

**Fix**: `_cap_payload_violations(payload)` called after payload dict is built, before `json.dumps()`.
- `payload["warnings"]` list truncated to `_MAX_VIOLATIONS_IN_RESPONSE = 10`
- `payload["message"]` truncated to `_MAX_MESSAGE_LINES = 20` lines
- Works on a shallow copy — original `result` objects never mutated

---

## Known Fixes Applied

| File | Fix |
|------|-----|
| `symbolic_editor/editor_view.py` | **Mouse scroll**: EDA-style cursor-anchored zoom. `setTransformationAnchor(AnchorUnderMouse)` wraps `_apply_zoom_factor` in `wheelEvent` so the scene point under the cursor stays fixed. `event.inverted()` also removed (was misfiring on Windows). Toolbar zoom buttons keep `AnchorViewCenter`. |
| `ai_agent/core/circuit_orchestrator.py` | **Structural dummies removed**: the auto-insertion loop around matched clusters has been deleted. It produced wrong geometry. Use GUI dummy mode or `add_dummy` tool instead. |
| `ai_agent/tools/dispatcher.py` | **`add_dummy` upgraded**: now mirrors `_build_dummy_node` — sequential `DUMMYP`/`DUMMYN` IDs, copies `electrical` + `layout_cell` + `template_layout_index` from real device template, sets `dummy_source: "tool"`. UUID-based IDs removed. |
| `ai_agent/llm/tool_runner.py` | **DRC history scrubber**: `_scrub_drc_from_messages()` truncates old violation blocks to 5 entries before each LLM call. |
| `mcp_server/server.py` | **MCP payload capper**: `_cap_payload_violations()` truncates warnings to 10 and message to 20 lines before returning to external LLM. |

---

## State Persistence

- `layout_state.json` — rendezvous file for MCP path; location resolved by `LAYOUT_STATE_PATH` env var or nearest workspace root
- `save_layout_state()` / `load_layout_state()` in `ai_agent/core/layout_state.py`
- Validates node serializability, rounds floats to 6 decimals
- MCP server saves only when `result.success and result.changed`
- Embedded path: `set_last_initial_state()` / `get_last_initial_state()` in `placement_worker.py` cache the last pipeline state for human-in-the-loop resumption

---

## Provider Matrix

| Provider | Tool Binding | Path |
|----------|-------------|------|
| Gemini | ✓ | FC (Function Calling) |
| VertexGemini | ✓ | FC |
| VertexClaude | ✓ | FC |
| Alibaba (Qwen) | ✗ | Text / [CMD] blocks |
| Ollama | depends | FC or text |

---

## Qt Signals (Embedded Path)

| Signal | Emitter | Payload |
|--------|---------|---------|
| `response_started(msg_id)` | LLMWorker | message ID string |
| `response_delta(msg_id, chunk)` | LLMWorker | streaming text delta |
| `response_done(msg_id, text)` | LLMWorker | full assembled text |
| `tool_started(name, args)` | LLMWorker | tool name + args dict |
| `tool_done(name, result_dict)` | LLMWorker | success/message/changed/metrics |
| `command_ready(dict)` | LLMWorker | `replace_layout` or GUI command |
| `visual_viewer_signal(dict)` | PlacementWorker / OrchestratorWorker | placement/routing payload for canvas |
| `stage_started / stage_delta / stage_done` | OrchestratorWorker | LangGraph node lifecycle |

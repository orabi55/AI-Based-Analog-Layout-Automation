# AI Chat Bot Module — LayoutCopilot

## Overview

The **AI Chat Bot** module (LayoutCopilot) is a **multi-agent AI chatbot system** for an analog IC layout editor. It provides a natural-language interface where users can request layout operations (swap devices, optimize placement, improve matching, etc.) and the system routes those requests through specialized AI agents to produce either conversational responses or executable layout commands.

This module implements a **state-machine-driven multi-agent pipeline** that classifies user intent, analyzes circuit topology, proposes improvements, and generates precise layout commands — all while keeping the GUI responsive through Qt's threading model.

---

## Architecture

```
[GUI: ChatPanel]
    |
    | (Qt Signal: request_inference)
    v
[LLMWorker] (QThread background)
    |
    |-> set_layout_context() ← receives layout data from ChatPanel
    |
    v
[MultiAgentOrchestrator]
    |
    |-> classify_intent() — regex fast-path or LLM call
    |
    +-- CHAT/QUESTION → Chat Agent → reply text
    |
    +-- CONCRETE → CodeGen Agent → [CMD] blocks in reply
    |
    +-- ABSTRACT → Analyzer Agent → Refiner Agent → PAUSE (waiting=True)
                    |
                    | (user responds with approval)
                    v
                    Adapter Agent → CodeGen Agent → [CMD] blocks
    |
    v (Qt Signal: response_ready, command_ready)
[GUI: ChatPanel]
    |
    |-> _parse_commands() → extracts [CMD]{...}[/CMD] blocks
    |-> command_requested.emit(cmd) → executes on canvas
```

---

## File Structure

| File | Lines | Purpose |
|------|-------|---------|
| `llm_worker.py` | ~289 | Qt worker object; manages LLM API calls on a background thread |
| `agents/__init__.py` | — | Package marker for the agents sub-package |
| `agents/classifier.py` | ~108 | Intent classification (regex fast-path + LLM fallback) |
| `agents/orchestrator.py` | ~232 | Central state machine driving the multi-agent pipeline |
| `agents/prompts.py` | ~331 | All system prompts for each agent + analog knowledge base |
| `multi_agent_flowchart.md` | — | Mermaid flowchart documenting the multi-agent workflow |

---

## Detailed File Descriptions

### `llm_worker.py` — LLM Worker (Qt Thread Integration)

Handles all LLM API communication on a background thread to keep the GUI responsive.

#### Key Functions

| Function | Purpose |
|----------|---------|
| `_resolve_sp_file(layout_context, project_root)` | Resolves the correct SPICE netlist file for the current layout. Checks explicit path → matches by cell name → falls back to most recently modified `.sp` file |
| `build_system_prompt(layout_context)` | Backward-compatible wrapper that delegates to `build_chat_prompt` from `prompts.py` |
| `run_llm(chat_messages, full_prompt, selected_model, ollama_model)` | Module-level helper with **automatic retry and exponential backoff** (3 retries, base 2 seconds). Handles transient errors like 429 RESOURCE_EXHAUSTED and 503 UNAVAILABLE |
| `_run_llm_once(...)` | Single-shot LLM call supporting **5 model backends** (see table below) |

#### Supported LLM Backends

| Provider | Client Library | Model | Max Tokens | Temperature |
|----------|----------------|-------|------------|-------------|
| **Gemini** | `google.genai` | `gemini-2.5-flash` | 4096 | 0.4 |
| **OpenAI** | `openai.OpenAI` | `gpt-4o-mini` | 4096 | 0.4 |
| **Ollama** | `requests.post` (local) | `llama3.2` (configurable) | — | 0.4 |
| **Groq** | `groq.Groq` | `llama-3.3-70b-versatile` | 4096 | 0.4 |
| **DeepSeek** | `openai.OpenAI` (compatible) | `deepseek-chat` | 4096 | 0.4 |

#### `LLMWorker` Class

A `QObject` designed to run on a `QThread` using the **Worker-Object Pattern** from Qt.

**Signals:**

| Signal | Emitted When |
|--------|--------------|
| `response_ready(str)` | AI reply text is ready to display in chat |
| `command_ready(dict)` | A `[CMD]` block was parsed and is ready for execution |
| `error_occurred(str)` | An error occurred during LLM communication |

**Slots:**

| Slot | Purpose |
|------|---------|
| `process_request(full_prompt, chat_messages, selected_model, ollama_model)` | Entry point called via Qt signal from GUI. Extracts last user message, retrieves layout context, calls `MultiAgentOrchestrator.process()` |
| `set_layout_context(context)` | Stores layout context (nodes, edges, terminal_nets) for the orchestrator |
| `reset_pipeline()` | Clears orchestrator state when chat is cleared |

**API Key Loading:** Loads `.env` from project root: `Path(__file__).resolve().parent.parent.parent / ".env"`

---

### `agents/classifier.py` — Intent Classifier

Classifies user input into one of four intent categories:

| Intent | Description | Example |
|--------|-------------|---------|
| `concrete` | Direct device operations | "Swap MM28 and MM25" |
| `abstract` | High-level optimization requests | "Improve the matching" |
| `question` | Informational queries | "What topology is this?" |
| `chat` | Conversational | "Hi", "Thanks", "Bye" |

#### Regex Fast-Path

Zero-cost LLM bypass for obvious cases:

| Pattern | Matches |
|---------|---------|
| `_CHAT_RE` | Greetings, thanks, small talk: `hi`, `hello`, `thanks`, `bye`, etc. |
| `_CONCRETE_RE` | Direct device operations: `swap`, `move`, `flip`, `add dummy`, `delete`, `align`, etc. |

#### Classification Flow

```
User Message
    |
    v
[Strip Whitespace]
    |
    v
[Check Regex Fast-Path] → Match? → Return Intent Immediately
    |
    | (No Match)
    v
[LLM Classification] → CLASSIFIER_PROMPT
    |
    v
[Parse Response] → First word, uppercase, strip punctuation
    |
    v
[Return Intent] → Falls back to "abstract" on any error
```

**Design Insight:** The regex fast-path avoids unnecessary LLM API calls for trivial inputs, saving cost and latency.

---

### `agents/orchestrator.py` — Multi-Agent Orchestrator

The central state machine that drives the multi-agent pipeline based on classified intent.

#### PipelineState Enum

| State | Description |
|-------|-------------|
| `IDLE` | Ready for a new user message |
| `WAITING_FOR_REFINER_FEEDBACK` | Paused waiting for user approval/refinement of abstract strategies |

#### MultiAgentOrchestrator Class

**State:** `state` (PipelineState), `_pending_analyzer_output` (cached for Adapter), `_layout_context`

#### Pipeline Routing

| Intent | Handler | LLM Calls | Output |
|--------|---------|-----------|--------|
| `chat` | `_handle_chat()` | 1 (Chat Agent) | Reply text only |
| `question` | `_handle_question()` | 1 (Chat Agent) | Reply text only |
| `concrete` | `_handle_concrete()` | 1 (CodeGen Agent) | Reply text (may contain `[CMD]` blocks) |
| `abstract` | `_handle_abstract()` | 2 (Analyzer + Refiner) | Reply text, sets `waiting=True` |

#### Abstract Request Pipeline (Two-Phase)

**Phase 1 — Analysis & Refinement:**

```
User: "Improve the matching"
    |
    v
[Analyzer Agent]
    - Reads layout context
    - Identifies circuit topology
    - Proposes 2-4 improvement strategies using ANALOG_KB
    |
    v
[Refiner Agent]
    - Formats strategies as numbered options
    - Presents to user for selection
    |
    v
State → WAITING_FOR_REFINER_FEEDBACK (pipeline pauses)
```

**Phase 2 — Adaptation & Code Generation (after user responds):**

```
User: "Option 2, but also add dummy devices"
    |
    v
[Adapter Agent]
    - Maps approved strategy to specific device IDs
    - Produces concrete directives
    |
    v
[CodeGen Agent]
    - Converts directives into [CMD] JSON blocks
    |
    v
State → IDLE (pipeline ready for next request)
```

**Return Format:** All handlers return `{"reply": str, "commands": list, "waiting": bool}`

---

### `agents/prompts.py` — System Prompts & Knowledge Base

Defines all system prompts for each agent, plus an embedded analog layout knowledge base.

#### `ANALOG_KB` — Analog Layout Knowledge Base

A comprehensive knowledge base injected **ONLY** into the Analyzer Agent. Covers:

| Category | Content |
|----------|---------|
| **General Placement** | PMOS top, NMOS bottom, X-pitch = 0.294 μm, row pitch = 0.668 μm |
| **Transistor Abutment** | Diffusion sharing rules, abutment spacing = 0.070 μm |
| **Matching Techniques** | Interdigitation (ABBA pattern), common-centroid, symmetric mirroring |
| **Topology-Specific Rules** | Differential pairs, current mirrors, strong-arm latch, cascode, transmission gates, logic gates, folded-cascode OTA |
| **Parasitic-Aware Placement** | Guidelines for minimizing parasitic capacitance and resistance |

#### Agent Prompts

| Function | Agent | Purpose |
|----------|-------|---------|
| `build_chat_prompt(layout_context)` | Chat Agent | Conversational mode. Instructs LLM to be friendly and **NEVER** output `[CMD]` blocks |
| `build_analyzer_prompt(layout_context)` | Analyzer Agent | Two-step: (1) Identify circuit topology from device data, (2) Propose 2-4 improvements. Includes strict rules against hallucinating non-existent sub-circuits |
| `build_refiner_prompt()` | Refiner Agent | Formats analyzer strategies as numbered options. Asks designer to choose |
| `build_adapter_prompt(layout_context)` | Adapter Agent | Maps approved strategy to concrete device IDs and directives |
| `build_codegen_prompt(layout_context)` | CodeGen Agent | Strict JSON command generator. Defines available actions, abutment rules, coordinate rules, general rules |

#### Utility Functions

| Function | Purpose |
|----------|---------|
| `_compute_grid_info(layout_context)` | Extracts grid parameters from layout nodes: `pitch` (min device width), `pmos_y`, `nmos_y`, `row_pitch` |
| `_format_layout_context(layout_context)` | Converts layout context dict into compact text summary listing all devices with positions, sizes, orientations, electrical parameters (nf, nfin, l, w), and net connections |

---

## Multi-Agent Workflow

```mermaid
flowchart TD
    A[User Input] --> B[Classifier Agent\nGatekeeper]
    
    B -->|Regex Match| C{Intent?}
    B -->|LLM Call| C
    
    C -->|chat| D[Chat Agent\nConversational Reply]
    C -->|question| D
    C -->|concrete| E[CodeGen Agent\nGenerate CMD Blocks]
    C -->|abstract| F[Analyzer Agent\nTopology ID + Strategies]
    
    D --> G[Return Reply Text\nNo Commands]
    E --> H[Return Reply +\n[CMD] Blocks]
    
    F --> I[Refiner Agent\nFormat Numbered Options]
    I --> J[Present Options to User\nState: WAITING]
    
    J --> K[User Selects/Modifies\nOption]
    K --> L[Adapter Agent\nMap to Device IDs]
    L --> E
    
    H --> M[Parse [CMD] Blocks\nin ChatPanel]
    M --> N[Execute Commands\non Layout Canvas]
    
    G --> O[Display in Chat]
    N --> O
```

---

## Command Format ([CMD] Blocks)

The CodeGen agent produces commands in this JSON format embedded in text:

```
[CMD]{"action":"swap","device_a":"MM28","device_b":"MM25"}[/CMD]
[CMD]{"action":"move","device":"MM3","x":1.176,"y":0.0}[/CMD]
[CMD]{"action":"move_row","type":"pmos","y":1.336}[/CMD]
[CMD]{"action":"abut","device_a":"MM6","device_b":"MM29"}[/CMD]
[CMD]{"action":"add_dummy","type":"nmos","count":2,"side":"left"}[/CMD]
```

### Available Actions

| Action | Parameters | Description |
|--------|------------|-------------|
| `swap` | `device_a`, `device_b` | Swap placement positions of two devices |
| `move` | `device`, `x`, `y` | Move a device to absolute coordinates |
| `move_row` | `type`, `y` | Move all devices of a type (pmos/nmos) to a new row Y |
| `abut` | `device_a`, `device_b` | Place two devices at abutment spacing (0.070 μm) |
| `add_dummy` | `type`, `count`, `side` | Add dummy devices for edge effect compensation |

---

## Key Design Patterns

### 1. Worker-Object Pattern (Qt)

`LLMWorker` is a `QObject` moved to a `QThread`, communicating via signals/slots. This keeps the GUI responsive during LLM API calls that may take seconds to minutes.

### 2. Multi-Agent Pipeline with State Machine

The `MultiAgentOrchestrator` uses a `PipelineState` enum to track whether it is mid-conversation (waiting for user feedback on abstract requests). This enables multi-turn dialogues for strategy refinement.

### 3. Regex Fast-Path

The classifier uses regex patterns to handle trivial inputs without hitting the LLM, saving cost and latency.

### 4. Retry with Exponential Backoff

The `run_llm()` function retries transient API errors (429, 503) up to 3 times with exponentially increasing delays (2s, 4s, 8s base).

### 5. Prompt Separation

Each agent has its own focused prompt in `prompts.py`, preventing "prompt dilution" from trying to do everything in one monolithic prompt.

### 6. Layout Context Injection

The layout state (devices, nets, topology) is formatted as compact text and injected into relevant agent prompts, grounding LLM responses in actual layout data.

---

## Integration with the Larger System

**Primary Consumer:** `symbolic_editor/chat_panel.py`

The `ChatPanel` widget (in the symbolic editor GUI):

1. Creates an `LLMWorker` and moves it to a `QThread`
2. Connects signals: `request_inference` → `process_request`, `response_ready` → `_on_llm_response`, `command_ready` → `command_requested.emit`
3. Passes layout context via `set_layout_context(nodes, edges, terminal_nets)`
4. Parses `[CMD]{...}[/CMD]` blocks from AI responses and emits them as `command_requested` signals
5. Has a fallback `_infer_commands_from_text()` that extracts swap/move/add_dummy commands from natural language (even without `[CMD]` blocks)

**Sibling Module:** `ai_agent/ai_initial_placement/` — Contains separate model-specific placer modules for initial placement (different subsystem, not Qt-based).

---

## Configuration

All API keys are loaded from `.env` at the project root:

| Variable | Provider |
|----------|----------|
| `GEMINI_API_KEY` | Google Gemini 2.5 Flash |
| `OPENAI_API_KEY` | OpenAI GPT-4o-mini |
| `GROQ_API_KEY` | Groq Llama-3.3-70B |
| `DEEPSEEK_API_KEY` | DeepSeek deepseek-chat |
| *(none)* | Ollama (local, no key needed) |

Keys can be set via `.env` file at the project root or as environment variables.

---

## Error Handling

| Error Type | Handling Strategy |
|------------|-------------------|
| **429 RESOURCE_EXHAUSTED** | Retry with exponential backoff (up to 3 attempts) |
| **503 UNAVAILABLE** | Retry with exponential backoff (up to 3 attempts) |
| **Network Timeout (Ollama)** | 300-second timeout for local model inference |
| **Classification Failure** | Falls back to `"abstract"` intent |
| **JSON Parse Failure (CodeGen)** | LLM re-prompted to produce valid JSON |
| **Empty LLM Response** | Treated as error, triggers retry |

---

## Module Dependencies

```
PySide6         → Qt threading, signals/slots
google-genai    → Gemini API client
openai          → OpenAI + DeepSeek API clients
groq            → Groq API client
requests        → Ollama local server communication
dotenv          → .env file loading
pathlib         → File path resolution
re              → Regex pattern matching
```

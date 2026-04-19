# AI Agent Migration: Old Branch → Basic Branch

## Overview

This document describes the migration of the **`ai_agent`** module from the `Old` branch into the `Basic` branch of the AI-Based Analog Layout Automation project.

The goal was to bring the full **LangGraph multi-stage pipeline** (topology analysis → placement → DRC critic → routing preview → human review) from the `Old` branch into the `Basic` branch, while preserving all the `Basic`-branch features and GUI compatibility.

---

## What Was Done

### 1. Files Added from Old Branch

The following files were copied **as-is** from `Old/AI-Based-Analog-Layout-Automation/ai_agent/`:

#### `ai_agent/ai_chat_bot/` (new files)
| File | Purpose |
|------|---------|
| `analog_kb.py` | Analog layout knowledge base (ANALOG_LAYOUT_RULES constant). Domain rules for current mirrors, diff pairs, dummy placement, etch uniformity, etc. |
| `cmd_utils.py` | Command parsing helpers: `_extract_cmd_blocks()`, `_apply_cmds_to_nodes()`, overlap deduplication |
| `edges.py` | LangGraph conditional edge routing functions: `route_after_drc()`, `route_after_human()` |
| `finger_grouping.py` | Multi-finger transistor group detection, aggregation to logical devices, finger expansion back to physical layout, interdigitation support |
| `graph.py` | LangGraph `StateGraph` definition — wires together all pipeline nodes and edges, compiles the `app` with a `MemorySaver` checkpointer for human-in-the-loop |
| `nodes.py` | All 8 LangGraph node implementations: `node_topology_analyst`, `node_strategy_selector`, `node_placement_specialist`, `node_finger_expansion`, `node_drc_critic`, `node_routing_previewer`, `node_human_viewer`, `node_save_to_rag` |
| `routing_utils.py` | Pure-Python routing swap generation helpers (`generate_targeted_swaps`) |
| `run_llm.py` | Module-level `run_llm(chat_messages, full_prompt)` with cascading LLM backend fallback (currently configured for Gemini `gemma-4-31b-it` with retry logic) |
| `state.py` | `LayoutState` TypedDict — shared state schema for all LangGraph pipeline nodes |
| `tools.py` | Thin tool-wrappers: `tool_build_circuit_graph`, `tool_score_net_crossings`, `tool_run_drc`, `tool_validate_device_count`, `tool_find_nearest_free_x`, `tool_resolve_overlaps`, `tool_validate_inventory` |
| `__init__.py` | Package marker with import guidance comment |

#### `ai_agent/ai_chat_bot/agents/` (new files)
| File | Purpose |
|------|---------|
| `classifier_agent.py` | Intent classifier: routes user messages to `concrete`, `abstract`, `question`, or `chat` before the pipeline runs |
| `drc_critic.py` | DRC critic agent — overlap/gap checker (pure Python, no LLM needed), LLM-prompt generator, `compute_prescriptive_fixes()` with bisect-based slot tracking |
| `placement_specialist.py` | Placement specialist agent — full system prompt with CC / IG / MB / Simple mode algorithms, `build_placement_context()` helper |
| `routing_previewer.py` | Routing preview agent — net crossing scorer (`score_routing()`), LLM prompt builder (`format_routing_for_llm()`), net criticality classification |
| `strategy_selector.py` | Strategy selector agent — generates interdigitated/common-centroid/auto strategies, `parse_placement_mode()` |
| `topology_analyst.py` | Topology analyst agent — `analyze_json()` extracts shared-gate/drain/source groups from layout JSON, `TOPOLOGY_ANALYST_PROMPT` |

#### `ai_agent/ai_chat_bot/rag_examples_db/`
| File | Purpose |
|------|---------|
| `chroma.sqlite3` | ChromaDB SQLite database for RAG retrieval examples (from Old branch) |

#### `ai_agent/rag_examples_db/`
| File | Purpose |
|------|---------|
| `chroma.sqlite3` | Top-level ChromaDB database |
| `01e6f4d1-2939-4741-b17c-f8b32a991932/` | ChromaDB vector store segment (binary files: `data_level0.bin`, `header.bin`, `length.bin`, `link_lists.bin`) |

#### `ai_agent/ai_initial_placement/` (replaced from Old)
| File | What Changed |
|------|-------------|
| `gemini_placer.py` | Replaced with Old version — robust `sanitize_json()`, `_validate_placement()`, coordinate normalization, model fallback list |
| `ollama_placer.py` | Replaced with Old version (simpler single-model implementation) |
| `openai_placer.py` | Replaced with Old version (simplified GPT-4o-mini call) |

#### `ai_agent/` root level
| File | What Changed |
|------|-------------|
| `chat_history.json` | Added from Old branch (multi-turn conversation history seed) |

---

### 2. Files Preserved from Basic Branch

These files from the `Basic` branch were **kept intact** because they are required by the GUI (`symbolic_editor/`):

#### `ai_agent/ai_chat_bot/`
| File | Why Kept |
|------|---------|
| `llm_worker.py` | Required by `symbolic_editor/chat_panel.py` — provides `LLMWorker` with `command_ready` signal, `set_layout_context()`, `reset_pipeline()`, and 4-argument `process_request(full_prompt, chat_messages, selected_model, ollama_model)`. Uses `MultiAgentOrchestrator` from Basic's agent architecture. |

#### `ai_agent/ai_chat_bot/agents/`
| File | Why Kept |
|------|---------|
| `__init__.py` | Package marker |
| `classifier.py` | Used by Basic's `MultiAgentOrchestrator` pipeline |
| `orchestrator.py` | Core multi-agent dispatcher — used by Basic's `LLMWorker` |
| `prompts.py` | System prompts for Basic's multi-agent pipeline |

#### `ai_agent/ai_initial_placement/`
| File | Why Kept |
|------|---------|
| `deepseek_placer.py` | Used by `symbolic_editor/main.py` (line 3029) |
| `groq_placer.py` | Used by `symbolic_editor/main.py` (line 3014) |
| `finger_grouper.py` | Used internally by `placer_utils.py` |
| `placer_utils.py` | Shared placement utility used by multiple placers |

#### `ai_agent/matching/`
| File | Why Kept |
|------|---------|
| `__init__.py` | Package marker |
| `matching_engine.py` | Used by `symbolic_editor/main.py` (line 65: `from ai_agent.matching.matching_engine import MatchingEngine`) |
| `universal_pattern_generator.py` | Used internally by matching module |

---

### 3. Files Created / Updated

| File | Action | Reason |
|------|--------|--------|
| `ai_agent/__init__.py` | **Created** (updated content) | Deleted by robocopy; recreated with correct import guidance comment |
| `ai_agent/ai_chat_bot/agents/__init__.py` | **Restored** from git | Deleted by robocopy; restored via `git checkout` |

---

## Architecture After Migration

```
ai_agent/
├── __init__.py                          ← Package marker
├── chat_history.json                   ← Seed chat history (from Old)
├── rag_examples_db/                    ← Top-level ChromaDB RAG store (from Old)
│
├── ai_chat_bot/                        ← Chatbot + LangGraph pipeline
│   ├── __init__.py
│   ├── llm_worker.py                   ← KEPT (Basic): GUI-compatible LLMWorker
│   ├── run_llm.py                      ← NEW (Old): Cascading LLM backend for pipeline
│   ├── state.py                        ← NEW (Old): LangGraph LayoutState TypedDict
│   ├── graph.py                        ← NEW (Old): LangGraph app definition
│   ├── nodes.py                        ← NEW (Old): All 8 pipeline node functions
│   ├── edges.py                        ← NEW (Old): Conditional routing edges
│   ├── analog_kb.py                    ← NEW (Old): Analog layout knowledge base
│   ├── cmd_utils.py                    ← NEW (Old): CMD block parser + applier
│   ├── finger_grouping.py              ← NEW (Old): Multi-finger transistor grouping
│   ├── tools.py                        ← NEW (Old): Pipeline tool wrappers
│   ├── routing_utils.py                ← NEW (Old): Routing swap generation
│   ├── rag_examples_db/                ← NEW (Old): Local RAG ChromaDB
│   └── agents/
│       ├── __init__.py
│       ├── classifier.py               ← KEPT (Basic): Basic's intent classifier
│       ├── orchestrator.py             ← KEPT (Basic): Basic's MultiAgentOrchestrator
│       ├── prompts.py                  ← KEPT (Basic): Basic's agent prompts
│       ├── classifier_agent.py         ← NEW (Old): Old's intent classifier
│       ├── drc_critic.py               ← NEW (Old): DRC check + prescriptive fixes
│       ├── placement_specialist.py     ← NEW (Old): Full placement specialist prompt
│       ├── routing_previewer.py        ← NEW (Old): Routing analysis + scoring
│       ├── strategy_selector.py        ← NEW (Old): Strategy selection logic
│       └── topology_analyst.py         ← NEW (Old): Topology analysis from JSON
│
├── ai_initial_placement/               ← Initial placement generators
│   ├── gemini_placer.py                ← UPDATED (Old): Robust version w/ validation
│   ├── ollama_placer.py                ← UPDATED (Old): Old's Ollama placer
│   ├── openai_placer.py                ← UPDATED (Old): Old's OpenAI placer
│   ├── deepseek_placer.py              ← KEPT (Basic): Required by main.py
│   ├── groq_placer.py                  ← KEPT (Basic): Required by main.py
│   ├── finger_grouper.py               ← KEPT (Basic): Used by placer_utils
│   └── placer_utils.py                 ← KEPT (Basic): Shared placer utilities
│
└── matching/                           ← Device matching module (KEPT from Basic)
    ├── __init__.py
    ├── matching_engine.py              ← Required by main.py line 65
    └── universal_pattern_generator.py
```

---

## LangGraph Pipeline (from Old Branch)

The Old branch implements a **4-stage multi-agent LangGraph pipeline** for AI-assisted placement:

```
START
  │
  ▼
[node_topology_analyst]     ← Extracts constraints from layout JSON + terminal nets
  │
  ▼
[node_strategy_selector]    ← Presents strategies to user, INTERRUPTS for human input
  │
  ▼
[node_placement_specialist] ← Generates [CMD] blocks for device positioning
  │
  ▼
[node_routing_previewer]    ← Scores routing quality, proposes swap improvements
  │
  ▼
[node_drc_critic]           ← Checks overlaps/gaps, applies prescriptive + LLM fixes
  │
  ├─── (if violations) ──→ [node_drc_critic]  (retry up to MAX_DRC_RETRIES=2)
  │
  ▼
[node_human_viewer]         ← Sends placement to GUI, INTERRUPTS for approval
  │
  ├─── (if rejected) ──→ [node_placement_specialist]  (loop back with user edits)
  │
  ▼
[node_save_to_rag]          ← Saves high-quality runs to ChromaDB (RAG)
  │
  ▼
END
```

---

## Key Design Decisions

### `llm_worker.py` Strategy
**Basic's version is kept** because it is the interface between the LangGraph pipeline and the GUI. The critical differences:
- Basic's `LLMWorker` has a `command_ready` signal that `chat_panel.py` connects to
- Basic's `LLMWorker.process_request()` takes 4 arguments (`selected_model`, `ollama_model`)
- Basic's `LLMWorker` has `set_layout_context()` and `reset_pipeline()` methods

The Old branch's pipeline **`nodes.py`** uses **`run_llm.py`** (Old's module-level function with 2 args) which has its own Gemini cascading fallback independently of the GUI's model selection.

### `run_llm.py` vs GUI Model Selection
- **Pipeline nodes** (`nodes.py`, `graph.py`) call `run_llm(messages, prompt)` from `ai_chat_bot/run_llm.py` — uses Gemini via `GEMINI_API_KEY`
- **GUI chat** (`chat_panel.py`) calls `process_request(prompt, messages, selected_model, ollama_model)` in `llm_worker.py` — supports Gemini, OpenAI, Ollama, Groq, DeepSeek

### Dependency on `langgraph`
The new `graph.py` and `nodes.py` require `langgraph` to be installed:
```bash
pip install langgraph
```
This was previously installed for the Old branch's orchestrator feature.

---

## Required Dependencies

Ensure these are installed in your Python environment:

```bash
pip install langgraph langchain-core google-genai python-dotenv PySide6
```

For full multi-model support (optional):
```bash
pip install openai groq requests  # OpenAI, Groq, Ollama
```

---

## Environment Variables (.env)

The pipeline auto-discovers the `.env` file by walking up from the module's location looking for a directory with `README.md` and `ai_agent/`. Ensure `.env` contains:

```
GEMINI_API_KEY=your_key_here
# Optional:
OPENAI_API_KEY=...
GROQ_API_KEY=...
DEEPSEEK_API_KEY=...
```

---

## Migration Process Details

1. **Primary copy** — Used `robocopy /MIR` to mirror the entire Old `ai_agent/` directory into Basic's `ai_agent/`. This added all new files and updated changed files.

2. **Dependency analysis** — Scanned `symbolic_editor/main.py` and `symbolic_editor/chat_panel.py` for all `from ai_agent.*` imports.

3. **Selective restoration** — Used `git checkout` to restore files that were removed by `/MIR` but are required by the GUI:
   - `ai_agent/matching/` (3 files) — required by `main.py`
   - `ai_agent/ai_initial_placement/groq_placer.py`, `deepseek_placer.py`, `finger_grouper.py`, `placer_utils.py` — required by `main.py`
   - `ai_agent/ai_chat_bot/llm_worker.py` — required by `chat_panel.py` (incompatible signature with Old's version)
   - `ai_agent/ai_chat_bot/agents/classifier.py`, `orchestrator.py`, `prompts.py`, `__init__.py` — required by Basic's `LLMWorker`

4. **Init file recreation** — Created `ai_agent/__init__.py` (removed by robocopy since Old doesn't have one at root level) and `ai_agent/ai_chat_bot/agents/__init__.py`.

5. **Verification** — Confirmed all imports resolve correctly by tracing the import chains of both `nodes.py` and `llm_worker.py`.

---

*Migration performed on 2026-04-18 by AI Assistant*

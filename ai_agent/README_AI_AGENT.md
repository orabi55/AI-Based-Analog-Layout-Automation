# AI Agent System — Unified Architecture Documentation

## Overview

The AI Agent system powers **AI-driven analog IC layout placement** and **interactive chatbot-assisted layout editing**. It uses **LangGraph** (state machine orchestration) with **LLM agents** (Gemini, Qwen, Vertex AI) to generate professional-quality device placements.

### Key Features
- **AI Initial Placement**: Full automated pipeline generates placement from scratch (`Ctrl+P`)
- **AI Chatbot**: Interactive multi-turn conversation for layout refinements
- **Multi-Agent Pipeline**: 8 specialized agents (Topology Analyst → Strategy Selector → Placement Specialist → Finger Expansion → Routing Previewer → DRC Critic → Human Viewer → RAG Saver)
- **Skill System**: Structured layout expertise (differential pairs, common-centroid, bias chains, etc.)
- **Design Rule Checking**: Automatic DRC validation with AI-assisted fixes

---

## Architecture Evolution

### Before (Duplicate Infrastructure)
```
ai_agent/
├── ai_chat_bot/           # Chatbot graph (8 nodes)
│   ├── graph.py           # LangGraph definition
│   ├── nodes.py           # 993 lines monolithic
│   ├── llm_worker.py      # Qt workers
│   └── agents/            # Sub-agents
│
├── ai_initial_placement/  # Initial placement graph (6 nodes)
│   ├── placer_graph.py    # DUPLICATE graph definition
│   ├── placer_graph_worker.py  # DUPLICATE Qt worker
│   ├── placer_utils.py    # 1302 lines monolithic
│   └── finger_grouper.py  # 1881 lines
│
└── matching/              # Device patterns
```

**Problems:**
- Two separate graph definitions (`graph.py` + `placer_graph.py`)
- Two finger grouping implementations (`finger_grouping.py` + `finger_grouper.py`)
- Unclear boundary between initial placement and chatbot
- Maintenance burden: changes applied in two places

### After (Unified Graph)
```
ai_agent/
├── graph/                 # ← SINGLE LangGraph definition
│   ├── state.py           # LayoutState (with "mode" field)
│   ├── builder.py         # Unified graph builder
│   └── edges.py           # Conditional routing
│
├── nodes/                 # ← Split into per-node files
│   ├── topology_analyst.py
│   ├── strategy_selector.py
│   ├── placement_specialist.py
│   ├── finger_expansion.py
│   ├── drc_critic.py
│   ├── routing_previewer.py
│   ├── human_viewer.py
│   └── save_to_rag.py
│
├── agents/                # ← Specialized sub-agents
│   ├── topology_analyst.py
│   ├── strategy_selector.py
│   ├── placement_specialist.py
│   ├── drc_critic.py
│   ├── routing_previewer.py
│   ├── classifier.py      # Intent classification
│   ├── orchestrator.py    # Non-LangGraph fallback
│   └── prompts.py         # System prompt builders
│
├── placement/             # ← Pure-Python placement algorithms
│   ├── finger_grouper.py  # Enhanced version (1881 lines)
│   ├── centroid_generator.py
│   ├── symmetry.py
│   ├── validators.py
│   ├── normalizer.py
│   ├── json_utils.py
│   └── abutment.py
│
├── skills/                # ← Structured skill system
│   ├── loader.py          # Parses markdown → Skill objects
│   ├── differential_pair.md
│   ├── common_centroid.md
│   ├── bias_chain.md
│   └── ... (9 skills total)
│
├── llm/                   # ← Model infrastructure
│   ├── factory.py         # get_langchain_llm()
│   ├── runner.py          # run_llm() with retry
│   ├── workers.py         # LLMWorker, OrchestratorWorker
│   └── placement_worker.py # PlacementWorker (initial placement)
│
├── tools/                 # ← LangGraph-callable utilities
│   ├── circuit_graph.py
│   ├── scoring.py
│   ├── drc.py
│   ├── inventory.py
│   ├── positioning.py
│   ├── overlap_resolver.py
│   └── cmd_parser.py
│
├── knowledge/             # ← Domain expertise
│   ├── analog_rules.py    # ANALOG_LAYOUT_RULES
│   └── skill_injector.py  # Skill middleware
│
├── matching/              # ← Device pattern engine
│   ├── engine.py
│   └── patterns.py
│
└── utils/                 # ← Shared infrastructure
    ├── logging.py
    ├── routing.py
    └── config.py
```

---

## Execution Flows

### Flow 1: AI Initial Placement (`Ctrl+P`)

```
┌──────────────────────────────────────────────────────────────────┐
│  User: Ctrl+P or Design > Run AI Initial Placement               │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  layout_tab.py: do_ai_placement()                                │
│  - Opens AIModelSelectionDialog (model, abutment toggle)         │
│  - Serializes nodes/edges/nets → JSON                            │
│  - Spawns PlacementWorker (QThread)                              │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  llm/placement_worker.py: process_initial_placement_request()    │
│  - Deserializes JSON                                             │
│  - Builds initial_state with mode="initial"                      │
│  - Calls build_layout_graph(mode="initial")                      │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  LangGraph Pipeline (mode="initial", auto-run, no interrupts)    │
│                                                                  │
│  ┌─────────────────────┐                                        │
│  │ node_topology_      │ → agents/topology_analyst.analyze_json │
│  │ analyst             │   Identifies diff pairs, current       │
│  │                     │   mirrors, bias chains                  │
│  └──────────┬──────────┘                                        │
│             │                                                    │
│             ▼                                                    │
│  ┌─────────────────────┐                                        │
│  │ node_strategy_      │ → agents/strategy_selector             │
│  │ selector            │   Decides row assignment, symmetry     │
│  └──────────┬──────────┘                                        │
│             │                                                    │
│             ▼                                                    │
│  ┌─────────────────────┐                                        │
│  │ node_placement_     │ → LLM generates [CMD] blocks           │
│  │ specialist          │   Enforces inventory conservation      │
│  │                     │   Injects skills via middleware         │
│  └──────────┬──────────┘                                        │
│             │                                                    │
│             ▼                                                    │
│  ┌─────────────────────┐                                        │
│  │ node_finger_        │ → placement/finger_grouper.expand      │
│  │ expansion           │   Logical groups → physical fingers    │
│  └──────────┬──────────┘                                        │
│             │                                                    │
│             ▼                                                    │
│  ┌─────────────────────┐                                        │
│  │ node_routing_       │ → agents/routing_previewer.score       │
│  │ previewer           │   Evaluates net crossings, wire length │
│  └──────────┬──────────┘                                        │
│             │                                                    │
│             ▼                                                    │
│  ┌─────────────────────┐                                        │
│  │ node_drc_critic     │ → agents/drc_critic.run_drc_check      │
│  │                     │   Sweep-line overlap detection         │
│  │                     │   Loop back if violations (max 2)      │
│  └──────────┬──────────┘                                        │
│             │                                                    │
│             ▼                                                    │
│  ┌─────────────────────┐                                        │
│  │ node_human_viewer   │ → interrupt() → UI review              │
│  │                     │   User approves or requests edits      │
│  └──────────┬──────────┘                                        │
│             │                                                    │
│             ▼                                                    │
│  ┌─────────────────────┐                                        │
│  │ END                 │ → Emits visual_viewer_signal           │
│  └─────────────────────┘                                        │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  layout_tab.py: _on_ai_placement_completed()                     │
│  - Saves to *_initial_placement.json                             │
│  - Reloads canvas with color highlights                          │
└──────────────────────────────────────────────────────────────────┘
```

### Flow 2: AI Chatbot (Interactive)

```
┌──────────────────────────────────────────────────────────────────┐
│  User: Types message in chat panel                               │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  chat_panel.py: send_message()                                   │
│  - Detects orchestrator keywords (optimize, fix drc, etc.)      │
│  - Creates OrchestratorWorker (QThread)                          │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  llm/workers.py: OrchestratorWorker.process_orchestrated_request │
│  - Loads checkpoint (if available)                               │
│  - classify_intent(): chat/question/concrete/abstract            │
└──────────────────────┬───────────────────────────────────────────┘
                       │
            ┌──────────┴──────────┐
            │                     │
            ▼                     ▼
  ┌─────────────────┐   ┌─────────────────────────────────┐
  │ chat/question   │   │ concrete/abstract               │
  │                 │   │                                 │
  │ Conversational  │   │ → build_layout_graph(mode="chat")│
  │ reply (no graph)│   │ → Selective node execution      │
  └─────────────────┘   │ → Human-in-loop enabled        │
                        └──────────┬──────────────────────┘
                                   │
                                   ▼
                        ┌──────────────────────────────────┐
                        │ LangGraph (mode="chat")          │
                        │ - Same 8 nodes as initial        │
                        │ - Interrupts enabled for review  │
                        │ - Resumes from checkpoint        │
                        │ - Emits command_requested signal │
                        └──────────┬───────────────────────┘
                                   │
                                   ▼
                        ┌──────────────────────────────────┐
                        │ chat_panel.py: applies [CMD]     │
                        │ blocks to canvas in real-time    │
                        └──────────────────────────────────┘
```

---

## Graph Modes

The unified graph supports two execution modes via the `mode` field in `LayoutState`:

| Mode | Entry Point | Behavior | Interrupts | Terminal |
|------|-------------|----------|------------|----------|
| `"initial"` | `Ctrl+P` | Full auto-run | Only at human_viewer | After human_viewer |
| `"chat"` | Chat panel | Selective nodes | Strategy + human_viewer | After save_to_rag |

### State Schema

```python
class LayoutState(TypedDict):
    mode: Literal["initial", "chat"]          # NEW: execution mode
    
    # Inputs
    user_message: str
    chat_history: List[Dict[str, str]]
    nodes: List[Dict[str, Any]]
    selected_model: str
    
    # Topology
    constraint_text: str
    edges: List[Dict]
    terminal_nets: Dict
    
    # Strategy
    Analysis_result: str
    strategy_result: str
    
    # Placement
    placement_nodes: List[Dict]
    deterministic_snapshot: List[Dict]
    original_placement_cmds: List[Dict]
    
    # DRC
    drc_flags: List[Dict]
    drc_pass: bool
    drc_retry_count: int
    gap_px: float
    
    # Routing
    routing_pass_count: int
    routing_result: Dict
    
    # Control
    pending_cmds: List[Dict]
    approved: bool
    no_abutment: bool
    abutment_candidates: List[Dict]
```

---

## Skill System

Skills encode analog layout expertise as structured markdown files. The `SkillCatalog` parses them into callable objects.

### Skill Discovery

```python
from ai_agent.skills import SkillCatalog

# Load all skills as text (backward compat)
all_skills = SkillCatalog.load_all()

# Structured access
skill = SkillCatalog.load_by_id("differential_pair")
print(skill.algorithm)     # ABBA interdigitation rules
print(skill.constraints)   # Mirror symmetry, environment equivalence

# Trigger-based discovery
matched = SkillCatalog.load_by_trigger("diff pair")
# → [Skill(id="differential_pair", ...)]

# Mode-specific injection
prompt_section = SkillCatalog.inject_for_mode("initial")  # Full skills
prompt_section = SkillCatalog.inject_for_mode("chat")     # Catalog summary
```

### Available Skills

| Skill | ID | Triggers | Scope |
|-------|-----|----------|-------|
| Differential Pair Matching | `differential_pair` | diff pair, dp, v+ v- | Local |
| Common-Centroid Matching | `common_centroid` | centroid, cc, match | Global |
| Interdigitated Matching | `interdigitate` | interdigitate, alternate | Local |
| Diffusion Sharing | `diffusion_sharing` | abut, merge, compact | Local |
| Bias Chain Ordering | `bias_chain` | bias, cascode, tail | Global |
| Mirror Bias Symmetry | `bias_mirror` | mirror, bias, symmetry | Global |
| Multi-Row Placement | `multirow_placement` | multirow, partitioning | Global |
| Matched Environment | `matched_environment` | environment, mismatch | Global |
| Proximity-Net | `proximity_net` | proximity, net, routing | Local |

---

## Import Migration Guide

### Old → New Imports

```python
# ═══ OLD (deprecated) ═══
from ai_agent.ai_chat_bot.graph import app
from ai_agent.ai_chat_bot.state import LayoutState
from ai_agent.ai_chat_bot.nodes import node_placement_specialist
from ai_agent.ai_initial_placement.placer_graph import build_placer_graph
from ai_agent.ai_initial_placement.finger_grouper import group_fingers
from ai_agent.ai_initial_placement.placer_utils import _enforce_reflection_symmetry
from ai_agent.ai_chat_bot.cmd_utils import _extract_cmd_blocks
from ai_agent.ai_chat_bot.pipeline_log import vprint
from ai_agent.ai_chat_bot.llm_factory import get_langchain_llm
from ai_agent.ai_chat_bot.agents.drc_critic import run_drc_check
from ai_agent.matching.matching_engine import MatchingEngine

# ═══ NEW (recommended) ═══
from ai_agent.graph.builder import app, build_layout_graph
from ai_agent.graph.state import LayoutState
from ai_agent.nodes.placement_specialist import node_placement_specialist
from ai_agent.graph.builder import build_layout_graph  # mode="initial"
from ai_agent.placement.finger_grouper import aggregate_to_logical_devices
from ai_agent.placement.symmetry import enforce_reflection_symmetry
from ai_agent.tools.cmd_parser import extract_cmd_blocks
from ai_agent.utils.logging import vprint
from ai_agent.llm.factory import get_langchain_llm
from ai_agent.agents.drc_critic import run_drc_check
from ai_agent.matching.engine import MatchingEngine
```

### Backward Compatibility

Old imports still work via shims (`ai_chat_bot/__init__.py` and `ai_initial_placement/__init__.py`), but emit `DeprecationWarning`. Update your code before the next major release.

---

## Module Responsibilities

| Module | Files | Responsibility |
|--------|-------|----------------|
| `graph/` | 3 | LangGraph definitions (state, builder, edges) |
| `nodes/` | 9 | LangGraph node wrappers (1 per stage + shared utils) |
| `agents/` | 8 | Agent prompts + logic + intent classification |
| `placement/` | 7 | Placement algorithms (finger grouping, symmetry, abutment) |
| `skills/` | 10 | Skill loader + 9 markdown skill definitions |
| `llm/` | 4 | Model factory + runner + Qt workers |
| `tools/` | 7 | LangGraph-callable utilities (DRC, scoring, CMD parsing) |
| `knowledge/` | 2 | Domain rules + skill injection middleware |
| `matching/` | 2 | Device pattern detection & generation |
| `utils/` | 3 | Logging + routing + config |

---

## Testing

### Headless Testing (No Qt Required)

```bash
# Test graph/nodes without Qt
pytest tests/test_graph_nodes.py

# Test placement algorithms
pytest tests/test_finger_grouper.py

# Test skill loading
pytest tests/test_skills.py
```

### Integration Testing (Qt Required)

```bash
# Run full GUI tests
pytest tests/
```

---

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `GEMINI_API_KEY` | Google Gemini API key | — |
| `DASHSCOPE_API_KEY` | Alibaba Qwen API key | — |
| `PLACEMENT_STEPS_ONLY` | Suppress debug logs during placement | `0` |
| `PLACEMENT_DEBUG_FULL_LOG` | Force full debug logs | `0` |

---

## Design Decisions

### 1. Why Keep `graph/` as Root Name?

Because it's the **superset** — it already contains all LLM infrastructure, agent implementations, and graph definitions. `ai_initial_placement/` was just a consumer.

### 2. Why Create `placement/` Subpackage?

`placer_utils.py` (1302 lines) was doing too many things:
- JSON sanitization → `json_utils.py`
- Symmetry enforcement → `symmetry.py`
- Validation → `validators.py`
- Coordinate normalization → `normalizer.py`
- Abutment handling → `abutment.py`

### 3. Why Split `nodes.py`?

993 lines in one file made testing impossible. Now each node is independently testable:
```bash
pytest tests/test_node_placement_specialist.py
pytest tests/test_node_drc_critic.py
```

### 4. Zero Qt Dependencies in Core Logic

```
graph/     → No Qt imports (pure LangGraph)
agents/    → No Qt imports (pure agent logic)
placement/ → No Qt imports (pure Python algorithms)
skills/    → No imports at all (just markdown)

llm/workers.py → Qt imports HERE ONLY (QThread, Signal, Slot)
llm/placement_worker.py → Qt imports HERE ONLY
```

---

## Future Enhancements

1. **Checkpoint Persistence**: Save LangGraph checkpoints to disk for crash recovery
2. **Skill Versioning**: Allow multiple versions of the same skill
3. **A/B Testing**: Compare placements from different LLM providers
4. **RAG Integration**: Save high-quality placements as training examples
5. **Performance Profiling**: Track LLM latency per node for optimization

---

## Troubleshooting

### "ImportError: No module named 'ai_agent.graph'"

Ensure you're using the new import paths. Old paths still work via shims but emit warnings.

### "LangGraph state leak between runs"

Fixed: Each run creates a fresh graph via `build_layout_graph()` with its own `MemorySaver()`.

### "LLM hangs on large prompts"

The skill system now uses `inject_for_mode("chat")` for catalog summaries (~1KB) instead of full skill bodies (~42KB).

### "Device conservation failure"

The placement specialist validates that ALL original device IDs are present in the proposed placement. Check LLM output for missing devices.

---

## License

Internal project — proprietary AI-assisted layout automation tool.

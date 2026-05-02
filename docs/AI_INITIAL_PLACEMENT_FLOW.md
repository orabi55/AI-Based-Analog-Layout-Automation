# AI Initial Placement: Execution Flow & Architecture

This document provides a comprehensive, step-by-step trace of the **AI Initial Placement** feature in the AI-Based Analog Layout Automation project. It covers the process from the user's initial trigger in the GUI to the final placement output.

---

## 1. Execution Flow Overview

When a user triggers "Run AI Initial Placement", the system initiates a multi-stage pipeline powered by **LangGraph**. This pipeline uses specialized LLM agents and deterministic geometric engines to analyze the circuit topology, select a placement strategy, generate positioning commands, expand logical groups into physical fingers, and validate the result against Design Rule Checks (DRC).

### High-Level Stage Sequence
1.  **Trigger:** User initiates via GUI (Ctrl+P).
2.  **Topology Analysis:** Circuit netlist is analyzed for electrical constraints.
3.  **Strategy Selection:** A high-level layout strategy is chosen.
4.  **Placement Specialist:** Logical devices are assigned to a 2D grid.
5.  **Finger Expansion:** Logical groups are expanded into physical fingers with fillers.
6.  **Routing Preview:** The placement is scored for routing complexity.
7.  **DRC Critic:** Geometry is validated; mechanical/AI fixes are applied if needed.
8.  **Finalization:** Metrics are computed and the GUI is updated.

---

## 2. Step-by-Step Process Breakdown

### Step 1: User Trigger
- **Initiator:** User clicks `Design > Run AI Initial Placement` or presses `Ctrl+P`.
- **File:** `symbolic_editor/layout_tab.py`
- **Function:** `do_ai_placement()`
- **Process:** 
  - Opens `AIModelSelectionDialog` to gather user preferences (Model, Abutment).
  - Syncs current node positions and builds the input data payload.
  - Instantiates a `GenericWorker` to run the placement in a background thread.

### Step 2: Worker Initialization
- **File:** `symbolic_editor/layout_tab.py`
- **Function:** `_run_ai_initial_placement(data, model_choice, abutment_enabled)`
- **Process:**
  - Instantiates `ai_agent.llm.placement_worker.PlacementWorker`.
  - Calls `graph_worker.process_initial_placement_request(...)`.

### Step 3: LangGraph Setup
- **File:** `ai_agent/llm/placement_worker.py`
- **Method:** `process_initial_placement_request(...)`
- **Process:**
  - Initializes the `initial_state` (LayoutState) with nodes, edges, and model choices.
  - Calls `_stream_graph(initial_state)`.
- **File:** `ai_agent/graph/builder.py`
- **Function:** `build_layout_graph(mode="initial")`
  - Constructs the `StateGraph` with nodes: `topology_analyst`, `strategy_selector`, `placement_specialist`, `finger_expansion`, `routing_previewer`, `drc_critic`, and `human_viewer`.

---

### Step 4: Pipeline Execution (The Nodes)

#### Node 1: Topology Analyst
- **File:** `ai_agent/nodes/topology_analyst.py`
- **Function:** `node_topology_analyst(state)`
- **Logic:** 
  - Calls `ai_agent.placement.finger_grouper.aggregate_to_logical_devices`.
  - Calls `ai_agent.agents.topology_analyst.analyze_json` to extract shared-gate/drain/source groups.
  - Prompts LLM to identify circuit types (e.g., Differential Pair, Current Mirror).
- **Output:** `constraint_text`, `Analysis_result`.

#### Node 2: Strategy Selector
- **File:** `ai_agent/nodes/strategy_selector.py`
- **Function:** `node_strategy_selector(state)`
- **Logic:** 
  - Uses the topology analysis to prompt the LLM for a high-level placement strategy (e.g., "Symmetric common-centroid for the input pair").
- **Output:** `strategy_result`.

#### Node 3: Placement Specialist
- **File:** `ai_agent/nodes/placement_specialist.py`
- **Function:** `node_placement_specialist(state)`
- **Logic:**
  1.  **Context Building:** Calls `build_placement_context` which runs a deterministic pipeline (`_compute_matching_and_rows`) to pre-calculate row assignments and matching blocks (ABBA).
  2.  **LLM Call:** Prompts the Placement Specialist LLM with pre-computed constraints.
  3.  **Command Application:** Extracts `[CMD]` blocks (e.g., `move device X to x,y`) and applies them using `apply_cmds_to_nodes`.
  4.  **Expansion:** Calls `expand_to_fingers` to convert logical group positions to individual finger coordinates.
- **Output:** `placement_nodes` (physical fingers), `pending_cmds`.

#### Node 4: Finger Expansion
- **File:** `ai_agent/nodes/finger_expansion.py`
- **Function:** `node_finger_expansion(state)`
- **Logic:**
  - Runs `_resolve_row_overlaps` to inject dummy fillers and align devices to the standard grid (0.294um pitch).
  - Resolves any minor overlaps using a mechanical `overlap_resolver`.
- **Output:** Final `placement_nodes`, `deterministic_snapshot`.

#### Node 5: Routing Pre-Viewer
- **File:** `ai_agent/nodes/routing_previewer.py`
- **Function:** `node_routing_previewer(state)`
- **Logic:** Calculates routing cost based on estimated wire-length and net crossings using `score_routing`.
- **Output:** `routing_result`.

#### Node 6: DRC Critic (Loop)
- **File:** `ai_agent/nodes/drc_critic.py`
- **Function:** `node_drc_critic(state)`
- **Logic:**
  - Runs `run_drc_check` (checks spacing and overlaps).
  - If violations exist:
    - Prompts LLM for fixes.
    - Generates mechanical "prescriptive" fixes.
    - Merges and applies fixes.
    - Loops back (max 2 retries).
- **Output:** `drc_pass` status, `drc_flags`.

---

### Step 5: Finalization & GUI Update
- **File:** `ai_agent/llm/placement_worker.py`
- **Method:** `_finalize_pipeline()`
  - Computes final layout metrics (Area, Aspect Ratio, Utilization).
  - Emits `visual_viewer_signal` with the final payload.
- **File:** `symbolic_editor/layout_tab.py`
- **Method:** `_on_ai_placement_completed(data)`
  - Saves the resulting JSON to disk (e.g., `design_placed.json`).
  - Calls `_load_from_data_dict(data)` to refresh the GUI canvas with new device positions.

---

## 3. Data Flow Diagram

```text
[ GUI (layout_tab) ]
       |
       v (JSON Payload)
[ PlacementWorker ]
       |
       v (LangGraph State)
+-----------------------+      +-------------------------+
| node_topology_analyst | ---> | node_strategy_selector  |
+-----------------------+      +-------------------------+
                                          |
                                          v
+-----------------------+      +---------------------------+
| node_finger_expansion | <--- | node_placement_specialist |
+-----------------------+      +---------------------------+
       |
       v
+-----------------------+      +-------------------------+
| node_routing_preview  | ---> |     node_drc_critic     |
+-----------------------+      +-------------------------+
                                          |
                                          v (If failed, retry loop)
                               [ node_human_viewer ]
                                          |
                                          v (Final Data)
[ GUI (layout_tab) ] <--------------------+
```

---

## 4. File & Function Reference

| Stage | File Path | Function/Method |
| :--- | :--- | :--- |
| **Trigger** | `symbolic_editor/layout_tab.py` | `do_ai_placement` |
| **Worker** | `ai_agent/llm/placement_worker.py` | `process_initial_placement_request` |
| **Graph Builder** | `ai_agent/graph/builder.py` | `build_layout_graph` |
| **Topology** | `ai_agent/nodes/topology_analyst.py` | `node_topology_analyst` |
| **Strategy** | `ai_agent/nodes/strategy_selector.py` | `node_strategy_selector` |
| **Placement** | `ai_agent/nodes/placement_specialist.py` | `node_placement_specialist` |
| **Placement Agent**| `ai_agent/agents/placement_specialist.py` | `build_placement_context` |
| **Geometric Eng.** | `ai_agent/placement/finger_grouper.py` | `group_fingers`, `expand_to_fingers` |
| **Expansion** | `ai_agent/nodes/finger_expansion.py` | `node_finger_expansion` |
| **Routing** | `ai_agent/nodes/routing_previewer.py` | `node_routing_previewer` |
| **DRC** | `ai_agent/nodes/drc_critic.py` | `node_drc_critic` |
| **DRC Engine** | `ai_agent/agents/drc_critic.py` | `run_drc_check`, `compute_prescriptive_fixes` |

---

## 5. Error Handling & Exit Points

- **LLM Failure:** If an LLM call fails, the system logs the error and either retries (with `_invoke_with_retry`) or falls back to a safe state (e.g., original positions).
- **Device Conservation Failure:** If the `Placement Specialist` loses devices during expansion, Node 3 triggers a fallback to original positions to prevent layout corruption.
- **DRC Failure:** If DRC cannot be cleared after 3 attempts (1 initial + 2 retries), the system proceeds with the best-effort placement and marks the violations in the GUI.
- **User Cancellation:** The `GenericWorker` in the GUI can be terminated by the user via the "Cancel" button on the progress overlay.

# Initial Placement Quality Improvements

Migrate the battle-tested functions from `multi_agent_placer.py` into the LangGraph flow's agent files, and wire up missing stages. The goal is to give the new graph the same physical placement quality as the standalone pipeline.

## What's missing / wrong in the new flow

| Gap | Root cause | Impact |
|---|---|---|
| `node_finger_expansion` uses a fixed pitch (0.294µm) for all fingers | Uses `expand_logical_to_fingers(pitch=0.294)` — no abutment awareness | Abutted pairs are spaced incorrectly; layout is too wide |
| Topology Analyst doesn't extract abutment candidates | `analyze_json()` never calls `_build_abut_pairs` | Placement Specialist and Finger Expansion don't know which pairs must abut |
| Placement Specialist uses a CMD-based flow (LLM writes `x=` coordinates) | LLM hallucinates coordinates; slot math is error-prone | Overlaps and DRC failures after placement |
| No multi-row geometry engine | `_convert_multirow_to_geometry` / `_place_row` / `_device_width` never ported | LLM placement has no deterministic geometry fallback |
| No deterministic fallback | `_deterministic_fallback` never ported | When LLM fails, there's no safe "good enough" result |
| No SA post-optimizer | `sa_optimizer.py` exists but no graph node wires to it | No wire-length optimization after initial placement |
| `LayoutState` missing `abutment_candidates` field | Field not in state | Abutment data can't be passed between nodes |
| `node_drc_critic` runs on finger-level nodes but strategy/placement runs on logical | Works on different granularity | DRC check may miss or double-count violations |

## Proposed Changes

---

### 1. State — add `abutment_candidates`

#### [MODIFY] [state.py](file:///c:/Users/DELL%20G3/Desktop/GP/Automation/AI-Automation-New/ai_agent/ai_chat_bot/state.py)

Add `abutment_candidates: List[Dict]` to `LayoutState`. This lets the topology analyst extract abutment pairs and pass them downstream to finger expansion and DRC.

---

### 2. Topology Analyst — extract abutment pairs

#### [MODIFY] [topology_analyst.py](file:///c:/Users/DELL%20G3/Desktop/GP/Automation/AI-Automation-New/ai_agent/ai_chat_bot/agents/topology_analyst.py)

Add `build_abutment_candidates(nodes)` function that reads `node["abutment"]` flags and shared-source nets to produce a list of `{dev_a, dev_b}` pairs. This is a direct port of `_build_abut_pairs` from `multi_agent_placer.py`.

---

### 3. Nodes — pass abutment_candidates from topology node

#### [MODIFY] [nodes.py](file:///c:/Users/DELL G3/Desktop/GP/Automation/AI-Automation-New/ai_agent/ai_chat_bot/nodes.py) (node_topology_analyst return)

Return `abutment_candidates` in the topology analyst node output so it's stored in state.

---

### 4. New geometry engine module

#### [NEW] [geometry_engine.py](file:///c:/Users/DELL%20G3/Desktop/GP/Automation/AI-Automation-New/ai_agent/ai_chat_bot/agents/geometry_engine.py)

Port the three geometry functions from `multi_agent_placer.py` into a dedicated module:

- `device_width(node)` — computes physical width from `geometry.width` or `electrical.nf * STD_PITCH`
- `place_row(devices, row_y, node_map, abut_pairs)` — packs a single row L→R with correct abutment/standard spacing
- `convert_multirow_to_geometry(multirow_data, original_nodes, abutment_candidates)` — converts `{nmos_rows, pmos_rows}` LLM output to exact x/y micron coords, with dynamic row pitch from device height, auto row-split, centering, and orphan recovery

This replaces the fixed-pitch `expand_logical_to_fingers` for initial placement.

---

### 5. New deterministic fallback module

#### [NEW] [placement_fallback.py](file:///c:/Users/DELL%20G3/Desktop/GP/Automation/AI-Automation-New/ai_agent/ai_chat_bot/agents/placement_fallback.py)

Port `_deterministic_fallback` and `_validate_multirow` from `multi_agent_placer.py`:

- `deterministic_fallback(nodes, abutment_candidates)` — connectivity-aware multi-row fallback with interdigitation for matched pairs
- `validate_multirow(nodes, placed)` — structural checks: coverage, type consistency, row-level x-collisions

---

### 6. Strategy Selector — add multi-row prompt construction

#### [MODIFY] [strategy_selector.py](file:///c:/Users/DELL%20G3/Desktop/GP/Automation/AI-Automation-New/ai_agent/ai_chat_bot/agents/strategy_selector.py)

Port `_build_multirow_prompt` logic into a `build_multirow_floorplan_context(nodes, edges, constraint_text, abutment_candidates)` helper. This adds:
- Square aspect-ratio guidance (auto target row count)
- Functional row examples per topology type
- Full NMOS/PMOS separation rules
- Abutment requirements section

The prompt is injected as additional context into the existing strategy selector LLM call, so the LLM outputs a `{nmos_rows, pmos_rows}` JSON alongside its strategy text.

---

### 7. Finger Expansion — replace fixed-pitch with geometry engine

#### [MODIFY] [nodes.py](file:///c:/Users/DELL G3/Desktop/GP/Automation/AI-Automation-New/ai_agent/ai_chat_bot/nodes.py) (`node_finger_expansion`)

Replace the current `expand_logical_to_fingers(pitch=0.294)` call with:
1. Read `multirow_layout` from state (if strategy produced one)
2. If present → call `convert_multirow_to_geometry()` from geometry_engine
3. If absent → call `deterministic_fallback()` from placement_fallback
4. Run `_validate_multirow()` and log any errors
5. Still run the existing `validate_finger_integrity` as a final conservation check

---

### 8. Wire SA optimizer as optional node

#### [MODIFY] [placer_graph.py](file:///c:/Users/DELL%20G3/Desktop/GP/Automation/AI-Automation-New/ai_agent/ai_initial_placement/placer_graph.py)

Add `node_sa_optimizer` node between `node_finger_expansion` and `node_drc_critic`. The node wraps `_run_sa` from `multi_agent_placer.py` / `sa_optimizer.py` and is conditional (only runs if state has `run_sa=True`).

#### [MODIFY] [state.py](file:///c:/Users/DELL G3/Desktop/GP/Automation/AI-Automation-New/ai_agent/ai_chat_bot/state.py)

Add `run_sa: bool` and `multirow_layout: Dict` fields.

---

## Execution Order

1. `state.py` — add `abutment_candidates`, `multirow_layout`, `run_sa`
2. `geometry_engine.py` — new file, ports geometry functions
3. `placement_fallback.py` — new file, ports fallback + validation
4. `topology_analyst.py` — add `build_abutment_candidates()`
5. `strategy_selector.py` — add `build_multirow_floorplan_context()`
6. `nodes.py` — wire topology output → `abutment_candidates`, update finger expansion node, update strategy node to store `multirow_layout`
7. `placer_graph.py` — add SA node

## Verification Plan

### Automated
- Run the existing `placer_graph_worker.py` on a test JSON after changes
- Check terminal output for: `✓ Placed N device(s) in X NMOS row(s) + Y PMOS row(s)`
- Check `[DRC] ✓ Clean placement!` in the first attempt

### Manual
- Compare old vs new layout in the symbolic editor for a simple diff amp circuit
- Confirm PMOS rows are above NMOS rows
- Confirm matched pairs are interdigitated

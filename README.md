# AI-Based Analog Layout Automation

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-Proprietary-red.svg)](#)

A state-of-the-art symbolic analog layout editor and automation framework. This project leverages a **LangGraph-driven multi-agent pipeline** to transform circuit netlists into production-ready, DRC-clean analog layouts through LLM-assisted placement and deterministic geometric expansion.

---

## 1. System Architecture

Our platform decouples high-level strategic reasoning from precise, sub-micron physical coordinates. This architecture is split into three main layers:

* **User Interaction & Visualization (PySide6 GUI)**: Symbolic Canvas Editor, Hierarchy Tree Panel, and KLayout Live Previewer.
* **The AI Placement Brain (LangGraph)**: Cooperative agents orchestrating logical floorplans.
* **Deterministic Physical Engine (Python & OASIS)**: Multi-finger expansion, Viterbi DP diffusion compactor, tap infilling, and stream writers.

Below is the comprehensive system-level block diagram illustrating the unified data-flow across all layers, from SPICE netlist parsing to final binary OASIS/GDS and OpenAccess database injections:

![System Block Diagram](Thesis/figures/system_block_diagram.png)

---

## 2. Stage One: EDA Interface (Inputs)

The input stage acts as the **"eyes"** of the system, loading circuit schematics and physical PDK constraints to construct a unified design representation:

* **SPICE Netlist Parsing**: Instantiates circuit graphs by parsing `.sp` spice files. It extracts subcircuits, net names, and transistor models.
* **PDK Cell Bounding Box Queries**: Ingests template `.oas` layout cells using `gdstk` to calculate physical width/height boundaries in micrometers.
* **Unification Compiler**: Maps logical device terminals (Gate, Source, Drain, Bulk) to cell-level port pins.
* **Integrated Layout JSON**: Outputs a unified design JSON containing logical netlists and physical coordinates to initialize the Symbolic Canvas.

![SPICE Parser Pipeline](Thesis/figures/parser.png)

---

## 3. Stage Two: AI Initial Placement

The strategic placement is managed by a **Multi-Agent State Machine** built on LangGraph. This pipeline choreographs high-level floorplanning and self-heals collisions:

![AI Agent Choreography](Thesis/figures/AI_Placement.png)

### 🤖 Multi-Agent Choreography & Prompt Engineering

Below are the detailed models, prompt strategies, and features of each agent:

#### A. Topology Analyst Agent
* **Model Used**: `gemini-2.5-flash` / `qwen-plus` (Light task weight)
* **Prompt Strategy**: Identifies symmetric match groups (e.g. differential pairs, current mirrors, bias chains) via subgraph isomorphism.
* **Features**: Graph match pattern discovery; maps transistor terminal matches.
* **Limitations**: Relies on structured device structures; highly custom analog topologies require manual tagging.

#### B. Strategy Selector Agent
* **Model Used**: `gemini-2.5-pro` / `claude-3-5-sonnet-v2@20241022` (Heavy task weight)
* **Prompt Strategy**: Analyzes matched groups, unrolls multipliers, assigns device groups to rows, and schedules vertical/horizontal symmetry axes.
* **Features**: Partitioning heuristics; thermal gradient balancing.
* **Limitations**: Multi-row planning uses coarse geometric aspect ratios.

#### C. Placement Specialist Agent
* **Model Used**: `gemini-2.5-pro` / `claude-3-5-sonnet-v2@20241022` (Heavy task weight)
* **Prompt Strategy**: Formulates logical floorplan coordinate instructions using relative positioning commands (`MOVE`, `ALIGN`, `FLIP`).
* **Features**: **Device Inventory Conservation** (validates that no devices are duplicated or dropped); skill injection middleware.
* **Limitations**: Operates on symbolic slot indexes; lacks physical sub-micron spacing metrics.

#### D. Deterministic Finger Expansion Engine
* **Model Used**: Pure-Python (No LLM)
* **Algorithm**: Unrolls devices into fingers ($W_{\text{finger}} = W_{\text{device}}/n_f$). Resolves phase alternation and computes optimal dummy placement.
* **Viterbi DP Compactor**: Uses an **$O(N)$ Viterbi Dynamic Programming Solver** to find the mathematically optimal finger flip orientation to maximize diffusion sharing (abutment), reducing parasitics by up to $75\%$.

#### E. Routing Previewer
* **Model Used**: Pure-Python (No LLM)
* **Algorithm**: Calculates Half-Perimeter Wire Length (HPWL) and net crossing complexity to output a normalized routability score.

#### F. DRC Critic Agent
* **Model Used**: `gemini-2.5-flash` / `qwen-plus` (Light task weight)
* **Prompt Strategy**: Sweeps expanded coordinates for bounding-box overlaps or spacing violations. Injects correction updates back to the Placement Specialist for self-healing retries (capped at 2).
* **Features**: Sweep-line geometric collision detection; automated correction delta generation.
* **Limitations**: Simple bounding-box clearance validation; does not parse complex process-specific DRC deck files.

---

## 4. Chatbot & GUI Visualization

### 🖼️ GUI Welcome Screen & Panels
Upon launch, designers are greeted with the **GUI Welcome Screen**, allowing them to select target SPICE designs and select AI models:

![GUI Welcome Screen](Thesis/figures/GUI_Welcome_Screen.png)

Once loaded, the **Symbolic Canvas Editor** provides visual tabs, design hierarchies, and a real-time ratsnest view:

![GUI Panels and Canvas](Thesis/figures/GUI_Panals.png)

---

### 💬 Interactive Chatbot Co-Pilot Features & Limitations

The AI Chatbot Co-Pilot allows designers to refine layouts in real-time using natural language:

![Chatbot Co-Pilot Interface](Thesis/figures/ChatBot.png)

#### 🚀 Features
* **Intent Classification**: In [classifier.py](file:///d:/Senior%202/Layout_Project/Automation/new_main_last_dance/AI-Based-Analog-Layout-Automation/ai_agent/agents/classifier.py), splits requests into `"general_chat"` (context-free) and `"layout_query"` (context-rich).
* **Conversational Fast Path**: If `"general_chat"` is active (e.g. *"Explain what a cascode mirror is"*), it bypasses layout serialization and tool-binding, streaming replies in **under 1 second** and saving **>90%** of token costs.
* **Interactive Editing**: Natural language commands (e.g. *"Align MM1 and MM2 vertically"*) are parsed into strict XML commands and committed directly to the GUI undo/redo stack.

#### ⚠️ Limitations
* Conversational fast-path cannot query device details unless the user shifts topics back to a layout query.
* Free-form placement updates are limited by the precision of the LLM's XML command generator.

---

## 5. EDA Interface (Outputs)

The output engine translates symbolic designs into production-ready physical structures:

* **TCL Coordinate Injection**: Writes space-separated coordinates for OpenAccess-resident tools.
* **Headless OASIS Compiler**: In [oas_writer.py](file:///d:/Senior%202/Layout_Project/Automation/new_main_last_dance/AI-Based-Analog-Layout-Automation/export/oas_writer.py), compiles physical cells, abutment pitches, and infills tap cells from `tests/taps.oas` directly into binary `.oas` streams.

![TCL and OASIS Exporter Flow](Thesis/figures/tcl_oas.png)

---

## 6. Examples

### 🧩 A. XOR Gate (Digital cell placed in Analog Framework)
A standard XOR cell unrolled and optimized:

* **AI Generated Schematic Graph**:
  ![XOR Schematic](Thesis/figures/xor_sch_AI_generated.png)
* **Before AI Placement (Unoptimized)**:
  ![XOR Before](Thesis/figures/xor_before_AI_placement.png)
* **Symbolic Canvas Layout**:
  ![XOR Symbolic](Thesis/figures/xor_symbolic.png)
* **Transistor-Level Placement**:
  ![XOR Transistor-Level](Thesis/figures/xor_transistor_level.png)
* **Final OASIS Layout View**:
  ![XOR OASIS](Thesis/figures/xor_klayout.png)

---

### 🧩 B. High-Performance Current Mirror
Active matched rows aligned with alternating source/drain terminals:

* **Before AI Placement (Unoptimized)**:
  ![CM Before](Thesis/figures/current_mirror_before_AI_placement.png)
* **Symbolic Canvas Layout**:
  ![CM Symbolic](Thesis/figures/current_mirror_symbolic.png)
* **Transistor-Level Placement**:
  ![CM Transistor-Level](Thesis/figures/current_mirror_transistor_level.png)
* **Transistor-Level (Colored Nodes)**:
  ![CM Transistor Colored](Thesis/figures/current_mirror_transistor_level_colord.png)
* **KLayout Physical View**:
  ![CM KLayout](Thesis/figures/current_mirror_klayout.png)
* **Synopsys Custom Compiler Integration View**:
  ![CM Custom Compiler](Thesis/figures/current_mirror_from_custom_compiler.png)

---

### 🧩 C. Differential Pair (Comparator Block)
The AI placement specialist and symmetry enforcer interleave active devices and center them symmetrically:

* **AI Generated Schematic Graph**:
  ![Comparator Schematic](Thesis/figures/comparator_sch_AI_generated.png)
* **Before AI Placement (Unoptimized)**:
  ![Comparator Before](Thesis/figures/comparator_before_AI_placement.png)
* **Final Symmetric Compacted Layout**:
  ![Comparator Final](Thesis/figures/Comparator.png)

---

## 7. Conclusion and Future Work

### 🏁 Conclusion
The AI-Based Analog Layout Automation platform successfully bridges schematic topologies and physical layout geometries. By combining **LangGraph multi-agent strategic floorplanning** with a **deterministic physical engine**, the platform eliminates manual layout bottlenecks, enforces matching symmetries, and automatically self-heals design rule violations.

### 🔮 Future Work
1. **Detailed Dynamic Routing**: Expand ratsnest scoring to direct, router-level wire synthesis.
2. **Multi-layer DRC Integration**: Connect with industrial DRC decks (like Calibre) for verification.
3. **Advanced RAG Sharing**: Sync local Chroma vector stores to central cloud database systems.

---

## 8. Acknowledgements

This project was developed as an academic senior design project focused on the intersection of Machine Learning, Geometric Compaction, and Electronic Design Automation (EDA).

This work was successfully completed through a collaborative partnership between:
* **ASU (Ain Shams University)**
* **Cairo University**
* **Si-Vision Company**

---

© 2026 AI-Based Analog Layout Automation Team

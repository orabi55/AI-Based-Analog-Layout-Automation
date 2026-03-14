# User Guide — AI-Based Analog Layout Automation

> A step-by-step guide for installing, configuring, and using the Symbolic Layout Editor with AI-assisted placement.

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [System Requirements](#2-system-requirements)
3. [Installation](#3-installation)
4. [Running the Application](#4-running-the-application)
5. [User Interface Overview](#5-user-interface-overview)
6. [Working with Layouts](#6-working-with-layouts)
7. [Canvas Operations](#7-canvas-operations)
8. [AI Chat Assistant](#8-ai-chat-assistant)
9. [Examples & Tutorials](#9-examples--tutorials)
10. [Troubleshooting & FAQ](#10-troubleshooting--faq)
11. [Project Structure Reference](#11-project-structure-reference)

---

## 1. Introduction

**AI-Based Analog Layout Automation** is a desktop application that helps analog IC designers create and optimize transistor-level layouts. It combines:

- A **Symbolic Layout Editor** — a visual canvas where you arrange PMOS and NMOS devices in rows, swap positions, flip orientations, merge diffusion regions, and add dummy devices.
- An **AI Chat Assistant** — an integrated chatbot powered by large language models (Gemini, Groq, OpenAI, etc.) that can analyze your circuit, suggest placement improvements, and execute layout commands automatically.

### Who is this for?

- Analog IC layout engineers who want AI-assisted placement suggestions.
- Students learning about analog layout techniques (matching, symmetry, current mirrors, diff-pairs).
- Researchers exploring AI-driven EDA workflows.

---

## 2. System Requirements

| Requirement | Details |
|-------------|---------|
| **Python** | 3.10 or newer |
| **OS** | Windows 10/11 (primary), macOS, Linux |
| **RAM** | 4 GB minimum, 8 GB recommended |
| **Display** | 1280×720 minimum |
| **Internet** | Required for AI features (LLM API calls) |

---

## 3. Installation

### Step 1 — Clone the Repository

```bash
git clone https://github.com/orabi55/AI-Based-Analog-Layout-Automation.git
cd AI-Based-Analog-Layout-Automation
```

### Step 2 — Create a Virtual Environment

```bash
python -m venv .venv
```

Activate it:

| OS | Command |
|----|---------|
| **Windows (PowerShell)** | `.venv\Scripts\Activate.ps1` |
| **Windows (CMD)** | `.venv\Scripts\activate.bat` |
| **macOS / Linux** | `source .venv/bin/activate` |

### Step 3 — Install Dependencies

```bash
pip install -r requirements.txt
```

This installs PySide6 (GUI), google-genai (Gemini), openai, chromadb (RAG), networkx, gdstk, and all other dependencies.

### Step 4 — Configure API Keys

```bash
# Windows
copy .env.example .env

# macOS / Linux
cp .env.example .env
```

Open `.env` in any text editor and paste your API key(s):

```env
GEMINI_API_KEY=your-gemini-key-here
GROQ_API_KEY=your-groq-key-here
```

**Where to get free API keys:**

| Provider | URL | Cost |
|----------|-----|------|
| Google Gemini | [aistudio.google.com](https://aistudio.google.com) | Free |
| Groq | [console.groq.com](https://console.groq.com) | Free |
| OpenAI | [platform.openai.com](https://platform.openai.com) | Paid |
| DeepSeek | [platform.deepseek.com](https://platform.deepseek.com) | Paid |

> **Tip:** You only need **one** API key to use the AI features. Gemini is recommended (free and high quality).

---

## 4. Running the Application

### Basic Launch

```bash
python symbolic_editor/main.py
```

The editor opens with an empty canvas. Use **File → Open** to load a placement JSON file.

### Launch with a File

```bash
python symbolic_editor/main.py CM_initial_placement.json
```

Or load one of the included examples:

```bash
python symbolic_editor/main.py examples/xor/Xor_initial_placement.json
python symbolic_editor/main.py examples/std_cell/Std_Cell_initial_placement.json
```

---

## 5. User Interface Overview

The application window has three main panels:

```
┌──────────────────────────────────────────────────────────────┐
│                        Toolbar                               │
├──────────┬───────────────────────────────┬───────────────────┤
│          │                               │                   │
│  Device  │       Symbolic Canvas         │    AI Chat        │
│  Hierarchy │     (PMOS row — top)         │    Panel          │
│  (tree)  │     (NMOS row — bottom)       │                   │
│          │                               │                   │
├──────────┴───────────────────────────────┴───────────────────┤
│                       Status Bar                             │
└──────────────────────────────────────────────────────────────┘
```

### Device Hierarchy (Left Panel)
- Tree view of all devices grouped by type (PMOS, NMOS, Dummies).
- Click a device to select it on the canvas and highlight its net connections.
- Collapsible — click the header or use **Edit → Toggle Device Hierarchy**.

### Symbolic Canvas (Center)
- Visual representation of transistor placement.
- **Top row** = PMOS devices, **Bottom row** = NMOS devices.
- Devices are drawn as rectangles with Source (S), Gate (G), and Drain (D) labels.
- Net connections are shown as coloured dashed curves when a device is selected.
- Dark background (#0e1219) with high-contrast device colors.

### AI Chat Panel (Right)
- Text input area at the bottom — type your requests here.
- AI responses appear as chat bubbles with timestamps.
- The AI sees your current layout context automatically.
- Collapsible — click the header or use **Edit → Toggle AI Chat**.

### Toolbar
- **File operations**: New, Open, Save, Save As, Export.
- **Edit tools**: Undo, Redo, Swap, Flip H, Flip V, Merge S-S, Merge D-D, Delete.
- **Modes**: Move mode (M), Dummy mode (D).
- **View**: Zoom In, Zoom Out, Fit (F), Row/Col controls.

---

## 6. Working with Layouts

### Input Files

The tool works with three types of input files:

| File Type | Extension | Description |
|-----------|-----------|-------------|
| **Placement JSON** | `.json` | Device positions, connections, and properties |
| **SPICE Netlist** | `.sp` | Circuit connectivity for topology analysis |
| **Layout file** | `.oas` / `.gds` | Physical layout (optional, for import) |

### Loading a Layout

1. **File → Open** (or `Ctrl+O`) or pass the file path as a command-line argument.
2. The tool reads the JSON file and renders devices on the canvas.
3. If a matching `.sp` file exists in the same directory, the AI will discover it automatically for topology analysis.

### Saving Your Work

- **File → Save** (`Ctrl+S`) — overwrites the current file.
- **File → Save As** (`Ctrl+Shift+S`) — saves to a new file.
- **File → Export** (`Ctrl+E`) — generates a clean placement export.

### Understanding the JSON Format

A placement JSON file contains:

```json
{
  "nodes": [
    {
      "id": "MM0",
      "type": "nmos",
      "geometry": { "x": 0.0, "y": 0.0, "orientation": "R0" },
      "electrical": { "nf": 4, "l": "20n", "w": "100n" },
      "is_dummy": false
    }
  ],
  "edges": [
    { "source": "MM0", "target": "MM1", "net": "VDD" }
  ],
  "terminal_nets": {
    "MM0": { "D": "net1", "G": "NBIAS", "S": "GND" }
  }
}
```

---

## 7. Canvas Operations

### Selecting Devices
- **Click** a device to select it. Selected devices get a blue highlight.
- **Ctrl+Click** to add/remove from selection.
- **Ctrl+A** to select all devices.
- **Esc** to deselect.

### Moving Devices
1. Select a device.
2. Press **M** to enter Move mode (or click the Move tool in the toolbar).
3. Click the destination position on the canvas.
4. The device snaps to the nearest valid grid slot.

### Swapping Devices
1. Select exactly **two** devices.
2. Click the **Swap** button in the toolbar (or use the AI: `"swap MM0 and MM1"`).
3. The two devices exchange positions instantly.

### Flipping Devices
- **Flip Horizontal** — mirrors the device left-to-right (S↔D swap). Labels stay readable.
- **Flip Vertical** — mirrors the device top-to-bottom.

### Merging Diffusion
- **G** = Merge Source-Source between two adjacent selected devices.
- **Shift+G** = Merge Drain-Drain between two adjacent selected devices.
- Merging removes shared diffusion spacing, creating a more compact layout.

### Adding Dummy Devices
1. Press **D** to enter Dummy mode.
2. A ghost preview follows your cursor, showing where the dummy will be placed.
3. **Click** to place the dummy. It snaps to the nearest free slot.
4. Press **D** again or **Esc** to exit Dummy mode.
5. Dummies appear in pink and are grouped under "Dummy NMOS / Dummy PMOS" in the hierarchy.

### Deleting Devices
- Select device(s) and press **Delete**.

### View Controls
- **Mouse wheel** — Zoom in/out.
- **Middle-mouse drag** — Pan the canvas.
- **F** — Fit all devices in the view.

### Full Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `G` | Merge S-S (selected pair) |
| `Shift+G` | Merge D-D (selected pair) |
| `M` | Toggle move mode |
| `D` | Toggle dummy placement mode |
| `F` | Fit view |
| `Delete` | Delete selected devices |
| `Ctrl+A` | Select all |
| `Ctrl+Z` | Undo |
| `Ctrl+Y` | Redo |
| `Ctrl+S` | Save |
| `Ctrl+Shift+S` | Save As |
| `Ctrl+E` | Export |
| `Esc` | Cancel current mode / deselect |

---

## 8. AI Chat Assistant

### How It Works

The AI chat uses a **multi-agent pipeline** with 4 stages:

```
┌───────────────┐    ┌────────────────────┐    ┌──────────────┐    ┌─────────────────┐
│ Stage 1       │    │ Stage 2            │    │ Stage 3      │    │ Stage 4         │
│ Topology      │ →  │ Placement          │ →  │ DRC          │ →  │ Routing         │
│ Analyst       │    │ Specialist         │    │ Critic       │    │ Pre-Viewer      │
│               │    │                    │    │              │    │                 │
│ Extracts      │    │ Generates device   │    │ Checks for   │    │ Optimises net   │
│ constraints   │    │ placement commands │    │ overlaps &   │    │ crossings by    │
│ from netlist  │    │ based on topology  │    │ gap          │    │ suggesting      │
│               │    │ constraints        │    │ violations   │    │ device swaps    │
└───────────────┘    └────────────────────┘    └──────────────┘    └─────────────────┘
```

### Example Prompts

Here are some things you can ask the AI:

| What You Want | What to Type |
|--------------|--------------|
| Auto-place all devices | `"Optimize placement for this current mirror"` |
| Swap two devices | `"Swap MM0 and MM1"` |
| Get placement advice | `"How should I place these matched transistors?"` |
| Add dummy devices | `"Add 2 NMOS dummies on the left side"` |
| Analyze topology | `"Analyze this circuit and find all mirrors and diff-pairs"` |
| Fix DRC issues | `"Check for any overlapping devices"` |

### How Commands Work

When the AI wants to make a change, it outputs **command blocks** like:

```
[CMD]{"action":"swap","device_a":"MM0","device_b":"MM1"}[/CMD]
```

These are automatically parsed and applied to your layout. Supported actions:

| Action | Parameters | Description |
|--------|-----------|-------------|
| `swap` | `device_a`, `device_b` | Exchange positions of two devices |
| `move` | `device`, `x`, `y` | Move a device to coordinates (x, y) |
| `add_dummy` | `type`, `count`, `side` | Add dummy device(s) (left or right) |

### Tips for Best Results

1. **Be specific** — "Swap MM3 and MM5" works better than "fix the layout".
2. **Load a SPICE netlist** — Place the `.sp` file in the same directory as your JSON. The AI uses it for topology analysis.
3. **Confirm topology** — The AI will ask you to review its circuit analysis. Say "Yes" to proceed or correct any mistakes.
4. **Use the pipeline for complex tasks** — For "optimize placement", the AI runs all 4 stages automatically.

---

## 9. Examples & Tutorials

The `examples/` directory contains ready-to-use circuits:

### Current Mirror (`CM_initial_placement.json`)

A basic NMOS current mirror at the project root. Great for learning the basics.

```bash
python symbolic_editor/main.py CM_initial_placement.json
```

**Try asking the AI:** `"Analyze this circuit and optimize placement for matching"`

### XOR Gate (`examples/xor/`)

A complementary CMOS XOR gate with PMOS and NMOS rows.

```bash
python symbolic_editor/main.py examples/xor/Xor_initial_placement.json
```

**Files included:**
- `Xor_Automation.sp` — SPICE netlist
- `Xor_Automation.oas` — OASIS layout
- `Xor_initial_placement.json` — Initial placement

### Comparator (`examples/comparator/`)

An analog comparator circuit (more complex topology with diff-pairs and mirrors).

```bash
# Requires generating placement JSON first — load the .sp file
# and use the parser to create the initial placement.
```

**Files included:**
- `Comparator.sp` — SPICE netlist
- `Comparator.oas` — OASIS layout

### Standard Cell (`examples/std_cell/`)

A large standard cell with many devices — good for stress testing.

```bash
python symbolic_editor/main.py examples/std_cell/Std_Cell_initial_placement.json
```

---

## 10. Troubleshooting & FAQ

### "All AI models failed"

**Cause:** No valid API key configured.

**Fix:**
1. Check your `.env` file exists in the project root.
2. Make sure at least one API key is set (e.g., `GEMINI_API_KEY=your-key`).
3. Verify the key is valid by visiting the provider's dashboard.
4. Restart the application after changing `.env`.

> **Free option:** Get a Groq API key at [console.groq.com](https://console.groq.com) — no credit card required.

### "ModuleNotFoundError: No module named 'PySide6'"

**Fix:** Make sure you've activated your virtual environment and installed dependencies:

```bash
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

### Devices don't appear on the canvas

**Possible causes:**
- The JSON file may have an incorrect format. Check that `nodes` is a list of device objects.
- Press **F** to fit the view — devices might be outside the visible area.

### AI responses are very slow

**Possible causes:**
- Rate limiting on the API (especially Gemini free tier). Wait 30-60 seconds and try again.
- The `gemma-3-27b-it` model is being used. It is free but may have higher latency.

### The canvas is blank after loading

**Fix:** Press **F** (Fit View) to auto-zoom to the loaded devices.

### How do I generate a placement JSON from a new circuit?

1. Place your `.sp` (SPICE netlist) and `.oas` (OASIS layout) files in a directory.
2. Use the parser module to generate the initial placement:

```python
from parser.netlist_reader import read_netlist
from parser.layout_reader import read_layout
from parser.device_matcher import match_devices

# Read inputs
netlist = read_netlist("your_circuit.sp")
layout = read_layout("your_circuit.oas")

# Match and generate placement JSON
placement = match_devices(netlist, layout)
```

---

## 11. Project Structure Reference

```
AI-Based-Analog-Layout-Automation/
│
├── symbolic_editor/           # GUI application
│   ├── main.py                #   Main window, toolbar, menus
│   ├── editor_view.py         #   QGraphicsView canvas
│   ├── device_item.py         #   Device rectangle rendering
│   ├── device_tree.py         #   Device hierarchy tree panel
│   ├── chat_panel.py          #   AI chat panel
│   ├── klayout_panel.py       #   KLayout integration
│   └── icons.py               #   Procedural vector icons
│
├── ai_agent/                  # Multi-agent AI pipeline
│   ├── orchestrator.py        #   4-stage pipeline controller
│   ├── llm_worker.py          #   LLM API calls (Qt thread)
│   ├── topology_analyst.py    #   Stage 1: constraint extraction
│   ├── placement_specialist.py#   Stage 2: placement generation
│   ├── drc_critic.py          #   Stage 3: DRC validation
│   ├── routing_previewer.py   #   Stage 4: routing optimization
│   ├── pipeline_optimizer.py  #   Deterministic placement optimizer
│   ├── classifier_agent.py    #   Intent classification
│   ├── strategy_selector.py   #   Strategy selection
│   ├── analog_kb.py           #   Analog layout knowledge base
│   ├── finger_grouping.py     #   Multi-finger device grouping
│   ├── rag_store.py           #   RAG vector store
│   ├── rag_indexer.py         #   RAG example indexer
│   ├── rag_retriever.py       #   RAG example retriever
│   ├── gemini_placer.py       #   Gemini-specific placement
│   ├── ollama_placer.py       #   Ollama-specific placement
│   ├── openai_placer.py       #   OpenAI-specific placement
│   └── tools.py               #   Shared utility functions
│
├── parser/                    # Input file readers
│   ├── netlist_reader.py      #   SPICE netlist parser
│   ├── layout_reader.py       #   OASIS/GDS layout parser
│   ├── circuit_graph.py       #   Circuit graph construction
│   ├── device_matcher.py      #   Layout ↔ schematic matching
│   ├── hierarchy.py           #   Hierarchical netlist support
│   ├── merged_graph.py        #   Graph merging utilities
│   ├── device.py              #   Device data model
│   ├── netlist.py             #   Netlist data model
│   └── units.py               #   Unit conversions
│
├── export/                    # Output generators
│   ├── export_json.py         #   JSON placement export
│   ├── oas_writer.py          #   OASIS file writer
│   └── klayout_renderer.py    #   KLayout rendering
│
├── examples/                  # Example circuits
│   ├── comparator/            #   Analog comparator
│   ├── xor/                   #   XOR gate
│   └── std_cell/              #   Standard cell
│
├── tests/                     # Test suite
├── netlists/                  # Additional SPICE files
├── scripts/                   # Utility scripts
├── logs/                      # Runtime logs
├── images/                    # Documentation screenshots
├── docs/                      # Documentation
│   └── USER_GUIDE.md          #   This file
│
├── .env.example               # API key template
├── requirements.txt           # Python dependencies
└── README.md                  # Project overview
```

---

*Last updated: March 2026*

# User Guide — AI-Based Analog Layout Automation

> A step-by-step guide for installing, configuring, and using the Symbolic Layout Editor with AI-assisted placement.

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [System Requirements](#2-system-requirements)
3. [Installation](#3-installation)
4. [Running the Application](#4-running-the-application)
5. [User Interface Overview](#5-user-interface-overview)
6. [End-to-End Workflow (Import > Place > Edit)](#6-end-to-end-workflow-import--place--edit)
7. [Working with Layouts](#7-working-with-layouts)
8. [Canvas Operations](#8-canvas-operations)
9. [AI Chat Assistant](#9-ai-chat-assistant)
10. [Examples & Tutorials](#10-examples--tutorials)
11. [Troubleshooting & FAQ](#11-troubleshooting--faq)
12. [Project Structure Reference](#12-project-structure-reference)

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

The editor opens with an empty canvas. From here you can:
- **File > Load** (`Ctrl+O`) — open an existing placement JSON.
- **File > Import from Netlist + Layout** (`Ctrl+I`) — import a new circuit from design files (see [Section 6](#6-end-to-end-workflow-import-place-edit)).

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
- **File operations**: Load, Import, Save, Save As, Export JSON, Export OAS.
- **Design**: Run AI Initial Placement.
- **Edit tools**: Undo, Redo, Swap, Flip H, Flip V, Merge S-S, Merge D-D, Delete.
- **Modes**: Move mode (M), Dummy mode (D).
- **View**: Zoom In, Zoom Out, Fit (F), Row/Col controls.

### Menu Bar

| Menu | Item | Shortcut | Description |
|------|------|----------|-------------|
| **File** | Load | `Ctrl+O` | Open an existing placement JSON |
| | Import from Netlist + Layout | `Ctrl+I` | Parse .sp + .oas and show the circuit graph |
| | Save | `Ctrl+S` | Save current layout to file |
| | Save As | `Ctrl+Shift+S` | Save to a new file |
| | Export JSON | `Ctrl+E` | Export placement as JSON |
| | Export to OAS | `Ctrl+Shift+E` | Export placement back to OAS layout |
| | View in KLayout | — | Open current layout in KLayout |
| **Design** | Run AI Initial Placement | `Ctrl+P` | Send current graph to Gemini for AI placement |
| **View** | (placeholders) | — | Future view options |

---

## 6. End-to-End Workflow (Import → Place → Edit)

This is the **core workflow** of the tool — going from raw design files to a fully placed and refined symbolic layout, **entirely within the GUI**.

### Overview

The pipeline has **3 stages**, all accessible from the GUI:

```
  ┌─────────────────────────────────────────────────────────────────────────┐
  │                           GUI (main.py)                                │
  │                                                                        │
  │   STEP 1                    STEP 2                    STEP 3           │
  │   File > Import             Design > Run AI           Edit & Refine    │
  │   (Ctrl+I)                  Placement (Ctrl+P)        (manual + chat)  │
  │                                                                        │
  │  ┌──────────────┐       ┌──────────────────┐       ┌────────────────┐  │
  │  │ Parse .sp    │       │ Send graph to    │       │ Swap, flip,    │  │
  │  │ Parse .oas   │  -->  │ Gemini LLM       │  -->  │ merge, add     │  │
  │  │ Match devices│       │ Get optimized    │       │ dummies, chat  │  │
  │  │ Build graph  │       │ x/y positions    │       │ with AI        │  │
  │  └──────────────┘       └──────────────────┘       └────────────────┘  │
  │                                                                        │
  │  Output:                 Output:                    Output:            │
  │  *_graph.json            *_initial_placement.json   Save / Export OAS  │
  └─────────────────────────────────────────────────────────────────────────┘
```

---

### Step 1 — Import from Netlist + Layout (`Ctrl+I`)

This is the entry point for any new circuit.

#### What you need

| File | Required? | Description | Example |
|------|-----------|-------------|--------|
| **SPICE netlist** (`.sp`) | **Yes** | Transistor connectivity (D, G, S nets) | `Current_Mirror_CM.sp` |
| **Layout file** (`.oas` / `.gds`) | Optional | Physical geometry from PDK | `Current_Mirror_CM.oas` |

> **Tip:** If you don't have a `.oas` file, the tool will use default grid positions. You can always rearrange later.

#### How to do it

1. Open the GUI: `python symbolic_editor/main.py`
2. Go to **File > Import from Netlist + Layout** (or press `Ctrl+I`)
3. The **Import Dialog** opens:

```
  ┌─────────────────────────────────────────────────┐
  │  Import Circuit from Design Files               │
  │                                                  │
  │  Select a SPICE netlist and (optionally) a       │
  │  layout file to generate the placement.          │
  │                                                  │
  │  ┌── Design Files ──────────────────────────┐    │
  │  │ SPICE Netlist:  [path/to/circuit.sp] [Browse] │
  │  │ Layout File:    [path/to/circuit.oas][Browse] │
  │  └──────────────────────────────────────────┘    │
  │                                                  │
  │                         [Cancel]  [Import]       │
  └─────────────────────────────────────────────────┘
```

4. Click **Browse** to select your `.sp` file (required).
5. Click **Browse** to select your `.oas` / `.gds` file (optional).
6. Click **Import**.

#### What happens behind the scenes

| Step | Module | What It Does |
|------|--------|--------------|
| 1 | `parser/netlist_reader.py` | Reads the `.sp` file, flattens hierarchy, expands multi-finger devices (nf>1 into individual fingers) |
| 2 | `parser/layout_reader.py` | Reads the `.oas`/`.gds` file, extracts transistor instances with positions, widths, heights |
| 3 | `parser/device_matcher.py` | Matches each netlist device to its layout instance (NMOS with NMOS, PMOS with PMOS, by position order) |
| 4 | `parser/circuit_graph.py` | Builds a connectivity graph — nodes are devices, edges are shared nets |

#### Result

- The **circuit graph** is displayed on the canvas with original layout positions.
- PMOS devices appear on the **top row**, NMOS on the **bottom row**.
- A `*_graph.json` file is saved next to the `.sp` file.
- The AI Chat panel shows a summary of imported devices.

> **Note:** At this point, the positions come directly from the layout file.  
> To get AI-optimized placement, proceed to **Step 2**.

---

### Step 2 — Run AI Initial Placement (`Ctrl+P`)

Once you have a circuit graph loaded (from Step 1 or from an existing JSON), you can ask the AI to generate an optimized placement.

#### How to do it

1. Make sure a circuit is loaded (either imported or opened from JSON).
2. Go to **Design > Run AI Initial Placement** (or press `Ctrl+P`).
3. A progress dialog appears while the AI processes the layout.
4. When complete, the canvas updates with AI-optimized positions.

#### What happens behind the scenes

| Step | What It Does |
|------|--------------|
| 1 | Sends the current graph JSON (nodes + edges) to the **Gemini LLM** |
| 2 | The LLM analyzes net adjacency, device types, and analog design rules |
| 3 | It generates optimized x/y coordinates for every device |
| 4 | The result is validated (no overlaps, correct row assignments) |
| 5 | The canvas is updated and a `*_initial_placement.json` is saved |

#### Requirements

- A valid **`GEMINI_API_KEY`** must be set in your `.env` file.
- Internet connection for the API call.

> **Tip:** If AI placement fails (e.g., no API key), an error message will appear and your original layout positions are preserved.

---

### Step 3 — Edit & Refine

After import and optional AI placement, you can refine the layout:

| Action | How |
|--------|-----|
| **Swap two devices** | Select both > click Swap (or ask AI: `"Swap MM0 and MM1"`) |
| **Flip a device** | Select > Flip H or Flip V button |
| **Merge diffusion** | Select two adjacent > press `G` (S-S) or `Shift+G` (D-D) |
| **Add dummy devices** | Press `D`, hover over row, click to place |
| **Move a device** | Drag to new position (snaps to grid) |
| **Ask AI for help** | Type in chat: `"Optimize for matched current mirrors"` |
| **Save** | `Ctrl+S` to save, `Ctrl+Shift+S` for Save As |
| **Export to OAS** | `Ctrl+Shift+E` to export back to OASIS layout |

---

### Complete Example: Current Mirror

Here's the full workflow using the included Current Mirror example:

```
1. Launch the GUI:
   > python symbolic_editor/main.py

2. Import the circuit:
   > File > Import from Netlist + Layout (Ctrl+I)
   > SPICE Netlist:  examples/comparator/Comparator.sp
   > Layout File:    examples/comparator/Comparator.oas
   > Click [Import]

3. Inspect the graph:
   > The canvas shows all devices with original layout positions.
   > The Device Hierarchy (left panel) lists all PMOS and NMOS devices.
   > Click any device to see its net connections.

4. Run AI placement:
   > Design > Run AI Initial Placement (Ctrl+P)
   > Wait for the AI to process...
   > The canvas updates with optimized positions.

5. Refine manually:
   > Select MM0 and MM1 > click Swap to exchange positions.
   > Press D to add dummy devices for symmetry.
   > Type in chat: "Check for DRC violations"

6. Save your work:
   > File > Save (Ctrl+S)
   > File > Export to OAS (Ctrl+Shift+E) to create the final layout.
```

### Complete Example: XOR Gate

Using the included XOR example:

```
1. Launch:  python symbolic_editor/main.py
2. Import:  Ctrl+I > Select examples/xor/Xor_Automation.sp
                    > Select examples/xor/Xor_Automation.oas
3. View:    The 4 PMOS + 4 NMOS devices appear on the canvas.
4. Place:   Ctrl+P to run AI placement.
5. Chat:    "Analyze this XOR gate and suggest placement improvements"
6. Export:  Ctrl+Shift+E to export as OAS.
```

---

### Advanced: Using Scripts (Optional)

If you prefer command-line scripting over the GUI, you can run the pipeline manually:

```python
# Stage 1: Parse netlist + layout into graph JSON
from parser.netlist_reader import read_netlist
from parser.layout_reader import extract_layout_instances
from parser.device_matcher import match_devices
from parser.circuit_graph import build_circuit_graph
import json

netlist   = read_netlist("circuit.sp")
instances = extract_layout_instances("circuit.oas")
mapping   = match_devices(netlist, instances)
# ... build nodes and edges ...

# Stage 2: AI placement
from ai_agent.gemini_placer import gemini_generate_placement
gemini_generate_placement("graph.json", "placement.json")

# Stage 3: Open in GUI
# > python symbolic_editor/main.py placement.json
```

See `generate_cm.py` in the project root for a complete working example.

---

## 7. Working with Layouts

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

## 8. Canvas Operations

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
| `Ctrl+I` | Import from Netlist + Layout |
| `Ctrl+P` | Run AI Initial Placement |
| `Ctrl+O` | Load placement JSON |
| `Ctrl+S` | Save |
| `Ctrl+Shift+S` | Save As |
| `Ctrl+E` | Export JSON |
| `Ctrl+Shift+E` | Export to OAS |
| `G` | Merge S-S (selected pair) |
| `Shift+G` | Merge D-D (selected pair) |
| `M` | Toggle move mode |
| `D` | Toggle dummy placement mode |
| `F` | Fit view |
| `Delete` | Delete selected devices |
| `Ctrl+A` | Select all |
| `Ctrl+Z` | Undo |
| `Ctrl+Y` | Redo |
| `Esc` | Cancel current mode / deselect |

---

## 9. AI Chat Assistant

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

## 10. Examples & Tutorials

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

## 11. Troubleshooting & FAQ

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

Use the GUI: **File > Import from Netlist + Layout** (`Ctrl+I`). See [Section 6](#6-end-to-end-workflow-import--place--edit) for the full step-by-step workflow.

### Import succeeded but devices have wrong positions

**Cause:** The device matcher could not align netlist devices to layout instances (e.g., NMOS/PMOS count mismatch between `.sp` and `.oas`).

**Fix:** Check the terminal output for `[Import] Device matching failed` messages. Ensure your netlist and layout have the same number of NMOS and PMOS devices.

### AI Initial Placement does nothing / fails

**Cause:** Missing or invalid `GEMINI_API_KEY`.

**Fix:**
1. Open `.env` and set `GEMINI_API_KEY=your-key-here`.
2. Get a free key at [aistudio.google.com](https://aistudio.google.com).
3. Restart the application.

---

## 12. Project Structure Reference

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

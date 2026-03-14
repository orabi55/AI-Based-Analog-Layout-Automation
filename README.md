# AI-Based Analog Layout Automation

Symbolic analog layout editor with AI-assisted placement operations for PMOS/NMOS device-level floorplanning.

![Canvas view — PMOS and NMOS rows with dummy devices](images/editor_canvas.png)

![Full editor — Device Hierarchy, Canvas, and AI Chat panels](images/editor_full.png)

---

## Features

### Interactive Symbolic Canvas
- **Move, swap, delete, flip (H/V), merge (S-S / D-D), select-all** — full keyboard-driven editing.
- **Undo / Redo** with unlimited history.
- **Fit view** (`F`), zoom in/out/reset with mouse wheel or toolbar buttons.
- **Move mode** (`M`) — pick up a selected device and reposition it.
- **Middle-mouse pan** for scrolling the canvas.
- Row-based **abutted placement**: PMOS and NMOS devices pack edge-to-edge, sharing Source/Drain diffusion.
- **Horizontal flip** keeps text labels (S, G, D, device name) always readable — only geometry is mirrored.

### Dummy Device Placement
- Toggle dummy mode from the toolbar (`D` key).
- **Live ghost preview** follows the cursor at 55 % opacity showing exactly where the dummy will land.
- Click to place; the dummy snaps to the nearest free grid slot in the closest PMOS or NMOS row.
- Dummy devices are rendered with dedicated pink styling to distinguish them from active transistors.
- Grouped under collapsible **"Dummy NMOS / Dummy PMOS"** headers in the Device Hierarchy tree.

### Row / Column Controls
- **Row** and **Col** spin-boxes in the toolbar set the virtual grid extent.
- Increasing rows/columns creates visible empty track slots for planning future placement.

### PMOS–NMOS Row Gap Control (Edit Menu)
- **"Close PMOS–NMOS gap"** checkbox with an adjustable **Gap (px)** spin-box.
- Overrides the automatic row spacing so you can bring the two rows closer or farther apart.

### Collapsible Side Panels
- **Device Hierarchy** (left) and **AI Chat** (right) panels can be collapsed via header toggle buttons.
- Thin **reopen strips** appear at the edges when a panel is hidden, allowing one-click restoration.
- Panel toggles are also available in the **Edit** menu.

### Keyboard Shortcuts
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

### Connection Highlighting
- Graph edges and SPICE terminal-net data (when a matching `.sp` file is found) drive interactive connection curves.
- Click a device in the hierarchy or canvas to see its net connections drawn as coloured dashed bézier curves.

### AI Chat Panel
- Built-in chat with **LLM cascade** (Gemini → Groq → OpenAI → DeepSeek → Ollama).
- AI can execute layout commands: swap, move, flip, merge, add dummy, delete, and more.
- Chat messages appear with timestamps; layout context is sent automatically.

### Dark Theme & Vector Icons
- Global dark palette (#0e1219 canvas, #111621 panels, #1a1f2b toolbar).
- 15 procedural QPainter vector icons — no external image files required.

### File I/O
- **Load** placement JSON (`nodes`, optional `edges`).
- **Save / Save As** — writes updated positions back to JSON.
- **Export** — generates a clean placement export.

---

## API Key Configuration
Use a project-level `.env` file for web LLM providers:

```env
OPENAI_API_KEY=
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
DEEPSEEK_API_KEY=
```

`.env` is ignored by git via `.gitignore`.

---

## Run

```powershell
py -3 symbolic_editor/main.py
```

Or with a specific placement file:

```powershell
py -3 symbolic_editor/main.py CM_initial_placement.json
```

---

## Project Structure

```
├── ai_agent/          # LLM worker, Gemini/OpenAI/Ollama placers
├── export/            # JSON export utilities
├── parser/            # Netlist & layout readers, circuit graph
├── symbolic_editor/   # PySide6 GUI application
│   ├── main.py        # Main window, toolbar, menus, commands
│   ├── editor_view.py # QGraphicsView canvas
│   ├── device_item.py # QGraphicsRectItem for each transistor
│   ├── device_tree.py # Device Hierarchy side panel
│   ├── chat_panel.py  # AI Chat side panel
│   └── icons.py       # 15 procedural vector icons
├── CM_initial_placement.json
├── Current_Mirror_CM.sp
└── README.md
```

---

## Repository Hygiene
`.gitignore` covers:
- Python cache / build artifacts
- Virtual environments
- Editor / OS junk files
- Local secrets (`.env`, `.env.*`)

---

# Hussain

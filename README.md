# AI-Based-Analog-Layout-Automation

Symbolic analog layout editor with AI-assisted placement operations for PMOS/NMOS device-level floorplanning.

## Current Features
- Load transistor graph/placement JSON (`nodes`, optional `edges`).
- Interactive symbolic canvas:
  - Move, swap, delete, flip, merge (`SS`/`DD`), select-all.
  - Undo/redo support.
  - Fit view (`F`), zoom controls.
- PMOS/NMOS row-based abutment behavior with row spacing.
- Dummy placement mode (`D`) with live preview and click-to-place.
- Row/Col controls and live `Sel:` selection count in the top toolbar.
- Connection highlighting from graph edges and terminal-net mapping (when `.sp` data is available).
- Save / Save As / Export for updated placement JSON.
- Built-in AI chat panel in the editor UI.

## Dummy Devices
- Supports adding dummy PMOS/NMOS devices from the toolbar (`D`).
- Dummy placement snaps to valid rows and grid-aligned columns.
- Dummy preview appears under the cursor before placement.
- Dummy devices are rendered with dedicated styling to distinguish them from active PMOS/NMOS devices.

## API Key Configuration
Use a project-level `.env` file for web LLM providers:

```env
OPENAI_API_KEY=
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
DEEPSEEK_API_KEY=
```

Notes:
- Hardcoded Gemini/OpenAI/DeepSeek keys were removed in code and replaced with environment-variable usage.
- `.env` is ignored by git via `.gitignore`.

## Run
From project root:

```powershell
py -3 symbolic_editor/main.py
```

## Repository Hygiene
Added `.gitignore` rules for:
- Python cache/build artifacts
- virtual environments
- editor/OS junk files
- local secrets (`.env`, `.env.*`)

# Hussain

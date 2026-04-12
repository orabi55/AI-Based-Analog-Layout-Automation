"""
ai_agent/ai_chat_bot/agents/prompts.py
========================================
Isolated system prompts for each agent in the multi-agent pipeline.

Each prompt is deliberately SHORT and focused on exactly one task.
This prevents "prompt dilution" — the core insight from LayoutCopilot.
"""

from pathlib import Path


# ─────────────────────────────────────────────────────────────────
# Analog Knowledge Base (injected ONLY into the Analyzer Agent)
# These are UNIVERSAL layout principles — not tied to any topology.
# ─────────────────────────────────────────────────────────────────
ANALOG_KB = """\
=== ANALOG LAYOUT PRINCIPLES ===

## GENERAL PLACEMENT RULES
- Devices that share a net should be placed ADJACENT to minimise wire length.
- Devices with identical W/L/nf are matching candidates -> place adjacent, same orientation.
- PMOS = TOP row(s). NMOS = BOTTOM row(s). Never mix types in one row.
- Multiple PMOS and NMOS rows are allowed for rectangular form factor.
- X-pitch = 0.294 um per device slot (standard). Abutted pitch = 0.070 um.
- Row pitch = 0.668 um.

## TRANSISTOR ABUTMENT (DIFFUSION SHARING)
- When two transistors of the same type share a SOURCE or DRAIN net, they should ABUT.
- Abutment eliminates diffusion break, reducing parasitic capacitance and area.
- Abutted devices MUST be in the same row with the same orientation.

## MATCHING TECHNIQUES (for precision analog)
- INTERDIGITATION (ABBA): For differential pairs and current mirrors.
  Place fingers of matched transistors in alternating pattern: A1 B1 B2 A2 A3 B3 B4 A4
  This cancels linear process gradients and improves matching.
- COMMON-CENTROID: For 4+ matched devices. Arrange symmetrically around a center.
  Pattern for ABCD: D C B A A B C D (mirror around center axis).
  Cancels both linear and quadratic process gradients.
- SYMMETRIC MIRRORING: For cross-coupled pairs. Place A and B adjacent with
  mirror symmetry. This ensures identical parasitic environments.

## TOPOLOGY-SPECIFIC RULES
- DIFFERENTIAL PAIR: Two devices with complementary gate inputs (VINP/VINN)
  -> MUST be SYMMETRIC about centre. Use ABBA interdigitation for best matching.
  -> Place tail current source ADJACENT to the diff pair.
- CURRENT MIRROR: Diode-connected device + copies sharing that gate net
  -> MUST be ADJACENT with ABBA interdigitation. Same orientation.
- STRONG-ARM LATCH COMPARATOR:
  -> Cross-coupled latch pairs at CENTER of each row.
  -> Diff pair flanking the latch. CLK switches at outer edges.
  -> Tail current source adjacent to diff pair.
  -> PMOS and NMOS latch pairs vertically aligned for shortest routing.
- CASCODE: Stacked between mirror and output -> same x-slot vertically.
- TRANSMISSION GATE: NMOS+PMOS pair sharing drain/source
  -> place vertically aligned (same x-slot).
- LOGIC GATES (NAND/NOR/XOR/INV): Complementary PMOS/NMOS stacks
  -> align PMOS above its corresponding NMOS vertically.
- FOLDED-CASCODE OTA:
  -> Input diff pair at center. Cascode devices flanking.
  -> CMFB devices near output stage. Bias mirrors at edges.

## PARASITIC-AWARE PLACEMENT
- CRITICAL signal nets (output, clock, feedback): minimise wire length.
- POWER nets (VDD/GND): route along row edges, wide metal.
- HIGH-FREQUENCY paths: minimise parasitic capacitance at sensitive nodes.
- GUARD RINGS: For substrate noise isolation between analog and digital.
- DUMMY DEVICES: Place at row edges to protect active devices from edge effects.
"""


# ─────────────────────────────────────────────────────────────────
# 1. CHAT / QUESTION Agent — conversational replies
# ─────────────────────────────────────────────────────────────────
def build_chat_prompt(layout_context: dict | None) -> str:
    """Prompt for friendly conversational / question-answering responses."""
    prompt = (
        "You are a friendly Analog IC Layout Engineering assistant "
        "inside a Symbolic Layout Editor.\n"
        "Respond warmly and helpfully. "
        "For questions about the layout, use the device data below.\n"
        "NEVER output [CMD] blocks — you are in conversational mode only.\n\n"
    )
    prompt += _format_layout_context(layout_context)
    return prompt


# ─────────────────────────────────────────────────────────────────
# 2. ANALYZER Agent — proposes high-level analog solutions
# ─────────────────────────────────────────────────────────────────
def build_analyzer_prompt(layout_context: dict | None) -> str:
    """Prompt for the Analyzer Agent.

    CRITICAL: The Analyzer must first identify the ACTUAL circuit
    topology from the device data before making any suggestions.
    It must NEVER suggest optimisations for sub-circuits that do
    not exist in the current layout.
    """
    prompt = (
        "You are an expert Analog IC Layout Analyst.\n\n"
        "STEP 1 — IDENTIFY THE CIRCUIT:\n"
        "Read the device list and net connections below CAREFULLY.\n"
        "Determine what kind of circuit this actually is by examining:\n"
        "  - Which devices share gate/drain/source nets\n"
        "  - The number of NMOS vs PMOS devices\n"
        "  - Signal flow patterns (inputs -> logic -> outputs)\n"
        "State your conclusion: e.g. 'This is a 2-input XOR gate' or\n"
        "'This is a folded-cascode OTA with CMFB'.\n\n"
        "STEP 2 — PROPOSE IMPROVEMENTS:\n"
        "Based on the ACTUAL circuit topology you identified in Step 1,\n"
        "propose 2-4 numbered layout improvement strategies.\n\n"
        "CRITICAL RULES:\n"
        "- ONLY suggest improvements relevant to the actual circuit.\n"
        "- Do NOT mention differential pairs if there are none.\n"
        "- Do NOT mention current mirrors if there are none.\n"
        "- Reference REAL device IDs (e.g. MM2, MM5) in your suggestions.\n"
        "- Focus on: net-driven adjacency, vertical PMOS/NMOS alignment,\n"
        "  shared-net grouping, and dummy insertion.\n"
        "- Do NOT generate [CMD] blocks or JSON.\n"
        "- Do NOT specify exact coordinates.\n"
        "- Keep each strategy to 1-2 sentences.\n\n"
        "REFERENCE KNOWLEDGE:\n"
        f"{ANALOG_KB}\n\n"
    )
    prompt += _format_layout_context(layout_context)
    return prompt


# ─────────────────────────────────────────────────────────────────
# 3. SOLUTION REFINER Agent — presents options to the user
# ─────────────────────────────────────────────────────────────────
def build_refiner_prompt() -> str:
    """Prompt for the Solution Refiner Agent."""
    return (
        "You are a Solution Presenter for an analog layout design tool.\n"
        "You will receive a list of high-level improvement strategies "
        "from the Analyzer.\n\n"
        "Your job:\n"
        "1. Format each strategy as a numbered option with a brief "
        "   explanation of WHY it helps.\n"
        "2. Ask the designer which option(s) they want to proceed with.\n"
        "3. Be concise — max 3 sentences per option.\n"
        "4. End with: 'Please reply with the number(s) of the options "
        "   you'd like to apply, or describe any modifications.'\n"
        "5. Do NOT generate [CMD] blocks.\n"
    )


# ─────────────────────────────────────────────────────────────────
# 4. SOLUTION ADAPTER Agent — maps approved plan to devices
# ─────────────────────────────────────────────────────────────────
def build_adapter_prompt(layout_context: dict | None) -> str:
    """Prompt for the Solution Adapter Agent."""
    prompt = (
        "You are a Solution Adapter for an analog layout tool.\n"
        "You receive an approved high-level strategy and the current "
        "layout state.\n\n"
        "Your ONLY job:\n"
        "1. Map the abstract strategy to SPECIFIC device IDs from the "
        "   layout data below.\n"
        "2. Output a list of CONCRETE directives in plain English.\n"
        "   Example: 'Swap MM3 and MM5', 'Add 1 nmos dummy on the left'.\n"
        "3. Each directive must name real device IDs from the layout.\n"
        "4. Do NOT generate [CMD] JSON blocks — the Code Generator "
        "   will handle that.\n\n"
    )
    prompt += _format_layout_context(layout_context)
    return prompt


# ─────────────────────────────────────────────────────────────────
# 5. CODE GENERATOR Agent — produces [CMD] JSON blocks
# ─────────────────────────────────────────────────────────────────
def build_codegen_prompt(layout_context: dict | None) -> str:
    """Prompt for the Code Generator Agent (Concrete Request Processor).

    Enhanced with grid-awareness so coordinates snap to real device
    widths instead of arbitrary floats like -5.53.
    """
    # Compute grid info from context
    grid_info = _compute_grid_info(layout_context)

    prompt = (
        "You are a strict JSON command generator for a layout editor.\n\n"
        "RULE #1: For ANY action, you MUST output a [CMD]{...}[/CMD] block.\n"
        "Available actions:\n"
        '[CMD]{"action":"swap","device_a":"MM28","device_b":"MM25"}[/CMD]\n'
        '[CMD]{"action":"move","device":"MM3","x":1.176,"y":0.0}[/CMD]\n'
        '[CMD]{"action":"move_row","type":"pmos","y":1.336}[/CMD]\n'
        '[CMD]{"action":"abut","device_a":"MM6","device_b":"MM29"}[/CMD]\n'
        '[CMD]{"action":"add_dummy","type":"nmos","count":2,"side":"left"}[/CMD]\n\n'
        "ABUTMENT RULES (CRITICAL):\n"
        "- Use 'abut' ONLY for transistors that share a SOURCE or DRAIN net.\n"
        "- Abutting MM_A and MM_B moves them side-by-side (X distance = 0.070µm).\n"
        "- It also sets 'abut_right':true on MM_A and 'abut_left':true on MM_B.\n\n"
        "COORDINATE RULES (CRITICAL - follow EXACTLY):\n"
        f"- Device width (X pitch) = {grid_info['pitch']:.4f} um.\n"
        f"- PMOS row Y = {grid_info['pmos_y']:.4f} um.\n"
        f"- NMOS row Y = {grid_info['nmos_y']:.4f} um.\n"
        f"- Row pitch (distance between rows) = {grid_info['row_pitch']:.4f} um.\n"
        "- All X coordinates MUST be multiples of the device width (pitch).\n"
        "- Y coordinates MUST be on the row grid: row_Y = base_Y + N * row_pitch.\n"
        "- You CAN change Y coordinates to add vertical space between rows.\n"
        "- To add space between NMOS and PMOS rows, use 'move_row' action to\n"
        "  shift ALL devices of one type up or down by N * row_pitch.\n"
        "- To move a single device, set its Y to any valid row grid value.\n"
        "- Do NOT invent arbitrary coordinates like -5.53 or 3.7 - always use\n"
        "  the row grid formula (base + N * row_pitch).\n\n"
        "GENERAL RULES:\n"
        "- Use full device IDs (MM28 not 28).\n"
        "- Multiple [CMD] blocks are OK.\n"
        "- add_dummy: type=nmos|pmos, count defaults to 1, side=left|right.\n"
        "- move_row: type=pmos|nmos, y=new Y for ALL devices of that type.\n"
        "- Prefer 'swap' over 'move' when rearranging two devices.\n"
        "- Write the [CMD] block FIRST, then 1-2 sentences confirming.\n"
        "- NEVER explain analog theory. NEVER hallucinate device IDs.\n"
        "- Only use device IDs that appear in the layout data below.\n\n"
    )
    prompt += _format_layout_context(layout_context)
    return prompt


def _compute_grid_info(layout_context: dict | None) -> dict:
    """Extract grid info from the current layout context."""
    default = {"pitch": 0.294, "pmos_y": 0.0, "nmos_y": 0.668, "row_pitch": 0.668}
    if not layout_context:
        return default

    nodes = layout_context.get("nodes", [])
    if not nodes:
        return default

    pmos_ys = []
    nmos_ys = []
    widths = []
    for n in nodes:
        geo = n.get("geometry", {})
        ntype = n.get("type", "")
        y = geo.get("y", 0.0)
        w = geo.get("width", 0.0)
        if w > 0:
            widths.append(w)
        if ntype == "pmos":
            pmos_ys.append(y)
        elif ntype == "nmos":
            nmos_ys.append(y)

    pitch = min(widths) if widths else 0.294
    pmos_y = min(pmos_ys) if pmos_ys else 0.0
    nmos_y = min(nmos_ys) if nmos_ys else 0.668

    # Compute row_pitch from the gap between NMOS and PMOS rows
    row_pitch = 0.668
    if pmos_ys and nmos_ys:
        # Row pitch = difference between PMOS and NMOS row Y values
        row_pitch = abs(pmos_y - nmos_y)
        if row_pitch < 0.1:
            row_pitch = 0.668  # fallback if rows overlap

    return {"pitch": pitch, "pmos_y": pmos_y, "nmos_y": nmos_y, "row_pitch": row_pitch}


# ─────────────────────────────────────────────────────────────────
# Helper: format layout context into a compact text block
# ─────────────────────────────────────────────────────────────────
def _format_layout_context(layout_context: dict | None) -> str:
    """Convert the layout context dict into a compact text summary."""
    if not layout_context:
        return "(No layout loaded)\n"

    nodes         = layout_context.get("nodes",         [])
    edges         = layout_context.get("edges",         [])
    terminal_nets = layout_context.get("terminal_nets", {})
    sp_file       = layout_context.get("sp_file_path",  "")

    lines = []
    if sp_file:
        lines.append(f"Active netlist: {Path(sp_file).name}")

    lines.append(f"=== CURRENT LAYOUT ({len(nodes)} devices) ===")
    for n in nodes:
        nid   = n.get("id",       "?")
        ntype = n.get("type",     "?")
        geo   = n.get("geometry", {})
        elec  = n.get("electrical", {})
        orient = geo.get("orientation", "R0")
        dummy_tag = " [DUMMY]" if n.get("is_dummy") else ""

        line = (
            f"  {nid} ({ntype}{dummy_tag}) "
            f"pos=({geo.get('x', 0):.4f},{geo.get('y', 0):.4f}) "
            f"size=({geo.get('width', 0):.4f}x{geo.get('height', 0):.4f}) "
            f"orient={orient}"
        )
        elec_parts = [f"{k}={elec[k]}" for k in ("nf", "nfin", "l", "w") if k in elec]
        if elec_parts:
            line += f" [{', '.join(elec_parts)}]"

        tnets = terminal_nets.get(nid, {})
        if tnets:
            parts = [f"{t}={tnets[t]}" for t in ("D", "G", "S") if t in tnets]
            line += f"  nets({', '.join(parts)})"
        lines.append(line)

    all_nets = sorted({e.get("net", "") for e in edges if e.get("net")})
    if all_nets:
        lines.append(f"\nNets: {', '.join(all_nets)}")

    return "\n".join(lines) + "\n"

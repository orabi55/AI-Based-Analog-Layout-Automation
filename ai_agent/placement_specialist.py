"""
Placement Specialist Agent
===========================
Generates [CMD] blocks for device positioning while enforcing strict
inventory conservation, row-based analog constraints, and routing quality.
"""

from ai_agent.analog_kb import ANALOG_LAYOUT_RULES


# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------
PLACEMENT_SPECIALIST_PROMPT = """\
You are the PLACEMENT SPECIALIST agent in a multi-agent analog IC layout system.
Your job is to rearrange existing devices on a symbolic grid to improve analog
circuit quality: symmetry, matching, and routing wire length ,You Must check current placement to avoid overlapping transistors while optimizing .

╔══════════════════════════════════════════════════════════════════════╗
║  RULE 0 — CONSERVATION GUARD (NEVER BREAK THIS)                      ║
╚══════════════════════════════════════════════════════════════════════╝
• Every ID in "IMMUTABLE TRANSISTORS" MUST be placed exactly once.
• Never invent a new ID. Never drop an ID. Never rename an ID.
• Dummies (DUMMYP*, DUMMYN*) may be repositioned but never deleted.
• If you do not move a device, it stays at its current (x, y) automatically.

╔══════════════════════════════════════════════════════════════════════╗
║  RULE 1 — ROW ASSIGNMENT (READ CAREFULLY)                            ║
╚══════════════════════════════════════════════════════════════════════╝
The layout has exactly TWO row types:
  • PMOS ROW  — all PMOS transistors share ONE y-value (the PMOS row y).
  • NMOS ROW  — all NMOS transistors share ONE y-value (the NMOS row y).

The PMOS row y is ALWAYS a SMALLER (more negative or less positive) number
than the NMOS row y — meaning PMOS sits ABOVE NMOS on screen.

⚠  CRITICAL Y-COORDINATE RULE:
  → You MUST copy each device's y exactly from the CURRENT LAYOUT INVENTORY.
  → You are FORBIDDEN from inventing a y-coordinate.
  → You are FORBIDDEN from swapping a PMOS device into the NMOS row or vice versa.
  → A PMOS device keeps its PMOS row y. An NMOS device keeps its NMOS row y.
  → If you move a device, only change its x. Keep y identical to inventory.

VERTICAL STACKING INTENT:
  When a PMOS and an NMOS device are functionally paired (same net, mirror,
  diff-pair), place them at the SAME x-coordinate — one in the PMOS row,
  one in the NMOS row. This creates a clean vertical stack and minimises
  the vertical wire connecting them.

╔══════════════════════════════════════════════════════════════════════╗
║  RULE 2 — NO OVERLAPS (ZERO TOLERANCE)                               ║
╚══════════════════════════════════════════════════════════════════════╝
• X-pitch is 0.294 µm. Each device occupies exactly ONE x-slot.
• Two devices of the SAME TYPE in the SAME ROW must have DIFFERENT x values.
• Allowed x values: 0.294 × n for integer n ≥ 0.
  Examples: 0.000, 0.294, 0.588, 0.882, 1.176, 1.470, 1.764, 2.058 …
• Before finalising, build a mental table:

    PMOS ROW  |  x=0.000 → MM_?  |  x=0.294 → MM_?  |  x=0.588 → MM_?  | …
    NMOS ROW  |  x=0.000 → MM_?  |  x=0.294 → MM_?  |  x=0.588 → MM_?  | …

  Each cell must contain AT MOST one device ID. If two IDs land in the
  same cell, you have an overlap — RESOLVE IT before outputting CMDs.

╔══════════════════════════════════════════════════════════════════════╗
║  RULE 3 — DUMMY DEVICE PLACEMENT                                     ║
╚══════════════════════════════════════════════════════════════════════╝
• Dummies (is_dummy=True) must be placed at the FAR LEFT or FAR RIGHT
  of their row — never in the centre between active transistors.
• DUMMYP* devices go in the PMOS row (keep their PMOS y value).
• DUMMYN* devices go in the NMOS row (keep their NMOS y value).
• Slot assignment: if active devices occupy slots 1…N, dummies go at
  slot 0 (left edge) or slot N+1, N+2 (right edge).

╔══════════════════════════════════════════════════════════════════════╗
║  RULE 4 — ROUTING-AWARE PLACEMENT                                    ║
╚══════════════════════════════════════════════════════════════════════╝
Minimise total wire length by following these priorities (in order):

  PRIORITY 1 — MATCHED PAIRS (highest priority):
    Devices with identical W/L/nf that share a net must be placed
    ADJACENT (consecutive x-slots) in their row.
    → Differential pair halves: same x-distance from the row centre.
    → Current mirror pair: adjacent slots, same orientation.

  PRIORITY 2 — NET ADJACENCY:
    Two devices sharing a Gate, Drain, or Source net should be as close
    as possible horizontally. Minimise the x-span of each net.
    Net span = |max_x_of_devices_on_net  −  min_x_of_devices_on_net|
    → Lower span = shorter wire = better routing.

  PRIORITY 3 — VERTICAL ALIGNMENT:
    A PMOS and NMOS that share a net (e.g., load–driver pair) should be
    placed at the same x-slot so the vertical wire is zero length.

  PRIORITY 4 — SIGNAL FLOW LEFT-TO-RIGHT:
    Input devices on the left, output devices on the right.
    Bias/tail devices in the centre or far side away from signal path.

╔══════════════════════════════════════════════════════════════════════╗
║  RULE 5 — EXTREME REORGANIZATION REQUIRES BOLD MOVES                 ║
╚══════════════════════════════════════════════════════════════════════╝
• DO NOT BE LAZY. Do not just make 1 or 2 cosmetic swaps.
• You are highly encouraged to COMPLETELY SCRAMBLE the X-coordinates of ALL devices to find the mathematically optimal shortest wire length.
• Treat the initial coordinates as starting garbage. Your goal is a dense, highly entangled global cluster.
• Output as many `move` or `swap` commands as you need to build the perfect arrangement.

╔══════════════════════════════════════════════════════════════════════╗
║  STEP-BY-STEP THINKING PROTOCOL (follow this order internally)       ║
╚══════════════════════════════════════════════════════════════════════╝
Step 1 — Read inventory. Identify all PMOS devices and their current y.
         Identify all NMOS devices and their current y.
Step 2 — Read topology constraints. Find matched pairs, diff-pairs, mirrors.
Step 3 — Decide x-sequence for PMOS row (left to right).
         Rule: matched/paired devices must be adjacent.
Step 4 — Decide x-sequence for NMOS row.
         Rule: align paired NMOS devices below their PMOS counterparts (same x).
Step 5 — Fill the mental table. Check every (x, row) cell has ≤ 1 device.
Step 6 — Place dummies at row edges.
Step 7 — Output [CMD] blocks. Use ONLY move or swap actions.
Step 8 — Self-check: count IDs in your CMDs vs IMMUTABLE TRANSISTORS list.
         If any ID is missing, add a move CMD to place it.

╔══════════════════════════════════════════════════════════════════════╗
║  OUTPUT FORMAT                                                        ║
╚══════════════════════════════════════════════════════════════════════╝
Output [CMD] blocks FIRST — ALL of them — then one sentence of explanation.

Supported actions:
  [CMD]{"action":"swap","device_a":"MM1","device_b":"MM2"}[/CMD]
  [CMD]{"action":"move","device":"MM3","x":1.176,"y":<COPY_FROM_INVENTORY>}[/CMD]

⚠ For move commands: always copy y exactly from the CURRENT LAYOUT INVENTORY.
  Do NOT write y=0.0 or y=0.668. Write the actual µm value shown in the list.

Only use device IDs from the IMMUTABLE TRANSISTORS list.
"""

# ---------------------------------------------------------------------------
# Helper: build_placement_context
# ---------------------------------------------------------------------------
def build_placement_context(nodes, constraints_text="", terminal_nets=None, edges=None):
    """
    Build a rich context string for the Placement Specialist LLM.

    Improvements:
      - Reports actual PMOS and NMOS y-values from the data (not hardcoded
        0.0 / 0.668) so the AI always copies correct values.
      - Provides a NET ADJACENCY TABLE so the AI sees which devices share
        nets and must be placed close together.
      - Adds a ROUTING COST HINT showing current net x-spans so the AI
        knows which nets most need improvement.
      - Separates active transistors from dummies clearly.

    Args:
        nodes:            list of device dicts from the layout context.
        constraints_text: string output from the Topology Analyst.
        terminal_nets:    dict {dev_id: {'D': net, 'G': net, 'S': net}}
        edges:            list of edge dicts with 'source', 'target', 'net'
    """
    lines = ["=" * 60, "CURRENT LAYOUT INVENTORY", "=" * 60]

    # ── Separate active vs dummy ──────────────────────────────────
    active_devices = sorted(
        [n for n in nodes if not n.get("is_dummy")],
        key=lambda n: (n.get("type", ""), n["id"]),
    )
    dummy_devices = sorted(
        [n for n in nodes if n.get("is_dummy")],
        key=lambda n: n["id"],
    )
    active_ids = [n["id"] for n in active_devices]
    dummy_ids  = [n["id"] for n in dummy_devices]

    # ── Compute actual row y-values from data ─────────────────────
    pmos_ys = sorted(set(
        round(n["geometry"]["y"], 6)
        for n in active_devices if n.get("type") == "pmos"
    ))
    nmos_ys = sorted(set(
        round(n["geometry"]["y"], 6)
        for n in active_devices if n.get("type") == "nmos"
    ))

    # ── Conservation anchors ──────────────────────────────────────
    lines.append(f"\nTOTAL DEVICE COUNT : {len(nodes)}")
    lines.append(f"IMMUTABLE TRANSISTORS ({len(active_ids)}): {', '.join(active_ids)}")
    lines.append(f"FLUID DUMMIES       ({len(dummy_ids)}): {', '.join(dummy_ids) or 'none'}")
    lines.append("")

    # ── Row y-value reference ─────────────────────────────────────
    lines.append("ROW Y-VALUE REFERENCE (copy these exactly into move CMDs):")
    if pmos_ys:
        for y in pmos_ys:
            devs = [n["id"] for n in active_devices
                    if n.get("type") == "pmos" and abs(n["geometry"]["y"] - y) < 1e-4]
            lines.append(f"  PMOS row  y = {y:.6f}   (devices: {', '.join(devs)})")
    else:
        lines.append("  PMOS row  — no PMOS devices found")

    if nmos_ys:
        for y in nmos_ys:
            devs = [n["id"] for n in active_devices
                    if n.get("type") == "nmos" and abs(n["geometry"]["y"] - y) < 1e-4]
            lines.append(f"  NMOS row  y = {y:.6f}   (devices: {', '.join(devs)})")
    else:
        lines.append("  NMOS row  — no NMOS devices found")
    lines.append("")

    # ── Per-device inventory ──────────────────────────────────────
    def _fmt(n):
        geo  = n.get("geometry", {})
        elec = n.get("electrical", {})
        nets = (terminal_nets or {}).get(n["id"], {})
        net_str = " | ".join(f"{t}={v}" for t, v in sorted(nets.items()) if v) if nets else ""
        return (
            f"  {n['id']:<12} type={n.get('type','?'):<5}  "
            f"x={geo.get('x',0):>8.4f}  y={geo.get('y',0):>9.6f}  "
            f"nf={elec.get('nf',1)}  "
            + (f"nets=[{net_str}]" if net_str else "")
        )

    lines.append("PMOS DEVICES (must stay in PMOS row — keep their y value):")
    pmos_nodes = [n for n in active_devices if n.get("type") == "pmos"]
    if pmos_nodes:
        lines.extend(_fmt(n) for n in pmos_nodes)
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("NMOS DEVICES (must stay in NMOS row — keep their y value):")
    nmos_nodes = [n for n in active_devices if n.get("type") == "nmos"]
    if nmos_nodes:
        lines.extend(_fmt(n) for n in nmos_nodes)
    else:
        lines.append("  (none)")
    lines.append("")

    if dummy_devices:
        lines.append("EXISTING DUMMIES (place at row edges only):")
        lines.extend(_fmt(n) for n in dummy_devices)
        lines.append("")

    # ── Net adjacency table ───────────────────────────────────────
    if terminal_nets:
        net_to_devs: dict = {}
        for dev_id, nets in terminal_nets.items():
            for terminal, net_name in nets.items():
                if net_name and net_name.upper() not in ("VDD", "VSS", "GND", "VCC", "AVDD", "AVSS"):
                    net_to_devs.setdefault(net_name, set()).add(dev_id)

        shared_nets = {
            net: devs for net, devs in net_to_devs.items() if len(devs) >= 2
        }
        if shared_nets:
            lines.append("NET ADJACENCY TABLE (devices sharing a net — place these close):")
            pos_x = {n["id"]: n["geometry"].get("x", 0) for n in nodes}
            for net_name in sorted(shared_nets):
                devs = sorted(shared_nets[net_name])
                xs   = [pos_x.get(d, 0) for d in devs]
                span = round(max(xs) - min(xs), 4) if len(xs) > 1 else 0
                lines.append(
                    f"  {net_name:<20} → {', '.join(devs):<40} "
                    f"(current x-span: {span:.4f} µm)"
                )
            lines.append("")

    # ── Current routing cost summary ─────────────────────────────
    if terminal_nets and nodes:
        pos_x = {n["id"]: n["geometry"].get("x", 0) for n in nodes}
        net_spans = {}
        for dev_id, nets in terminal_nets.items():
            for _, net_name in nets.items():
                if net_name and net_name.upper() not in ("VDD", "VSS", "GND", "VCC", "AVDD", "AVSS"):
                    net_spans.setdefault(net_name, []).append(pos_x.get(dev_id, 0))

        worst = sorted(
            [(net, max(xs) - min(xs)) for net, xs in net_spans.items() if len(xs) >= 2],
            key=lambda t: -t[1],
        )[:5]
        if worst:
            lines.append("ROUTING COST — worst 5 nets by x-span (reduce these spans):")
            for net_name, span in worst:
                lines.append(f"  {net_name:<20} current span = {span:.4f} µm")
            lines.append("")

    # ── Topology constraints from Stage 1 ────────────────────────
    if constraints_text:
        lines.append("=" * 60)
        lines.append("TOPOLOGY CONSTRAINTS (from Topology Analyst — Stage 1)")
        lines.append("=" * 60)
        lines.append(constraints_text)
        lines.append("")

    # ── Net-sharing score-based placement order (routing hint) ──────
    if terminal_nets and nodes:
        net_to_devs2: dict = {}
        for dev_id, nets in terminal_nets.items():
            for _, net_name in nets.items():
                if net_name and net_name.upper() not in ("VDD", "VSS", "GND", "VCC", "AVDD", "AVSS"):
                    net_to_devs2.setdefault(net_name, set()).add(dev_id)

        # Score: how many signal nets does each PMOS/NMOS pair in common?
        def _net_score(dev_a, dev_b):
            nets_a = set(v for v in (terminal_nets.get(dev_a) or {}).values() if v)
            nets_b = set(v for v in (terminal_nets.get(dev_b) or {}).values() if v)
            return len(nets_a & nets_b)

        # Sort PMOS and NMOS separately by descending shared-net count with neighbors
        def _sort_by_net_sharing(dev_list):
            if len(dev_list) <= 1:
                return dev_list
            ids = [n["id"] for n in dev_list]
            scores = {d: sum(_net_score(d, o) for o in ids if o != d) for d in ids}
            return sorted(dev_list, key=lambda n: -scores.get(n["id"], 0))

        pmos_sorted = _sort_by_net_sharing(pmos_nodes)
        nmos_sorted = _sort_by_net_sharing(nmos_nodes)

        lines.append("RECOMMENDED PLACEMENT ORDER (left-to-right, sort by highest net sharing):")
        if pmos_sorted:
            lines.append("  PMOS row (left→right): " + " | ".join(n["id"] for n in pmos_sorted))
        if nmos_sorted:
            lines.append("  NMOS row (left→right): " + " | ".join(n["id"] for n in nmos_sorted))
        lines.append("  (Align paired PMOS/NMOS at the same x-slot for minimal vertical wire)")
        lines.append("")

    # ── Final instruction ─────────────────────────────────────────
    lines.append("=" * 60)
    lines.append("INSTRUCTION:")
    lines.append("  1. Follow the Step-by-Step Thinking Protocol in the system prompt.")
    lines.append("  2. Output [CMD] blocks to place EVERY device in the inventory.")
    lines.append("  3. Copy y values EXACTLY from ROW Y-VALUE REFERENCE above.")
    lines.append("  4. No two devices in the same row may share an x-value.")
    lines.append("  5. Paired devices must be adjacent or vertically stacked.")
    lines.append("=" * 60)

    return "\n".join(lines)

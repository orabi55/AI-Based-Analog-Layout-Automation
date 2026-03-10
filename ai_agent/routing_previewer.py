"""
Routing Pre-Viewer Agent
========================
Evaluates the current placement for estimated wire length / net-crossing
complexity and recommends swap [CMD] blocks to reduce routing congestion.

Improvements over previous version:
  - Upgraded ROUTING_PREVIEWER_PROMPT with detailed reasoning protocol,
    net classification (critical vs bias), and move CMD support.
  - score_routing() now computes per-net wire-length estimate (Manhattan
    distance sum) in addition to the crossing count, giving a more accurate
    quality signal.
  - Adds net_criticality classification: differential/output nets are HIGH,
    bias/tail nets are LOW — the LLM focuses effort on the right nets.
  - format_routing_for_llm() now outputs a structured table with x-spans,
    wire-length estimates, and device positions so the LLM can reason about
    specific swaps rather than guessing.
  - Adds a SAME-ROW vs CROSS-ROW annotation per net so the LLM knows which
    nets will need a long vertical detour.
"""

# ---------------------------------------------------------------------------
# Net classification helpers
# ---------------------------------------------------------------------------
_POWER_NETS = frozenset({"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS"})

# Heuristic: nets whose names suggest they carry differential signals or
# outputs are "critical". Bias/tail nets are "low priority".
_CRITICAL_KEYWORDS   = ("out", "vout", "vop", "von", "inp", "inn", "vip", "vim",
                         "vin", "ck", "clk", "data", "q", "qb", "z", "y", "a", "b")
_BIAS_KEYWORDS       = ("bias", "tail", "nbias", "pbias", "ntail", "ptail",
                        "ibias", "vbias", "vcm", "cmfb", "pun", "pdn")


def _classify_net(net_name: str) -> str:
    """Return 'critical', 'bias', or 'signal' for a given net name."""
    n = net_name.lower()
    if any(n == kw or n.startswith(kw) for kw in _BIAS_KEYWORDS):
        return "bias"
    if any(n == kw or n.startswith(kw) or n.endswith(kw) for kw in _CRITICAL_KEYWORDS):
        return "critical"
    return "signal"


# ---------------------------------------------------------------------------
# System prompt (7-section LayoutCopilot structure)
# ---------------------------------------------------------------------------
ROUTING_PREVIEWER_PROMPT = """\
### I. ROLE PLAY
You are the ROUTING PRE-VIEWER agent in a multi-agent analog IC layout system.
You are an expert analog router with deep knowledge of parasitic-aware placement.
You minimize wire length, crossing count, and coupling risks on critical nets.

### II. WORKFLOW OVERVIEW
You are the final Stage 4 of a 4-stage pipeline:
  Stage 1: Topology Analyst — constraint extraction.
  Stage 2: Placement Specialist — device placement.
  Stage 3: DRC Critic — geometric violations fixed.
  Stage 4 (YOU): Routing Pre-Viewer — routing quality optimisation.

### III. TASK DESCRIPTION
Receive the current placement and routing analysis table.
Output [CMD] blocks that reduce total wire length and net crossings,
prioritising CRITICAL signal nets over BIAS nets.

### IV. PIPELINE (follow these steps internally)
Step 1: Read the ROUTING ANALYSIS TABLE. Find nets with the largest spans.
Step 2: Filter to CRITICAL and SIGNAL nets only (ignore BIAS for now).
Step 3: For each bad net: identify which two devices are the farthest apart.
Step 4: Decide: can a swap reduce this net's span WITHOUT increasing another's?
Step 5: Check: does the swap break a matched pair? If yes, reject the swap.
Step 6: Repeat for up to 3-5 total CMDs.
Step 7: If score is already low (< 3 crossings, all spans < 1.0 µm),
         do NOT output any [CMD] blocks.

### V. INFORMATION VERIFICATION
Before outputting [CMD]s, verify:
  [ ] I only swap same-type devices (PMOS with PMOS, NMOS with NMOS).
  [ ] My swaps reduce the CRITICAL net spans, not increase them.
  [ ] I did not separate already-adjacent matched pairs.
  [ ] I used valid device IDs from the device list.

### VI. INTERACTION GUIDELINE
Output [CMD] blocks FIRST, then one sentence of explanation.
Limit: 3-5 CMDs maximum.
If routing is already good: write ONLY
'Routing looks good — no further changes recommended.'

### VII. EXTERNAL KNOWLEDGE
Supported [CMD] action types for routing optimisation:

  SWAP (reorder devices in same row):
  [CMD]{"action":"swap","device_a":"MM1","device_b":"MM2"}[/CMD]
  Only same-type swaps allowed (both PMOS or both NMOS).

  NET PRIORITY (annotate a net as high-priority for the router):
  [CMD]{"action":"net_priority","net":"OUT","priority":"high"}[/CMD]
  priority = "high" | "medium" | "low"

  WIRE WIDTH (set custom wire width for a specific net):
  [CMD]{"action":"wire_width","net":"VDD","width_um":0.5}[/CMD]
  width_um is the target wire width in micrometres.

  WIRE SPACING (set minimum spacing between two nets to reduce coupling):
  [CMD]{"action":"wire_spacing","net_a":"INP","net_b":"INN","spacing_um":0.3}[/CMD]
  spacing_um is the minimum required lateral spacing between the two nets.

  NET REROUTE (flag a net for manual review / rerouting emphasis):
  [CMD]{"action":"net_reroute","net":"VOUT","reason":"High fanout, reduce coupling"}[/CMD]

PRINCIPLES:
- CRITICAL nets (OUT, INP, INN, CLK) carry diff signals / outputs. Minimise span.
- BIAS nets (NBIAS, PBIAS, VTAIL, VCMFB) are less critical. Fix last.
- Adjacent devices sharing a net have wire length ≈ 0 — ideal.
- Cross-row nets (PMOS+NMOS at same x) have short vertical wire — good.
- Never separate already-adjacent matched pairs.
"""




# ---------------------------------------------------------------------------
# Pure-Python routing heuristic (upgraded)
# ---------------------------------------------------------------------------
def score_routing(nodes, edges, terminal_nets):
    """Estimate routing quality from net x-spans and crossing counts.

    Improvements over previous version:
      - Separates PMOS and NMOS device positions so cross-row nets are
        identified accurately.
      - Computes an estimated total wire length (sum of net spans) in µm.
      - Classifies each net as 'critical', 'bias', or 'signal'.
      - Returns per-net data needed for the richer format_routing_for_llm().

    Args:
        nodes:         list of placement node dicts
        edges:         list of edge dicts (each has 'net' key)
        terminal_nets: dict {dev_id: {'D': net, 'G': net, 'S': net}}

    Returns:
        dict: {
            "score":        int,   # total estimated crossing count
            "worst_nets":   [str], # nets with most crossings (up to 5)
            "net_spans":    {net: (min_x, max_x)},
            "net_details":  {net: {span, wire_length, criticality, cross_row,
                                   devices, pmos_devs, nmos_devs}},
            "total_wire_length": float,  # sum of all net spans (µm)
            "summary":      str,
        }
    """
    # ── Build position index (x per device) ──────────────────────
    pos_x    = {}  # dev_id → x (µm)
    dev_type = {}  # dev_id → 'pmos' | 'nmos'
    for n in nodes:
        geo = n.get("geometry", {})
        pos_x[n["id"]]    = float(geo.get("x", 0))
        dev_type[n["id"]] = n.get("type", "nmos")

    # ── Collect all signal nets → devices ────────────────────────
    net_devices: dict = {}  # net → set of dev_ids

    for edge in (edges or []):
        net = edge.get("net", "")
        src = edge.get("source", edge.get("src", ""))
        tgt = edge.get("target", edge.get("tgt", ""))
        if net and net.upper() not in _POWER_NETS:
            net_devices.setdefault(net, set())
            if src in pos_x:
                net_devices[net].add(src)
            if tgt in pos_x:
                net_devices[net].add(tgt)

    for dev_id, nets in (terminal_nets or {}).items():
        if dev_id not in pos_x:
            continue
        for _, net_name in nets.items():
            if net_name and net_name.upper() not in _POWER_NETS:
                net_devices.setdefault(net_name, set()).add(dev_id)

    # ── Per-net details ──────────────────────────────────────────
    net_details: dict = {}
    for net_name, devs in net_devices.items():
        devs = {d for d in devs if d in pos_x}
        if len(devs) < 2:
            continue
        xs        = [pos_x[d] for d in devs]
        pmos_devs = [d for d in devs if dev_type.get(d) == "pmos"]
        nmos_devs = [d for d in devs if dev_type.get(d) == "nmos"]
        x_min, x_max = min(xs), max(xs)
        span      = x_max - x_min
        cross_row = bool(pmos_devs and nmos_devs)
        net_details[net_name] = {
            "span":        round(span, 4),
            "wire_length": round(span, 4),   # Manhattan horizontal component
            "criticality": _classify_net(net_name),
            "cross_row":   cross_row,
            "devices":     sorted(devs),
            "pmos_devs":   sorted(pmos_devs),
            "nmos_devs":   sorted(nmos_devs),
            "x_min":       round(x_min, 4),
            "x_max":       round(x_max, 4),
        }

    # ── Net spans dict (kept for backwards compatibility) ────────
    net_spans = {
        net: (d["x_min"], d["x_max"])
        for net, d in net_details.items()
    }

    # ── Crossing count (overlapping x-spans) ─────────────────────
    nets_list = list(net_spans.items())
    crossings: dict = {}
    for i, (net_a, (a0, a1)) in enumerate(nets_list):
        for net_b, (b0, b1) in nets_list[i + 1:]:
            if a0 < b1 and b0 < a1:
                crossings[net_a] = crossings.get(net_a, 0) + 1
                crossings[net_b] = crossings.get(net_b, 0) + 1

    total_score = sum(crossings.values()) // 2
    worst_nets  = sorted(crossings, key=crossings.get, reverse=True)[:5]

    # ── Total wire length estimate ────────────────────────────────
    total_wire = round(sum(d["wire_length"] for d in net_details.values()), 4)

    # ── Placement Cost Function ───────────────────────────────────
    total_cost = 0
    for net, d in net_details.items():
        span = d["span"]
        if d["criticality"] == "critical":
            total_cost += (span ** 2) * 10
        elif d["criticality"] == "signal":
            total_cost += (span ** 2) * 3
        else:
            total_cost += span * 1
    placement_cost = round(total_cost, 4)

    # ── Summary string ────────────────────────────────────────────
    if total_score == 0 and total_wire < 2.0:
        summary = (
            f"Routing score: 0 crossings, "
            f"total wire length ≈ {total_wire:.3f} µm — placement well-optimised."
        )
    else:
        worst_str = ", ".join(
            f"{n}({crossings.get(n,0)} cross, span={net_details[n]['span']:.3f}µm)"
            for n in worst_nets
        ) if worst_nets else "none"
        summary = (
            f"Routing score: ~{total_score} crossing(s), "
            f"total wire ≈ {total_wire:.3f} µm. "
            f"Worst nets: {worst_str}"
        )

    return {
        "score":            total_score,
        "worst_nets":       worst_nets,
        "net_spans":        net_spans,
        "net_details":      net_details,
        "total_wire_length": total_wire,
        "placement_cost":   placement_cost,
        "summary":          summary,
    }


# ---------------------------------------------------------------------------
# Format routing result for LLM (upgraded)
# ---------------------------------------------------------------------------
def format_routing_for_llm(routing_result, nodes, terminal_nets):
    """Format score_routing() output as a structured LLM prompt snippet.

    Improvements:
      - Groups nets by criticality (CRITICAL first, then SIGNAL, then BIAS).
      - Shows x-span in µm and labels cross-row nets explicitly.
      - Lists the specific devices on each bad net with their x-positions
        so the LLM can reason about concrete swap options.
      - Adds a SAME-TYPE SWAP CANDIDATES section showing which device pairs
        in the same row are the best candidates to move.
    """
    lines = []

    # ── Header ────────────────────────────────────────────────────
    lines.append("=" * 60)
    lines.append("ROUTING ANALYSIS REPORT")
    lines.append("=" * 60)
    lines.append(routing_result["summary"])
    lines.append("")

    # ── Per-device position table ─────────────────────────────────
    pos_x    = {}
    dev_type = {}
    for n in nodes:
        pos_x[n["id"]]    = round(float(n.get("geometry", {}).get("x", 0)), 4)
        dev_type[n["id"]] = n.get("type", "nmos")

    # ── Net detail table grouped by criticality ───────────────────
    net_details = routing_result.get("net_details", {})

    for priority_label, priority_key in [
        ("CRITICAL NETS (fix these first)", "critical"),
        ("SIGNAL NETS", "signal"),
        ("BIAS NETS (fix only if critical nets are clean)", "bias"),
    ]:
        group = {
            net: d for net, d in net_details.items()
            if d["criticality"] == priority_key
        }
        if not group:
            continue

        lines.append(f"── {priority_label} ──")
        # Sort by span descending within group
        for net_name in sorted(group, key=lambda n: -group[n]["span"]):
            d = group[net_name]
            cross_tag = " [CROSS-ROW]" if d["cross_row"] else ""
            lines.append(
                f"  {net_name:<20} span={d['span']:.4f}µm{cross_tag}"
            )
            # Show devices and their x-positions
            for dev_id in d["devices"]:
                x   = pos_x.get(dev_id, 0)
                dt  = dev_type.get(dev_id, "?")
                lines.append(f"    {dev_id:<12} ({dt})  x={x:.4f}")
        lines.append("")

    # ── Swap candidate table ──────────────────────────────────────
    # For each bad net (worst 3 CRITICAL nets), suggest the swap that would
    # most reduce the span: bring the rightmost device next to the leftmost.
    lines.append("── SWAP CANDIDATES (to reduce worst net spans) ──")

    worst_nets   = routing_result.get("worst_nets", [])
    # Supplement with highest-span critical nets if worst_nets is sparse
    critical_by_span = sorted(
        [n for n, d in net_details.items() if d["criticality"] == "critical"],
        key=lambda n: -net_details[n]["span"],
    )
    candidate_nets = list(dict.fromkeys(worst_nets + critical_by_span))[:5]

    if not candidate_nets:
        lines.append("  No swap candidates — routing is already good.")
    else:
        for net_name in candidate_nets:
            d = net_details.get(net_name)
            if not d or d["span"] < 0.294:
                continue  # already adjacent
            # Find leftmost and rightmost devices ON THE SAME ROW
            pmos = sorted(d["pmos_devs"], key=lambda dev: pos_x.get(dev, 0))
            nmos = sorted(d["nmos_devs"], key=lambda dev: pos_x.get(dev, 0))
            for row_label, row_devs in [("PMOS", pmos), ("NMOS", nmos)]:
                if len(row_devs) >= 2:
                    leftmost  = row_devs[0]
                    rightmost = row_devs[-1]
                    span      = abs(pos_x.get(rightmost, 0) - pos_x.get(leftmost, 0))
                    if span >= 0.294:
                        lines.append(
                            f"  Net {net_name} ({row_label} row): "
                            f"{leftmost} (x={pos_x.get(leftmost,0):.4f}) and "
                            f"{rightmost} (x={pos_x.get(rightmost,0):.4f}) "
                            f"are {span:.4f}µm apart — consider swapping "
                            f"{rightmost} with a device adjacent to {leftmost}."
                        )
    lines.append("")

    lines.append("Suggest swaps via [CMD] blocks to reduce crossings and net spans.")
    return "\n".join(lines)
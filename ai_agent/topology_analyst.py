"""
Topology Analyst Agent
======================
Identifies placement constraints from SPICE netlist topology:
  - shared_gate  → mirror/cascode candidates → must stay adjacent
  - shared_drain → differential-pair loads → symmetry required
  - shared_source → bias-current mirrors → close grouping preferred

Domain helper: analyze_topology() – pure Python, no LLM needed.
"""

import os
import sys

from ai_agent.analog_kb import ANALOG_LAYOUT_RULES

# ---------------------------------------------------------------------------
# System prompt (7-section LayoutCopilot structure)
# ---------------------------------------------------------------------------
TOPOLOGY_ANALYST_PROMPT = """\
### I. ROLE PLAY
You are an expert Analog IC Layout Engineer specialising in topology analysis.
You are part of a multi-agent team. Your specialty is reading SPICE netlists
and device connectivity to identify matched pairs, current mirrors, diff-pairs,
and symmetry requirements. You are methodical, precise, and always verify.

### II. WORKFLOW OVERVIEW
You are Stage 1 of a 4-stage pipeline:
  Stage 1 (YOU): Topology Analyst — extract constraints, present to user.
  Stage 2: Placement Specialist — generate device placement commands.
  Stage 3: DRC Critic — check and fix design rule violations.
  Stage 4: Routing Pre-Viewer — optimise net crossings.
Your output feeds directly into Stage 2. Errors here propagate — be accurate.

### III. TASK DESCRIPTION
Analyse the device list and net connectivity. Identify and name:
  - Differential pairs (same source net, or tail current sharing)
  - Current mirrors (shared gate net, same device type)
  - Cascode structures (stacked bias gate nets)
  - Matched pairs (same W/L/nf values)
  - Symmetry axis devices

### IV. PIPELINE (follow these steps internally)
Step 1: Read each device's type (PMOS/NMOS) and its D/G/S net connections.
Step 2: Group devices that share gate nets — mirror candidates.
Step 3: Group devices that share source nets — diff-pair candidates.
Step 4: Check W/L/nf for matching — identical values → symmetry required.
Step 5: Check for cascode intent (bias gate nets shared between device pairs).
Step 6: Present findings as a numbered list with specific device IDs.
Step 7: Ask the user to confirm before proceeding to Stage 2.

### V. INFORMATION VERIFICATION
Before responding, verify:
  [ ] Did I receive a non-empty device list?
  [ ] Did I identify EVERY device (not just the first few)?
  [ ] Did I check BOTH shared-gate AND shared-source relationships?
If the device list is empty: reply 'No devices found. Please load a layout first.'

### VI. INTERACTION GUIDELINE
End EVERY response with EXACTLY this question:
'Do you confirm these pairings are correct? Reply Yes to proceed,
 or describe any corrections.'
Do NOT generate [CMD] blocks. Do NOT suggest x/y coordinates.
Do NOT output raw JSON.

### VII. EXTERNAL KNOWLEDGE
""" + ANALOG_LAYOUT_RULES

# ---------------------------------------------------------------------------
# Pure-Python domain helper
# ---------------------------------------------------------------------------
def analyze_topology(nodes, terminal_nets, sp_file_path=None):
    """Extract topology constraints from circuit graph and placement data.

    Args:
        nodes: list of placement node dicts (id, type, geometry, electrical)
        terminal_nets: dict {dev_id: {'D': net, 'G': net, 'S': net}}
        sp_file_path: optional path to .sp file for full graph analysis

    Returns:
        str: A compact, human-readable constraint summary for LLM injection.
    """
    constraints = []

    # ---- 1. Try full circuit graph analysis (requires networkx + parser) ----
    graph_constraints = _try_graph_analysis(sp_file_path, nodes)
    if graph_constraints:
        constraints.extend(graph_constraints)

    # ---- 2. Fallback: infer from terminal_nets directly ----
    if not constraints and terminal_nets:
        constraints.extend(_infer_from_terminal_nets(terminal_nets, nodes))

    # ---- 3. Identify PMOS / NMOS rows ----
    pmos_ids = [n["id"] for n in nodes if str(n.get("type","")).lower().startswith("p")]
    nmos_ids = [n["id"] for n in nodes if str(n.get("type","")).lower().startswith("n") and not n.get("is_dummy")]

    # Build summary text
    lines = []
    if pmos_ids:
        lines.append(f"PMOS row ({len(pmos_ids)} devices): {', '.join(pmos_ids[:12])}"
                     + (" ..." if len(pmos_ids) > 12 else ""))
    if nmos_ids:
        lines.append(f"NMOS row ({len(nmos_ids)} devices): {', '.join(nmos_ids[:12])}"
                     + (" ..." if len(nmos_ids) > 12 else ""))
    if constraints:
        lines.append("\nTopology constraints:")
        for c in constraints[:20]:   # cap to avoid prompt bloat
            lines.append(f"  {c}")
    else:
        lines.append("\nNo topology constraints extracted (no SPICE data available).")

    return "\n".join(lines)


def _try_graph_analysis(sp_file_path, nodes):
    """Use parser.circuit_graph if networkx and the .sp file are available."""
    if not sp_file_path or not os.path.isfile(sp_file_path):
        return []
    try:
        import networkx  # noqa: F401
        # Add project root to path
        project_root = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..")
        )
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from parser.netlist_reader import read_netlist
        from parser.circuit_graph import build_circuit_graph

        netlist = read_netlist(sp_file_path)
        G = build_circuit_graph(netlist)
        constraints = []
        for u, v, data in G.edges(data=True):
            rel = data.get("relation", "connection")
            net = data.get("net", "")
            if rel == "shared_gate":
                constraints.append(f"MIRROR/CASCODE: {u} ↔ {v} (gate-net={net})")
            elif rel == "shared_drain":
                constraints.append(f"DIFF-PAIR LOAD: {u} ↔ {v} (drain-net={net})")
            elif rel == "shared_source":
                constraints.append(f"SHARED-SRC: {u} ↔ {v} (net={net})")
        return constraints
    except Exception as exc:
        return [f"(Graph analysis skipped: {exc})"]


def _infer_from_terminal_nets(terminal_nets, nodes):
    """Simple fallback: group devices that share the same gate net."""
    from collections import defaultdict
    gate_groups = defaultdict(list)
    drain_groups = defaultdict(list)
    for dev_id, nets in terminal_nets.items():
        g_net = nets.get("G", "")
        d_net = nets.get("D", "")
        if g_net and g_net.upper() not in ("VDD", "VSS", "GND"):
            gate_groups[g_net].append(dev_id)
        if d_net and d_net.upper() not in ("VDD", "VSS", "GND"):
            drain_groups[d_net].append(dev_id)

    constraints = []
    for net, devs in gate_groups.items():
        if len(devs) >= 2:
            constraints.append(f"MIRROR (shared-gate {net}): {' ↔ '.join(devs)}")
    for net, devs in drain_groups.items():
        if len(devs) >= 2:
            constraints.append(f"DIFF-PAIR (shared-drain {net}): {' ↔ '.join(devs)}")
    return constraints

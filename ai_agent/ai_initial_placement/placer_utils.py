import json
import re
from collections import defaultdict
import copy

# ---------------------------------------------------------------------------
# Robust JSON sanitizer
# ---------------------------------------------------------------------------
def _repair_truncated_json(text: str) -> str:
    """
    Fix truncated JSON by closing any unclosed brackets and braces.
    Handles the common case where an LLM hits its token limit mid-output.

    Parameters
    ----------
    text : str
        The raw, potentially truncated JSON string.

    Returns
    -------
    str
        A string with all detected open brackets (`[`) and braces (`{`)
        properly closed at the end to make it structurally valid JSON.
    """
    text = text.rstrip()
    if text.endswith(","):
        text = text[:-1]

    open_braces = 0
    open_brackets = 0
    in_string = False
    escape = False

    for ch in text:
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            open_braces += 1
        elif ch == '}':
            open_braces -= 1
        elif ch == '[':
            open_brackets += 1
        elif ch == ']':
            open_brackets -= 1

    text = re.sub(r',\s*$', '', text)
    text += ']' * max(0, open_brackets)
    text += '}' * max(0, open_braces)
    return text

def sanitize_json(text: str) -> dict:
    """
    Extract and sanitize LLM output into a strict JSON dictionary.

    Handles edge cases common to LLM generation such as:
    - Markdown code fences (e.g., ```json ... ```)
    - Trailing commas before array/object closures
    - Inline JavaScript-style comments (// ...)
    - Truncated output (by invoking _repair_truncated_json)

    Parameters
    ----------
    text : str
        The raw text response direct from the LLM.

    Returns
    -------
    dict
        A fully parsed JSON dictionary representing the layout geometry.

    Raises
    ------
    ValueError
        If the text cannot be repaired or parsed into valid JSON.
    """
    if not text or len(text.strip()) == 0:
        raise ValueError("Empty response from LLM")

    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    cleaned = re.sub(r"```json\s*", "", text)
    cleaned = re.sub(r"```\s*", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    brace_pos = cleaned.find('{')
    if brace_pos == -1:
        raise ValueError(f"No JSON object found. Raw text:\n{text[:500]}")

    s = cleaned[brace_pos:]
    s = re.sub(r'//[^\n]*', '', s)
    s = re.sub(r',\s*([\]}])', r'\1', s)

    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    s_repaired = _repair_truncated_json(s)
    try:
        return json.loads(s_repaired)
    except json.JSONDecodeError:
        pass

    for trim in range(50, min(len(s), 2000), 50):
        attempt = _repair_truncated_json(s[:-trim])
        try:
            result = json.loads(attempt)
            if isinstance(result, dict) and "nodes" in result:
                print(f"[sanitize_json] Recovered JSON by trimming {trim} chars from end")
                return result
        except json.JSONDecodeError:
            continue

    raise ValueError(f"Could not parse LLM output as JSON. First 500 chars: {s[:500]}")

def _ensure_placement_dict(parsed) -> dict:
    """
    Normalize the result of sanitize_json() to always be a dictionary
    containing a "nodes" key.

    Different LLMs might return a bare list `[{}, {}]` or nest the nodes under
    keys like "placement", "result", or "layout", instead of the expected
    `{"nodes": [{}, {}]}` structure. This function enforces consistency.

    Parameters
    ----------
    parsed : dict | list
        The raw Python object obtained after successfully parsing the LLM JSON.

    Returns
    -------
    dict
        A dictionary strictly containing a "nodes" key mapping to a list.

    Raises
    ------
    ValueError
        If the parsed object structure is entirely unrecognized.
    """
    if isinstance(parsed, list):
        return {"nodes": parsed}
    if isinstance(parsed, dict):
        if "nodes" not in parsed:
            for key in ("placement", "result", "layout", "devices", "placements"):
                if key in parsed and isinstance(parsed[key], list):
                    return {"nodes": parsed[key]}
        return parsed
    raise ValueError(f"Unexpected JSON type from LLM: {type(parsed).__name__}.")

# ---------------------------------------------------------------------------
# Pre-analysis helpers (pure Python - no LLM)
# ---------------------------------------------------------------------------
def _build_net_adjacency(nodes: list, edges: list) -> str:
    """
    Construct a human-readable text block summarizing which devices share
    which signal nets.

    This block is injected directly into the LLM prompt to heavily bias the
    model into placing devices that share nets closer together. It explicitly
    flags nets that cross between PMOS and NMOS rows.

    Parameters
    ----------
    nodes : list
        List of device node dictionaries.
    edges : list
        List of edge dictionaries detailing connectivity (source, target, net).

    Returns
    -------
    str
        A multi-line formatted string enumerating all signal nets and their
        connected devices. Returns a placeholder string if no nets are found.
    """
    _POWER = frozenset({"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS"})
    net_devs: dict = defaultdict(set)
    for e in edges:
        net = e.get("net", "")
        if net and net.upper() not in _POWER:
            net_devs[net].add(e.get("source", ""))
            net_devs[net].add(e.get("target", ""))

    if not net_devs:
        return "  (no signal nets found)"

    dev_type = {n["id"]: n.get("type", "nmos") for n in nodes}
    lines = []
    for net in sorted(net_devs):
        devs = sorted(d for d in net_devs[net] if d)
        pmos = [d for d in devs if dev_type.get(d) == "pmos"]
        nmos = [d for d in devs if dev_type.get(d) == "nmos"]
        cross = " [CROSS-ROW]" if pmos and nmos else ""
        lines.append(f"  {net:<12} -> {', '.join(devs)}{cross}")
    return "\n".join(lines)

def _build_device_inventory(nodes: list, row_summary: str = "") -> str:
    """
    Construct a structured text block enumerating all devices to be placed.

    Separates devices by type (PMOS vs NMOS) and lists out their critical
    electrical parameters (number of fingers, multiplier, length) so the
    model understands the physical bounds and sizing constraints.

    Parameters
    ----------
    nodes : list
        List of node dictionaries containing device metadata.
    row_summary : str, optional
        An optional pre-computed string detailing explicitly assigned rows
        (used when generating multi-row/complex layouts). Defaults to "".

    Returns
    -------
    str
        A multi-line formatted string detailing the device inventory.
    """
    pmos = [n for n in nodes if n.get("type") == "pmos"]
    nmos = [n for n in nodes if n.get("type") == "nmos"]
    lines = []
    lines.append(f"  TOTAL: {len(nodes)} devices ({len(pmos)} PMOS, {len(nmos)} NMOS)")
    lines.append("")
    lines.append("  PMOS devices (must be placed in PMOS rows — see ROW ASSIGNMENT below):")
    for n in sorted(pmos, key=lambda x: x["id"]):
        e = n.get("electrical", {})
        lines.append(f"    {n['id']:<10}  nfin={e.get('nfin',1)}  nf={e.get('nf',1)}  l={e.get('l')}")
    lines.append("")
    lines.append("  NMOS devices (must be placed in NMOS rows — see ROW ASSIGNMENT below):")
    for n in sorted(nmos, key=lambda x: x["id"]):
        e = n.get("electrical", {})
        lines.append(f"    {n['id']:<10}  nfin={e.get('nfin',1)}  nf={e.get('nf',1)}  l={e.get('l')}")

    if row_summary:
        lines.append("")
        lines.append("  PRE-ASSIGNED ROW LAYOUT:")
        lines.append(row_summary)

    return "\n".join(lines)

def _build_block_info(nodes: list, graph_data: dict) -> str:
    """
    Construct a text block defining hierarchical device groupings (blocks).

    Any devices declared as part of the same block/sub-circuit must be kept
    strictly contiguous within the layout row. This alerts the LLM to those bounds.

    Parameters
    ----------
    nodes : list
        List of node dictionaries.
    graph_data : dict
        The full graph JSON data which may contain top-level 'blocks' definitions.

    Returns
    -------
    str
        A multi-line formatted string describing which devices belong to which blocks.
    """
    blocks = graph_data.get("blocks", {})
    if not blocks:
        for n in nodes:
            b = n.get("block")
            if b:
                inst = b.get("instance", "")
                if inst and inst not in blocks:
                    blocks[inst] = {"subckt": b.get("subckt", "?"), "devices": []}
                if inst:
                    blocks[inst]["devices"].append(n.get("id", ""))
    if not blocks:
        return "  (no hierarchical blocks detected)"

    lines = []
    for inst, info in blocks.items():
        subckt = info.get("subckt", "?")
        devs = info.get("devices", [])
        lines.append(f"  {inst} ({subckt}): {', '.join(devs)}")
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Post-placement validation
# ---------------------------------------------------------------------------
def _validate_placement(original_nodes: list, result) -> list:
    """
    Run quick structural sanity checks on the returned AI placement.

    Validates that:
    1. No devices were dropped (missing).
    2. No hallucinated (extra) devices were added.
    3. No two devices occupy the exact same physical slot in a row (collision).
    4. PMOS and NMOS devices have not erroneously swapped rows/types.

    Parameters
    ----------
    original_nodes : list
        The source-of-truth list of nodes sent to the model.
    result : list | dict
        The parsed geometry output from the model. Can be the raw list of nodes
        or a dict containing a 'nodes' key.

    Returns
    -------
    list
        A list of string error messages. If empty, the placement is valid.
    """
    errors = []
    orig_ids  = {n["id"] for n in original_nodes}
    orig_type = {n["id"]: n.get("type") for n in original_nodes}

    if isinstance(result, list):
        placed_nodes = result
    elif isinstance(result, dict):
        placed_nodes = result.get("nodes", [])
    else:
        errors.append(f"Unexpected placement result type: {type(result).__name__} — expected list or dict.")
        return errors

    if not placed_nodes:
        errors.append("Response has no 'nodes' array.")
        return errors

    placed_ids = {n.get("id") for n in placed_nodes if isinstance(n, dict) and n.get("id")}

    missing = orig_ids - placed_ids
    extra   = placed_ids - orig_ids
    if missing:
        errors.append(f"MISSING devices: {sorted(missing)}")
    if extra:
        errors.append(f"EXTRA (invented) devices: {sorted(extra)}")

    # Overlap check — uses actual device widths (bounding-box aligned, zero gap)
    rows = defaultdict(list)
    for n in placed_nodes:
        if not isinstance(n, dict): continue
        y = n.get("geometry", {}).get("y", 0)
        # Use 0.001 tolerance for row grouping
        rows[round(float(y), 3)].append(n)
    
    for y, row_nodes in rows.items():
        sorted_row = sorted(row_nodes, key=lambda n: n.get("geometry", {}).get("x", 0))
        for i in range(len(sorted_row) - 1):
            n1 = sorted_row[i]
            n2 = sorted_row[i+1]
            x1 = n1.get("geometry", {}).get("x", 0)
            x2 = n2.get("geometry", {}).get("x", 0)
            w1 = n1.get("geometry", {}).get("width", 0.294)
            dx = x2 - x1
            
            # Check if they are abutted
            abut1 = n1.get("abutment", {})
            abut2 = n2.get("abutment", {})
            is_abutted = abut1.get("abut_right") and abut2.get("abut_left")
            
            if is_abutted:
                # Target distance is 0.070 (5 fins). Allow some tolerance (0.005)
                if abs(dx - 0.070) > 0.005:
                    errors.append(f"Abutment spacing error between {n1['id']} and {n2['id']}: delta X is {dx:.4f}um, expected 0.070um.")
            else:
                # Non-abutted: n2.x must be >= n1.x + n1.width (bounding boxes touch, zero gap)
                min_x2 = round(x1 + w1, 4)
                if round(x2, 4) < min_x2 - 0.001:
                    errors.append(f"Overlap in row y={y} between {n1['id']} and {n2['id']}: n2.x={x2:.4f} < n1.x+w1={min_x2:.4f} (bounding boxes overlap).")


    for n in placed_nodes:
        if not isinstance(n, dict):
            continue
        dev_id   = n.get("id", "?")
        expected = orig_type.get(dev_id)
        actual   = n.get("type")
        if expected and actual and expected != actual:
            errors.append(f"Device {dev_id} changed type: was {expected}, now {actual}")

    return errors

# ---------------------------------------------------------------------------
# Coordinate normalisation
# ---------------------------------------------------------------------------
def _normalise_coords(nodes: list) -> tuple:
    """
    Shift all node Y-coordinates uniformly so that the minimum Y value is 0.0.

    This ensures that components always sit on or above the positive Y-axis,
    normalizing topologies plotted in negative coordinate space (e.g. from
    certain extraction tools) before passing them to the AI.

    Parameters
    ----------
    nodes : list
        List of node dictionaries with absolute 'geometry' coordinates.

    Returns
    -------
    tuple
        A 2-tuple `(normalised_nodes, y_offset)` where:
        - `normalised_nodes` is a deep copy of the original nodes with shifted Y values.
        - `y_offset` is the float amount that was ADDED to the original coordinates.
    """
    if not nodes:
        return nodes, 0.0

    all_ys = [n.get("geometry", {}).get("y", 0.0) for n in nodes if "geometry" in n]
    if not all_ys:
        return nodes, 0.0

    min_y    = min(all_ys)
    y_offset = -min_y

    if abs(y_offset) < 1e-9:
        return nodes, 0.0

    normalised = copy.deepcopy(nodes)
    for n in normalised:
        geo = n.get("geometry", {})
        if "y" in geo:
            geo["y"] = round(geo["y"] + y_offset, 6)

    return normalised, y_offset

def _restore_coords(placed_nodes: list, y_offset: float) -> list:
    """
    Un-shift Y coordinates back to their original extraction frame.

    This reverses the arithmetic applied by `_normalise_coords` so that the
    AI's layout strictly aligns with the source schematic's bounding box logic.

    Parameters
    ----------
    placed_nodes : list
        List of node dictionaries with AI-assigned coordinates.
    y_offset : float
        The float amount originally added by `_normalise_coords`.

    Returns
    -------
    list
        A deep copy of the `placed_nodes` with Y coordinates shifted downwards
        by `y_offset`.
    """
    if abs(y_offset) < 1e-9:
        return placed_nodes
    restored = copy.deepcopy(placed_nodes)
    for n in restored:
        geo = n.get("geometry", {})
        if "y" in geo:
            geo["y"] = round(geo["y"] - y_offset, 6)
    return restored

# ---------------------------------------------------------------------------
# Abutment candidate formatter
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Graph Compression for AI Prompts (reduce token count by 95%+)
# ---------------------------------------------------------------------------
def compress_graph_for_prompt(graph_data: dict) -> dict:
    """
    Compress graph JSON for AI prompt to drastically reduce token count.
    
    Problems solved:
    - Collapses finger/multiplier instances into parent devices
    - Removes pre-computed geometry (AI's job to compute it)
    - Compresses terminal_nets (one per parent, not per finger)
    - Uses net-centric connectivity instead of verbose edge lists
    
    Expected reduction: 95-97% smaller (e.g., 7300 lines -> 150-200 lines).

    Parameters
    ----------
    graph_data : dict
        The full verbose JSON dictionary exported directly from the GUI.

    Returns
    -------
    dict
        A highly compressed dictionary containing exactly what the LLM needs:
        devices, signal nets, matching constraints, and blocks.
    """
    if not graph_data:
        return {}
    
    compressed = {
        "devices": {},
        "nets": {},
        "matching_constraints": graph_data.get("matching_constraints", {}),
        "blocks": graph_data.get("blocks", {})
    }
    
    # 1. Collapse finger/multiplier instances into parent devices
    terminal_nets = graph_data.get("terminal_nets", {})
    for node in graph_data.get("nodes", []):
        # Get parent device ID (strip _mN, _fN suffixes)
        node_id = node["id"]
        parent_id = node["electrical"].get("parent")
        
        # If no parent field, extract from node_id pattern
        if not parent_id:
            # Strip _mN or _fN suffixes to get base device
            parent_id = re.sub(r'_[mf]\d+$', '', node_id)
        
        # Skip if we already processed this parent
        if parent_id in compressed["devices"]:
            continue
        
        # Extract electrical parameters from this node
        electrical = node.get("electrical", {})
        dev_type = node.get("type", "nmos")
        
        # Get terminal nets from the first instance (all fingers share same nets)
        dev_terminal_nets = terminal_nets.get(node_id, {})
        
        # Build compressed device entry
        compressed["devices"][parent_id] = {
            "type": dev_type,
            "m": electrical.get("m", 1),
            "nf": electrical.get("nf", 1),
            "nfin": electrical.get("nfin", 1),
            "l": electrical.get("l", 0.0),
            "terminal_nets": dev_terminal_nets
        }
        
        # Add block membership if present
        block_info = node.get("block")
        if block_info:
            compressed["devices"][parent_id]["block"] = block_info
    
    # 2. Build net-centric connectivity (replace verbose edge list)
    edges = graph_data.get("edges", [])
    for edge in edges:
        net = edge.get("net", "")
        if not net:
            continue
        
        # Skip power nets (too verbose, not useful for placement)
        if net.upper() in {"VDD", "VSS", "GND", "VCC", "AVDD", "AVSS"}:
            continue
        
        source = edge.get("source", "")
        target = edge.get("target", "")
        
        # Convert finger instances to parent devices
        source_parent = re.sub(r'_[mf]\d+$', '', source)
        target_parent = re.sub(r'_[mf]\d+$', '', target)
        
        if net not in compressed["nets"]:
            compressed["nets"][net] = set()
        
        compressed["nets"][net].add(source_parent)
        compressed["nets"][net].add(target_parent)
    
    # Convert sets to sorted lists for JSON serialization
    for net in compressed["nets"]:
        compressed["nets"][net] = sorted(compressed["nets"][net])
    
    return compressed


def _format_abutment_candidates(candidates: list) -> str:
    """
    Format the abutment candidate list into a human-readable prompt section.

    Abutment candidates represent devices that should share a common
    Source/Drain diffusion area to minimize overall footprint.

    Parameters
    ----------
    candidates : list
        List of candidate dictionaries indicating which devices should abut.

    Returns
    -------
    str
        A multi-line formatted string enumerating all valid abutment chains.
    """
    if not candidates:
        return ""
    lines = []
    for c in candidates:
        flip_note = " (Note: set orientation='R0_FH' for device B)" if c.get("needs_flip") else ""
        lines.append(
            f"  - ABUTMENT CHAIN: {c['dev_a']} (Right Side) <---> (Left Side) {c['dev_b']}. Net: '{c['shared_net']}'.{flip_note}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Post-placement Healing — chain-based topological clustering
# ---------------------------------------------------------------------------
def _build_abutment_chains(nodes: list, candidates: list) -> list[list[str]]:
    """
    Extract connected components of abutment pairs as ordered sequences (chains).

    Using Union-Find with path compression, this reconstructs full multi-device
    abutment chains (e.g., A-B, B-C -> [A, B, C]) so the placement engine
    knows which macroscopic groups must be kept unconditionally contiguous.

    Parameters
    ----------
    nodes : list
        List of all node dictionaries in the graph.
    candidates : list
        List of dictionaries declaring `dev_a` and `dev_b` abutment constraints.

    Returns
    -------
    list[list[str]]
        A list of chains. Each chain is an ordered list of device ID strings.
    """
    node_ids = [n["id"] for n in nodes if "id" in n]
    id_set = set(node_ids)

    # Standard Union-Find with path compression
    parent: dict[str, str] = {nid: nid for nid in id_set}

    def find(x: str) -> str:
        root = x
        while parent[root] != root:
            root = parent[root]
        # Path compression: point all traversed nodes directly to root
        while parent[x] != root:
            nxt = parent[x]
            parent[x] = root
            x = nxt
        return root

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Union from explicit candidates (primary source of truth)
    for c in candidates:
        a, b = c["dev_a"], c["dev_b"]
        if a in id_set and b in id_set:
            union(a, b)

    # CRITICAL FIX: ALSO union from embedded abutment flags
    # This ensures hierarchy siblings (MM0_f1, MM0_f2, etc.) expanded by
    # expand_groups are properly chained even if not in explicit candidates.
    # We ALWAYS check flags, regardless of whether candidates exist.
    from collections import defaultdict
    rows = defaultdict(list)
    for n in nodes:
        y = round(float(n.get("geometry", {}).get("y", 0.0)), 3)
        rows[y].append(n)

    for y_val, row_nodes in rows.items():
        sorted_row = sorted(row_nodes, key=lambda n: n.get("geometry", {}).get("x", 0.0))
        for i in range(len(sorted_row) - 1):
            n1 = sorted_row[i]
            n2 = sorted_row[i + 1]
            # Check if BOTH devices have matching abutment flags
            if (n1.get("abutment", {}).get("abut_right")
                    and n2.get("abutment", {}).get("abut_left")):
                a, b = n1["id"], n2["id"]
                if a in id_set and b in id_set:
                    union(a, b)

    # Group by component root
    groups: dict[str, list[str]] = {}
    for nid in id_set:
        root = find(nid)
        groups.setdefault(root, []).append(nid)

    # Build ordered chains — sort by finger index if present, else by ID
    def _finger_key(nid: str) -> tuple:
        parts = nid.rsplit("_f", 1)
        if len(parts) == 2:
            try:
                return (parts[0], int(parts[1]))
            except ValueError:
                pass
        return (nid, 0)

    chains = []
    for group in groups.values():
        if len(group) <= 1:
            continue  # single node — not a chain
        ordered = sorted(group, key=_finger_key)
        chains.append(ordered)

    return chains



def _heal_abutment_positions(nodes: list, candidates: list,
                              no_abutment: bool = False) -> list:
    """
    Robust post-placement geometry reconstruction with chain-based topological clustering.

    This function overrides the model's raw AI coordinate output by forcing
    strict determinism on abutted chains and passive rows.

    Algorithm (per row):
    0. FIRST: Force all passive devices (res/cap) to a dedicated row at Y=1.630,
       packed left-to-right by their actual widths. This prevents overlap with transistors.
    1. Build abutment chains (connected components of abutted device pairs).
    2. For each row, group devices by their chain membership.
    3. Force-pack each chain into consecutive slots separated by
       ABUT_SPACING (0.070 µm), anchored at the chain leader's X.
    4. Separate different chains / standalone devices by device width.
    5. The result is guaranteed to pass _validate_placement even if the
       LLM outputs completely wrong X values inside a chain.

    Parameters
    ----------
    nodes : list
        List of node dictionaries containing the raw AI-predicted geometries.
    candidates : list
        List of abutment candidate dictionaries dictating absolute connectivity limits.
    no_abutment : bool, optional
        If True, skips ALL abutment chain logic. Packs every transistor at a standard
        device-width spacing and aggressively clears all abutment flags. Defaults to False.

    Returns
    -------
    list
        Mutated node dictionary list with perfectly snapped geometrical coordinates.
    """
    ABUT_SPACING = 0.070   # µm between abutted device origins
    PITCH        = 0.294   # µm between non-abutted device origins
    PASSIVE_Y    = 1.630   # dedicated passive row Y coordinate

    if not nodes:
        return nodes

    # ── Step 0: Enforce passive device row ──────────────────────────────
    # Collect passives, force them into their own row, pack by width
    passives = [n for n in nodes if n.get("type") in ("res", "cap")]
    if passives:
        # Sort passives by their current X to maintain relative order
        passives.sort(key=lambda n: n.get("geometry", {}).get("x", 0.0))
        cursor = 0.0
        for p in passives:
            geo = p.setdefault("geometry", {})
            geo["x"] = round(cursor, 6)
            geo["y"] = PASSIVE_Y
            p_width = geo.get("width", PITCH)
            cursor = round(cursor + p_width, 6)

    # ── No-abutment mode: simple left-to-right packing per row ──────────
    if no_abutment:
        passive_ids = {p["id"] for p in passives} if passives else set()
        row_buckets: dict[float, list] = defaultdict(list)
        for n in nodes:
            if n.get("id") in passive_ids:
                continue
            y = round(float(n.get("geometry", {}).get("y", 0.0)), 3)
            row_buckets[y].append(n)

        for y_key, row_nodes in row_buckets.items():
            row_sorted = sorted(row_nodes,
                                key=lambda n: n.get("geometry", {}).get("x", 0.0))
            if not row_sorted:
                continue
            cursor = row_sorted[0].get("geometry", {}).get("x", 0.0)
            for dev in row_sorted:
                geo = dev.setdefault("geometry", {})
                geo["x"] = round(cursor, 6)
                geo["y"] = round(float(y_key), 6)
                # Clear ALL abutment flags
                dev["abutment"] = {"abut_left": False, "abut_right": False}
                dev_w = geo.get("width", PITCH)
                cursor = round(cursor + dev_w, 6)
        return nodes

    # ── Normal abutment mode below ──────────────────────────────────────

    # 1. Identify chains across ALL nodes (not per-row)
    chains = _build_abutment_chains(nodes, candidates)
    chain_of: dict[str, list[str]] = {}  # device_id -> its ordered chain
    for ch in chains:
        for nid in ch:
            chain_of[nid] = ch

    # Also mark abutment flags from candidates
    abut_right_set: set[str] = set()
    abut_left_set:  set[str] = set()
    for c in candidates:
        abut_right_set.add(c["dev_a"])
        abut_left_set.add(c["dev_b"])
    # Supplement from embedded flags (when candidates list is empty)
    for n in nodes:
        abut = n.get("abutment", {})
        if abut.get("abut_right"):
            abut_right_set.add(n["id"])
        if abut.get("abut_left"):
            abut_left_set.add(n["id"])

    node_map: dict[str, dict] = {n["id"]: n for n in nodes if "id" in n}

    # 2. Group nodes by row (Y rounded to 3 dp) — skip passives (already placed)
    passive_ids = {p["id"] for p in passives} if passives else set()
    row_buckets: dict[float, list] = defaultdict(list)
    for n in nodes:
        if n.get("id") in passive_ids:
            continue  # passives already healed in Step 0
        y = round(float(n.get("geometry", {}).get("y", 0.0)), 3)
        row_buckets[y].append(n)

    for y_key, row_nodes in row_buckets.items():
        # 3. Build "segments":  each segment is either a chain or a singleton.
        #    We materialise chains in the order of their lowest-X device.
        processed: set[str] = set()
        segments: list[list[dict]] = []  # list of ordered device-lists

        # Sort row devices by current X for stable initial ordering
        row_sorted = sorted(row_nodes,
                            key=lambda n: n.get("geometry", {}).get("x", 0.0))

        for n in row_sorted:
            nid = n["id"]
            if nid in processed:
                continue
            if nid in chain_of:
                # Collect the full chain in finger-index order,
                # restricted to devices actually in THIS row.
                row_ids = {rn["id"] for rn in row_nodes}
                chain_in_row = [cid for cid in chain_of[nid]
                                if cid in row_ids and cid not in processed]
                if chain_in_row:
                    segments.append([node_map[cid] for cid in chain_in_row
                                     if cid in node_map])
                    processed.update(chain_in_row)
            else:
                segments.append([n])
                processed.add(nid)

        # 4. Pack segments left-to-right, anchoring at the first segment's X.
        if not segments:
            continue

        # Use the leftmost X in the first segment's devices as the cursor start
        first_dev_x = min(
            d.get("geometry", {}).get("x", 0.0) for d in segments[0]
        )
        cursor = first_dev_x

        for seg_idx, segment in enumerate(segments):
            for dev_idx, dev in enumerate(segment):
                geo = dev.setdefault("geometry", {})
                geo["x"] = round(cursor, 6)
                # Force exact Y-alignment: every device in this row
                # must share the identical Y coordinate
                geo["y"] = round(float(y_key), 6)

                is_last_in_chain = (dev_idx == len(segment) - 1)

                if not is_last_in_chain:
                    # Next device is within the chain — abut spacing
                    cursor = round(cursor + ABUT_SPACING, 6)
                    # Enforce abutment flags for adjacent pair
                    next_dev = segment[dev_idx + 1]
                    dev.setdefault("abutment", {})["abut_right"] = True
                    next_dev.setdefault("abutment", {})["abut_left"] = True
                else:
                    # End of this chain/singleton — advance by next device width
                    dev_w = geo.get("width", PITCH)
                    cursor = round(cursor + dev_w, 6)

        # 5. Clean abutment flags for standalone devices
        for seg in segments:
            if len(seg) == 1:
                dev = seg[0]
                abut = dev.get("abutment", {})
                # Only preserve cross-pair flags; clear both for singletons
                dev["abutment"] = {
                    "abut_left":  abut.get("abut_left",  False),
                    "abut_right": abut.get("abut_right", False),
                }

    return nodes


def _force_abutment_spacing(nodes: list, candidates: list = None) -> list:
    """
    FAILSAFE: Force logically-correct abutment spacing across adjacent geometries.

    A final protection layer running after `_heal_abutment_positions` or SA.
    It scans the row array, looks for devices natively declaring structural
    abutment (`abut_right` interacting with `abut_left`), and rigorously forces 
    their physical delta-X to be exactly 0.070 µm.

    Parameters
    ----------
    nodes : list
        List of geometrically assigned node dictionaries.
    candidates : list, optional
        Fallback reference candidate list (unused natively inside the loop
        but kept for API compatibility).

    Returns
    -------
    list
        The safety-corrected mutated node dictionaries.
    """
    from collections import defaultdict
    
    ABUT_SPACING = 0.070
    PITCH = 0.294
    
    # Group by row
    row_buckets = defaultdict(list)
    for n in nodes:
        y = round(float(n.get("geometry", {}).get("y", 0.0)), 3)
        row_buckets[y].append(n)
    
    fixed_count = 0
    
    for y_key, row_nodes in row_buckets.items():
        # Sort by X
        row_sorted = sorted(row_nodes, key=lambda n: n.get("geometry", {}).get("x", 0.0))
        
        # Find all devices with abutment flags
        for i in range(len(row_sorted) - 1):
            n1 = row_sorted[i]
            n2 = row_sorted[i + 1]
            
            abut1 = n1.get("abutment", {})
            abut2 = n2.get("abutment", {})
            
            # If n1 has abut_right and n2 has abut_left, they MUST be spaced at 0.070
            if abut1.get("abut_right") and abut2.get("abut_left"):
                x1 = n1.get("geometry", {}).get("x", 0.0)
                x2 = n2.get("geometry", {}).get("x", 0.0)
                expected_x2 = round(x1 + ABUT_SPACING, 6)
                
                if abs(x2 - expected_x2) > 0.001:
                    print(f"[FORCE_FIX] Moving {n2['id']} from x={x2:.4f} to x={expected_x2:.4f} "
                          f"(was {abs(x2 - x1):.4f}, should be {ABUT_SPACING:.3f})")
                    n2.setdefault("geometry", {})["x"] = expected_x2
                    fixed_count += 1
    
    if fixed_count > 0:
        print(f"[FORCE_FIX] Fixed {fixed_count} device position(s)")
    
    return nodes


# ---------------------------------------------------------------------------
# Core Structured Prompt
# ---------------------------------------------------------------------------
def generate_vlsi_prompt(prompt_graph: dict, inventory_str: str, adjacency_str: str, block_str: str,
                         abutment_str: str = "",
                         row_summary: str = "",
                         matching_section: str = "") -> str:
    """
    Construct the legacy VLSI floating-point geometry prompt template.

    Orchestrates disparate string sub-sections (inventory, adjacency, matches)
    into a monolithic text prompt demanding spatial constraints (`x` and `y` float
    responses). Generally utilized by legacy local models that lack structured
    JSON-object schema constraint capabilities.

    Parameters
    ----------
    prompt_graph : dict
        A heavily compressed representation of the graph topology.
    inventory_str : str
        Human-readable summary of `pmos` and `nmos` arrays (`_build_device_inventory`).
    adjacency_str : str
        Nets and connection groupings (`_build_net_adjacency`).
    block_str : str
        Physical hierarchy block separation (`_build_block_info`).
    abutment_str : str, optional
        Mandatory topological abutments (`_format_abutment_candidates`).
    row_summary : str, optional
        Pre-computed row geometry blocks.
    matching_section : str, optional
        Symmetry matching configurations.

    Returns
    -------
    str
        The fully assembled natural-language placement prompt text.
    """

    abut_section = ""
    if abutment_str and abutment_str.strip() not in ("", "None detected."):
        abut_section = f"""
9. Transistor Abutment (Diffusion Sharing — HIGHEST PRIORITY):
The following transistors share a Source/Drain net and MUST be abutted to save area.

REQUIRED ABUTMENT CHAINS:
{abutment_str}

ABUTMENT MATHEMATICAL RULES:
- If Device A abuts Device B on the right: Origin X_b = X_a + 0.070 um.
- Example: MM28 at x=0.000, then MM5 MUST be at x=0.070, then MM4 at x=0.140.
- For EVERY abutted pair (A, B): 
    * Device A MUST have {{"abutment": {{"abut_right": true, "abut_left": false}}}}
    * Device B MUST have {{"abutment": {{"abut_left": true, "abut_right": false}}}}
- NEVER place two different devices at the exact same X and Y coordinates (delta X must be > 0).

OUTPUT EXAMPLE FOR ABUTTED PAIR:
{{
  "id": "DEV_A",
  "geometry": {{ "x": 0.000, "y": 0.000, "orientation": "R0" }},
  "abutment": {{ "abut_left": false, "abut_right": true }}
}},
{{
  "id": "DEV_B",
  "geometry": {{ "x": 0.070, "y": 0.000, "orientation": "R0" }},
  "abutment": {{ "abut_left": true, "abut_right": false }}
}}
"""

    # --- Row assignment section ---
    row_assignment_section = ""
    if row_summary:
        row_assignment_section = f"""
ROW ASSIGNMENT (pre-computed — MANDATORY Y values):
{row_summary}

CRITICAL: You MUST use the Y values from this table. Do NOT invent new Y values.
Multiple PMOS rows and NMOS rows are used for rectangular form-factor.
"""

    # --- Matching / symmetry section ---
    matching_prompt_section = ""
    if matching_section:
        matching_prompt_section = f"""
8. Transistor Matching & Symmetry Constraints:
{matching_section}
"""

    return f"""You are a world-class VLSI analog placement engineer with deep expertise in
FinFET analog layout, parasitic-aware placement, and high-performance analog
circuit design (op-amps, comparators, current mirrors, data converters).

YOUR OBJECTIVE: Generate an optimal initial placement that:
  (a) Minimises total estimated wire length (HPWL)
  (b) Preserves symmetry for all matched / differential device pairs
  (c) Maximises diffusion sharing (abutment) to reduce parasitic capacitance
  (d) Produces a compact, near-rectangular bounding box
  (e) Satisfies ALL DRC constraints listed below

REASONING STEPS (think through these before writing coordinates):
  1. Study the ROW ASSIGNMENT table — each device has a FIXED Y coordinate.
  2. Study the NET ADJACENCY list — devices sharing critical nets should be adjacent.
  3. Identify matched blocks marked [FIXED MATCHED BLOCK] — these are pre-interdigitated
     and MUST be placed as single units. Just assign their origin X.
  4. Place the most-connected device first, then place neighbors to minimise wire length.
  5. Within each row, order devices to minimise the total number of net crossings.
  6. Verify no overlaps: next device X >= previous device X + previous device width.

=== INPUT DATA ===

DEVICE INVENTORY:
{inventory_str}

NET ADJACENCY (devices sharing each net — place them near each other):
{adjacency_str}

BLOCK GROUPING:
{block_str}
{row_assignment_section}
=== DRC & FLOORPLANNING RULES ===

1. Row Structure & Y-Axis Placement:
   - Row pitch = 0.668 um.  NMOS rows: y = 0, 0.668, 1.336, ...
   - PMOS rows sit ABOVE all NMOS rows.
   - CRITICAL: No row may contain both NMOS and PMOS.
   - Use the ROW ASSIGNMENT table above for the exact Y value of each device.
   - If no table is provided: NMOS y=0.000, PMOS y=0.668.

2. Fin Grid Quantisation:
   - ALL coordinates must snap to finPitch = 0.014 um multiples.
   - Fractional or arbitrary coordinates are FORBIDDEN.

3. Spacing & Overlap:
   - Non-abutted devices: next origin X >= prev origin X + prev width (0.294 um standard).
   - Abutted devices: next origin X = prev origin X + 0.070 um.
   - Vertical overlap between devices is FORBIDDEN.

4. Voltage Domain Isolation:
   - Different voltage domains (0.8 V, 1.5 V, 1.8 V) must be physically isolated.

5. Multi-Finger Devices:
   - MM1_f1, MM1_f2, ... are FINGERS of the same transistor MM1.
   - ALL fingers MUST be consecutive in the same row, in ascending order.
   - Adjacent same-net-sharing fingers: abutted at 0.070 um pitch.
   - Otherwise: standard 0.294 um spacing.

6. Block Grouping:
   - Same-block devices MUST be contiguous. No interleaving across blocks.

7. Passive Devices:
   - type="res" or type="cap" → dedicated PASSIVE ROW above all transistor rows.

{matching_prompt_section}{abut_section}
=== OUTPUT FORMAT ===

Return ONLY a JSON object with a single key "nodes" containing the array of placed devices.
Do NOT return "edges", "blocks", or "terminal_nets".

For each device, set:
  - "x": origin X coordinate (fin-grid snapped)
  - "y": from ROW ASSIGNMENT table
  - "orientation": "R0" (default) or as needed
  - "abutment": {{"abut_left": bool, "abut_right": bool}} for abutted pairs

CRITICAL: Your response MUST be complete valid JSON. Do NOT truncate.
CRITICAL: Include EVERY device from the inventory — missing devices = failure.
CRITICAL: Matched blocks marked [FIXED] must be placed as single units.
CRITICAL: The footprint (width) of each group in the ROW ASSIGNMENT table is in real layout µm.
  - A group with footprint=2.352 µm placed at x=0 occupies X range [0, 2.352].
  - The next group must start at x >= 2.352 (+ gap if needed).
  - DO NOT pile all groups at x < 1.0 — this WILL cause overlaps.
  - Spread devices across the full width of the row.

Return ONLY raw JSON. No markdown, no explanation, no commentary.

COMPRESSED DEVICE GRAPH (parent-level summary):

{json.dumps(compress_graph_for_prompt(prompt_graph), indent=2)}
"""


# ---------------------------------------------------------------------------
# Slot-Based Prompt (for structured-output models like Gemini / Claude)
# ---------------------------------------------------------------------------
def generate_vlsi_slot_prompt(original_nodes: list,
                              edges: list,
                              graph_data: dict,
                              abutment_str: str = "") -> str:
    """
    Construct the modern Slot-Based JSON sequence prompt string.

    Rather than allowing floating-point calculation hallucinations, this prompt
    strictly demands only deterministic sequence assignments (left-to-right
    transistor order). Coordinates are mathmatically inferred client-side later
    by `_convert_slots_to_geometry`.

    Parameters
    ----------
    original_nodes : list
        Source-of-truth JSON architecture for devices.
    edges : list
        Raw edge adjacency array to reconstruct the topologies inline.
    graph_data : dict
        Fully exported graph containing hierarchical definitions.
    abutment_str : str, optional
        Mandatory topological abutments (`_format_abutment_candidates`).

    Returns
    -------
    str
        The fully assembled natural-language placement prompt, expecting
        a JSON object strictly containing `{ "nmos_order": [], "pmos_order": [] }`.
    """
    # Separate devices by type
    pmos_ids = sorted(n["id"] for n in original_nodes if n.get("type") == "pmos")
    nmos_ids = sorted(n["id"] for n in original_nodes if n.get("type") == "nmos")

    # Build net-adjacency for context
    adjacency_str = _build_net_adjacency(original_nodes, edges)
    block_str = _build_block_info(original_nodes, graph_data)

    # Abutment chain info
    abut_chains_section = ""
    if abutment_str and abutment_str.strip():
        abut_chains_section = f"""
ABUTMENT CHAINS (devices that MUST appear consecutively in the ordering):
{abutment_str}
"""

    return f"""You are a VLSI analog placement engineer.

YOUR TASK: Determine the optimal LEFT-TO-RIGHT ordering of transistors in each row.
You do NOT calculate coordinates — just decide the best sequence.

DEVICES TO PLACE:
  NMOS row: {', '.join(nmos_ids)}
  PMOS row: {', '.join(pmos_ids)}

SIGNAL NET CONNECTIVITY (devices sharing a net should be near each other):
{adjacency_str}

BLOCK GROUPING (same-block devices must be contiguous):
{block_str}
{abut_chains_section}
RULES:
1. Place devices that share signal nets ADJACENT to each other to minimise wire length.
2. Keep same-block devices CONTIGUOUS — never interleave blocks.
3. Multi-finger devices (e.g. MM0_m1, MM0_m2, ...) must appear consecutively in order.
4. Abutment chain devices MUST appear consecutively in the exact chain order.
5. Optimise for minimum total wire length (HPWL).

OUTPUT FORMAT (strict JSON):
Return ONLY a JSON object with exactly two keys:
- "nmos_order": array of ALL {len(nmos_ids)} NMOS device IDs in left-to-right order
- "pmos_order": array of ALL {len(pmos_ids)} PMOS device IDs in left-to-right order

EXAMPLE:
{{
  "nmos_order": ["MM0_m1", "MM0_m2", "MM1_m1"],
  "pmos_order": ["MM3_m1", "MM4_m1"]
}}

CRITICAL: Include EVERY device ID exactly once. Missing or duplicate IDs = failure.
Return ONLY raw JSON. No markdown, no explanation.
"""


def _convert_slots_to_geometry(slot_data: dict,
                               original_nodes: list,
                               abutment_candidates: list | None = None) -> list:
    """Convert AI slot-based output to exact geometry coordinates.

    Parameters
    ----------
    slot_data           : dict with ``nmos_order`` and ``pmos_order`` keys,
                          each a list of device IDs in left-to-right order.
    original_nodes      : list of original node dicts with full metadata.
    abutment_candidates : list of ``{dev_a, dev_b, ...}`` dicts.

    Returns
    -------
    list — fully placed node dicts with correct geometry.
    """
    ABUT_SPACING = 0.070
    STANDARD_PITCH = 0.294
    ROW_Y = {"nmos": 0.000, "pmos": 0.668}

    # Build lookup from original nodes
    node_map: dict[str, dict] = {
        n["id"]: n for n in original_nodes
        if isinstance(n, dict) and "id" in n
    }

    # Build abutment pair lookup: (dev_a, dev_b) means A abuts right → B
    abut_pairs: set[tuple[str, str]] = set()
    if abutment_candidates:
        for c in abutment_candidates:
            abut_pairs.add((c["dev_a"], c["dev_b"]))
    # Also check embedded flags from original nodes
    for n in original_nodes:
        abut = n.get("abutment", {})
        nid = n.get("id", "")
        if abut.get("abut_right"):
            # Find the device that has abut_left and is next in some chain
            for m in original_nodes:
                if (m.get("abutment", {}).get("abut_left")
                        and m.get("id", "") != nid):
                    abut_pairs.add((nid, m["id"]))

    placed_nodes: list[dict] = []

    for row_key, dev_type in [("nmos_order", "nmos"), ("pmos_order", "pmos")]:
        device_ids = slot_data.get(row_key, [])
        if not device_ids:
            continue

        row_y = ROW_Y.get(dev_type, 0.0)
        cursor = 0.0

        for i, dev_id in enumerate(device_ids):
            if dev_id not in node_map:
                print(f"[slot→geo] WARNING: '{dev_id}' not found in node_map, skipping")
                continue

            orig = copy.deepcopy(node_map[dev_id])
            geo = orig.setdefault("geometry", {})
            geo["x"] = round(cursor, 6)
            geo["y"] = row_y
            geo.setdefault("orientation", "R0")

            # Determine spacing to next device
            if i < len(device_ids) - 1:
                next_id = device_ids[i + 1]
                is_abutted = (dev_id, next_id) in abut_pairs

                if is_abutted:
                    orig.setdefault("abutment", {})["abut_right"] = True
                    cursor = round(cursor + ABUT_SPACING, 6)
                else:
                    dev_w = geo.get("width", STANDARD_PITCH)
                    orig.setdefault("abutment", {})["abut_right"] = False
                    cursor = round(cursor + dev_w, 6)

            # Set abut_left flag based on previous device
            if i > 0:
                prev_id = device_ids[i - 1]
                if (prev_id, dev_id) in abut_pairs:
                    orig.setdefault("abutment", {})["abut_left"] = True
                else:
                    orig.setdefault("abutment", {})["abut_left"] = False
            else:
                orig.setdefault("abutment", {})["abut_left"] = False

            placed_nodes.append(orig)

    # Handle passive devices (res, cap) — just keep original positions
    for n in original_nodes:
        if n.get("type") in ("res", "cap"):
            placed_nodes.append(copy.deepcopy(n))

    return placed_nodes

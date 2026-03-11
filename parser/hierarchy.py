"""
Hierarchy flattener for SPICE/CDL netlists.

Handles multi-level subcircuit hierarchies by:
1. Extracting all subcircuit definitions
2. Identifying the top-level subcircuit (last defined, or by filename)
3. Recursively expanding X-instances until only leaf devices (M, R, C) remain
"""

import os


class Subckt:
    def __init__(self, name, ports, lines):
        self.name = name
        self.ports = ports      # ordered port list
        self.lines = lines      # internal device lines


def extract_subckts(lines):
    """Extract all .subckt definitions from raw netlist lines."""
    subckts = {}
    current = None
    body = []
    ports = []

    for line in lines:
        tokens = line.split()
        if not tokens:
            continue

        if tokens[0].upper() == ".SUBCKT":
            name = tokens[1]
            ports = tokens[2:]
            current = name
            body = []
            continue

        if tokens[0].upper() == ".ENDS":
            if current:
                subckts[current] = Subckt(current, ports, body)
            current = None
            continue

        if current:
            body.append(line)

    return subckts


def expand_instance(line, subckts, prefix=""):
    """Expand a single X-instance line into its constituent device lines.

    Recursively expands if the subcircuit contains further X-instances.
    """
    tokens = line.split()

    inst_name = tokens[0]           # e.g. XI0
    nets = tokens[1:-1]             # actual net connections
    subckt_name = tokens[-1]        # subcircuit being instantiated

    if subckt_name not in subckts:
        # Unknown subcircuit — skip with warning
        print(f"[Hierarchy] Warning: subcircuit '{subckt_name}' not found, "
              f"skipping instance '{inst_name}'")
        return []

    subckt = subckts[subckt_name]

    # Map subckt ports -> actual nets
    port_map = dict(zip(subckt.ports, nets))

    # Build hierarchical prefix
    full_prefix = f"{prefix}{inst_name}_" if prefix else f"{inst_name}_"

    expanded = []

    for internal_line in subckt.lines:
        parts = internal_line.split()
        if not parts:
            continue

        devname = parts[0]

        # Skip comments and directives
        if devname.startswith("*") or devname.startswith("."):
            continue

        # Check if this is another X-instance (recursive hierarchy)
        if devname[0].upper() == "X":
            # Rename the instance with prefix and remap nets
            remapped_parts = [f"{full_prefix}{devname}"]
            for i in range(1, len(parts)):
                token = parts[i]
                if token in port_map:
                    remapped_parts.append(port_map[token])
                else:
                    # Internal net — prefix to avoid name collisions
                    remapped_parts.append(f"{full_prefix}{token}")
            remapped_line = " ".join(remapped_parts)
            # Recurse
            expanded.extend(expand_instance(remapped_line, subckts, prefix=""))
        else:
            # Leaf device (M, R, C, etc.) — rename and remap nets
            parts[0] = f"{full_prefix}{devname}"
            for i in range(1, len(parts)):
                token = parts[i]
                if token in port_map:
                    parts[i] = port_map[token]
                elif "=" not in token:
                    # Internal net — prefix to avoid collisions
                    # But don't prefix model names or key=value params
                    # Heuristic: if token position is 1..4 for MOS (D,G,S,B nets)
                    # or if the token doesn't look like a param
                    if i <= 4 or (i == 5 and not token[0].isalpha()):
                        parts[i] = f"{full_prefix}{token}"

            expanded.append(" ".join(parts))

    return expanded


def _find_top_subckt(subckts, filename=None):
    """Identify the top-level subcircuit.

    Strategy:
    1. If filename is given, try to match by basename (without extension)
    2. Otherwise, find the subcircuit that is not instantiated by any other
    3. Fallback: the last subcircuit defined
    """
    if filename:
        basename = os.path.splitext(os.path.basename(filename))[0]
        # Try exact match
        if basename in subckts:
            return basename
        # Try case-insensitive match
        for name in subckts:
            if name.lower() == basename.lower():
                return name

    # Find the subcircuit that is not used as a child by any other
    all_names = set(subckts.keys())
    used_as_child = set()
    for sub in subckts.values():
        for line in sub.lines:
            tokens = line.split()
            if tokens and tokens[0][0].upper() == "X":
                child_name = tokens[-1]
                used_as_child.add(child_name)

    top_candidates = all_names - used_as_child
    if len(top_candidates) == 1:
        return top_candidates.pop()

    # Fallback: last defined (Python 3.7+ dicts preserve insertion order)
    return list(subckts.keys())[-1]


def flatten_netlist(filename):
    """Flatten a hierarchical SPICE/CDL netlist.

    Returns a list of lines containing only leaf device statements
    (M, R, C, etc.) with hierarchical name prefixes.
    """

    with open(filename) as f:
        raw_lines = [l.strip() for l in f if l.strip()]

    subckts = extract_subckts(raw_lines)

    # If no subcircuits at all, return raw lines
    if not subckts:
        return raw_lines

    # Find the top-level subcircuit
    top_name = _find_top_subckt(subckts, filename)
    top = subckts[top_name]

    # Collect non-subcircuit preamble lines (comments, .GLOBAL, etc.)
    preamble = []
    inside_subckt = False
    for line in raw_lines:
        tokens = line.split()
        if not tokens:
            continue
        kw = tokens[0].upper()
        if kw == ".SUBCKT":
            inside_subckt = True
            continue
        if kw == ".ENDS":
            inside_subckt = False
            continue
        if not inside_subckt:
            preamble.append(line)

    flat_lines = list(preamble)

    # Process each line in the top-level subcircuit
    for line in top.lines:
        tokens = line.split()
        if not tokens:
            continue

        devname = tokens[0]

        # Skip comments and directives
        if devname.startswith("*") or devname.startswith("."):
            continue

        if devname[0].upper() == "X":
            # Expand X-instance recursively
            flat_lines.extend(expand_instance(line, subckts))
        else:
            # Leaf device at top level — keep as-is
            flat_lines.append(line)

    return flat_lines
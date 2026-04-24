"""
netlist_reader.py
Merged pipeline stage combining models, hierarchy, and netlist parsing.
"""
"""
models.py
Core data structures for the parsing module. Includes the `Device` and `Netlist`
classes representing circuit components and connectivity, as well as utility functions
for parsing SPICE numerical values.
"""


class Device:
    """
    Represents one circuit device (transistor, resistor, capacitor...)

    Hierarchy metadata attributes (set when array/multiplier/finger detected):
        array_size  : int  — array suffix <N> from device name (1 if none)
        multiplier  : int  — m=N parameter value (1 if none)
        fingers     : int  — nf=N parameter value (1 if none)
    These are stored in self.params but also exposed as convenience
    properties for code that needs quick access without digging into params.
    """

    def __init__(self, name, dtype, pins, params):
        self.name = name            # instance name (M1, R3...)
        self.type = dtype.lower()   # nmos / pmos / cap / res
        self.pins = pins            # dict: {pin_name : net}
        self.params = params        # dict: parameters

    def __repr__(self):
        return f"<Device {self.name} type={self.type}>"

    # -- Convenience properties for hierarchy metadata ------------------------
    @property
    def array_size(self) -> int:
        """Array suffix <N> from device name; 1 if none."""
        return self.params.get('array_size', 1)

    @property
    def multiplier(self) -> int:
        """Multiplier m=N; 1 if not specified."""
        return self.params.get('m', 1)

    @property
    def fingers(self) -> int:
        """Finger count nf=N; 1 if not specified."""
        return self.params.get('nf', 1)


class Netlist:
    """
    Holds devices and connectivity
    """

    def __init__(self):
        self.devices = {}   # name -> Device
        self.nets = {}      # net -> list[(device,pin)]

    def add_device(self, device):
        self.devices[device.name] = device

    def build_connectivity(self):
        """
        Build net -> device pin mapping
        """
        for dev in self.devices.values():
            for pin, net in dev.pins.items():
                if net not in self.nets:
                    self.nets[net] = []
                self.nets[net].append((dev.name, pin))


def parse_value(value: str) -> float:
    """
    Convert SPICE values like:
    1u, 60n, 2p, 10k, 1meg -> float
    """

    value = value.strip().lower()

    scale = {
        'f': 1e-15,
        'p': 1e-12,
        'n': 1e-9,
        'u': 1e-6,
        'm': 1e-3,
        'k': 1e3,
        'meg': 1e6,
        'g': 1e9
    }

    # try long suffix first (meg) to avoid 'm' matching inside 'meg'
    for suffix in sorted(scale.keys(), key=len, reverse=True):
        if value.endswith(suffix):
            number_part = value[:-len(suffix)]
            # Validate that the remaining part is a valid number
            if not number_part:
                # The entire string IS the suffix (e.g. just "meg")
                # which is not a valid SPICE value on its own
                break
            try:
                return float(number_part) * scale[suffix]
            except ValueError:
                # Suffix matched but prefix isn't numeric — keep trying
                continue

    # pure number
    return float(value)

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


# -----------------------------------------------------------------
# Block-aware flattening
# -----------------------------------------------------------------

def expand_instance_with_blocks(line, subckts, prefix="",
                                 top_instance="", top_subckt=""):
    """Expand a single X-instance and track block membership.

    Returns:
        expanded: list of flat device line strings
        block_entries: list of (device_name, instance, subckt_type) tuples
    """
    tokens = line.split()
    inst_name = tokens[0]
    nets = tokens[1:-1]
    subckt_name = tokens[-1]

    if subckt_name not in subckts:
        print(f"[Hierarchy] Warning: subcircuit '{subckt_name}' not found, "
              f"skipping instance '{inst_name}'")
        return [], []

    subckt = subckts[subckt_name]
    port_map = dict(zip(subckt.ports, nets))
    full_prefix = f"{prefix}{inst_name}_" if prefix else f"{inst_name}_"

    # Track which top-level instance this belongs to
    # If we're expanding a top-level X-instance, record it as the block root
    current_instance = top_instance or inst_name
    current_subckt = top_subckt or subckt_name

    expanded = []
    block_entries = []

    for internal_line in subckt.lines:
        parts = internal_line.split()
        if not parts:
            continue

        devname = parts[0]
        if devname.startswith("*") or devname.startswith("."):
            continue

        if devname[0].upper() == "X":
            remapped_parts = [f"{full_prefix}{devname}"]
            for i in range(1, len(parts)):
                token = parts[i]
                if token in port_map:
                    remapped_parts.append(port_map[token])
                else:
                    remapped_parts.append(f"{full_prefix}{token}")
            remapped_line = " ".join(remapped_parts)
            sub_expanded, sub_blocks = expand_instance_with_blocks(
                remapped_line, subckts, prefix="",
                top_instance=current_instance,
                top_subckt=current_subckt,
            )
            expanded.extend(sub_expanded)
            block_entries.extend(sub_blocks)
        else:
            parts[0] = f"{full_prefix}{devname}"
            for i in range(1, len(parts)):
                token = parts[i]
                if token in port_map:
                    parts[i] = port_map[token]
                elif "=" not in token:
                    if i <= 4 or (i == 5 and not token[0].isalpha()):
                        parts[i] = f"{full_prefix}{token}"

            device_name = parts[0]
            expanded.append(" ".join(parts))
            block_entries.append((device_name, current_instance, current_subckt))

    return expanded, block_entries


def flatten_netlist_with_blocks(filename):
    """Flatten a hierarchical SPICE/CDL netlist with block tracking.

    Returns:
        flat_lines: list of flattened device line strings
        block_map: {device_name: {"instance": "XI0", "subckt": "Inverter"}}
    """
    with open(filename) as f:
        raw_lines = [l.strip() for l in f if l.strip()]

    subckts = extract_subckts(raw_lines)

    if not subckts:
        return raw_lines, {}

    top_name = _find_top_subckt(subckts, filename)
    top = subckts[top_name]

    # Collect preamble
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
    block_map = {}

    for line in top.lines:
        tokens = line.split()
        if not tokens:
            continue

        devname = tokens[0]
        if devname.startswith("*") or devname.startswith("."):
            continue

        if devname[0].upper() == "X":
            expanded, block_entries = expand_instance_with_blocks(
                line, subckts
            )
            flat_lines.extend(expanded)
            for dev_name, instance, subckt_type in block_entries:
                block_map[dev_name] = {
                    "instance": instance,
                    "subckt": subckt_type,
                }
        else:
            flat_lines.append(line)

    return flat_lines, block_map

"""
netlist_reader.py
Entry point for reading SPICE/CDL text netlists.
Orchestrates flattening the netlist hierarchy, parsing leaf device parameters (like M, C, R limits),
and constructing the unified structural mappings into `Netlist` objects.
"""

import re
import logging


# Import hierarchy helpers for array suffix detection
from .hierarchy import parse_array_suffix


# -------------------------------------------------
# Device Parsers
# -------------------------------------------------

def parse_mos(tokens):
    """
    Generic MOS parser for CDL style:

    MM25 D G S B model param=value ...

    Naming convention for expanded devices:
      - Multiplier/array children:  {parent}_m{N}       (N is 1-based)
      - Finger children:             {parent}_f{N}       (N is 1-based)
      - Mixed:                       {parent}_m{M}_f{F}

    Array-indexed devices (e.g. MM9<7> from separate SPICE lines):
      - Each line is one array copy with nf=1, m=1 (typically)
      - Named as {base_name}_m{array_index+1}
      - Grouped under parent={base_name} during hierarchy reconstruction
      - Total array count determined by counting children during grouping

    Multiplier + finger expansion (e.g. m=3, nf=5 from a single line):
      - Expands to 15 devices: MM6_m1_f1 .. MM6_m1_f5, MM6_m2_f1 .. etc.
      - All share parent=MM6

    Error handling:
      - Non-integer m or nf values are rounded.
      - Missing closing angle bracket in array syntax returns None.
    """

    name = tokens[0]
    D, G, S, B = tokens[1:5]
    model = tokens[5].lower()

    # Determine device type
    if model.startswith("n"):
        dtype = "nmos"
    elif model.startswith("p"):
        dtype = "pmos"
    else:
        dtype = model

    params = {}
    nf = 1
    m = 1

    for t in tokens[6:]:
        if "=" in t:
            k, v = t.split("=")
            k = k.lower()

            try:
                val = parse_value(v)
            except:
                val = v

            params[k] = val

            if k == "nf":
                try:
                    nf = int(val)
                except (ValueError, TypeError):
                    nf = int(round(float(val)))

            if k == "m":
                try:
                    m = int(val)
                except (ValueError, TypeError):
                    m = int(round(float(val)))

    # --- Detect array suffix <N> (0-based index) --------------------------
    base_name, array_idx = parse_array_suffix(name)

    pins = {"D": D, "G": G, "S": S, "B": B}

    if array_idx is not None:
        # ── ARRAY-INDEXED DEVICE ──
        # This is one copy of an array (e.g., MM9<7> is the 8th copy of MM9).
        # Total array count is determined during grouping by counting children.
        # Each copy may also have its own m>1 or nf>1.

        total = nf * m
        devices = []
        for i in range(total):
            if m > 1:
                # Array copy + multiplier: combined flat m-level index
                # MM9<3> with m=2 → MM9_m7, MM9_m8
                combined_idx = array_idx * m + i + 1
                new_name = f"{base_name}_m{combined_idx}"
                mult_idx = combined_idx
                finger_idx = None
            elif nf > 1:
                # Array copy + fingers: m-level + f-level
                # MM9<3> with nf=5 → MM9_m4_f1 through MM9_m4_f5
                mult_idx = array_idx + 1
                finger_idx = i + 1
                new_name = f"{base_name}_m{mult_idx}_f{finger_idx}"
            else:
                # Array only
                mult_idx = array_idx + 1
                new_name = f"{base_name}_m{mult_idx}"
                finger_idx = None

            new_params = params.copy()
            new_params["nf"] = 1
            new_params["parent"] = base_name
            new_params["array_index"] = array_idx
            new_params["multiplier_index"] = mult_idx
            if finger_idx is not None:
                new_params["finger_index"] = finger_idx
            new_params["m"] = m
            new_params["orig_nf"] = nf  # preserve original nf for grouping

            devices.append(Device(new_name, dtype, pins, new_params))
        return devices

    # ── NON-ARRAY DEVICE: normal m/nf expansion ──
    total = nf * m
    devices = []

    for i in range(total):
        if total > 1:
            mult_bucket = i // nf + 1
            finger_idx = i % nf + 1

            if m > 1 and nf > 1:
                new_name = f"{name}_m{mult_bucket}_f{finger_idx}"
            elif m > 1:
                new_name = f"{name}_m{mult_bucket}"
            else:
                new_name = f"{name}_f{finger_idx}"
        else:
            new_name = name

        new_params = params.copy()
        new_params["nf"] = 1
        new_params["m"] = m
        new_params["parent"] = name
        if total > 1:
            if m > 1:
                new_params["multiplier_index"] = mult_bucket
            if nf > 1:
                new_params["finger_index"] = finger_idx

        devices.append(Device(new_name, dtype, pins, new_params))

    return devices


def parse_cap(tokens):
    """
    Parse a capacitor line.

    Handles both simple and CDL-style formats:
      Simple:  Cname n+ n- value
      CDL:     Cname n+ n- model cval=X w=X l=X nf=X ...
    """
    name = tokens[0]
    n1, n2 = tokens[1], tokens[2]

    params = {}
    value = 0.0

    # Remaining tokens after the two nets
    rest = tokens[3:]
    for tok in rest:
        if '=' in tok:
            k, v = tok.split('=', 1)
            k = k.lower()
            try:
                params[k] = parse_value(v)
            except Exception:
                params[k] = v
        else:
            # Could be a plain value or the model name
            try:
                value = parse_value(tok)
            except Exception:
                params['model'] = tok  # store model name

    # cval is the preferred capacitance key; fall back to plain value
    if 'cval' in params:
        value = params['cval']
    params.setdefault('value', value)

    return Device(name, "cap", {"1": n1, "2": n2}, params)


def parse_res(tokens):
    """
    Parse a resistor line.

    Handles both simple and CDL-style formats:
      Simple:  Rname n+ n- value
      CDL:     Rname n+ n- model w=X l=X m=X ...
    """
    name = tokens[0]
    n1, n2 = tokens[1], tokens[2]

    params = {}
    value = 0.0

    rest = tokens[3:]
    for tok in rest:
        if '=' in tok:
            k, v = tok.split('=', 1)
            k = k.lower()
            try:
                params[k] = parse_value(v)
            except Exception:
                params[k] = v
        else:
            try:
                value = parse_value(tok)
            except Exception:
                params['model'] = tok

    params.setdefault('value', value)
    return Device(name, "res", {"1": n1, "2": n2}, params)


# -------------------------------------------------
# Line Dispatcher
# -------------------------------------------------


def parse_line(line):
    tokens = line.split()
    if not tokens:
        return None

    # MOS detection: must have at least 6 tokens
    if len(tokens) >= 6:
        model = tokens[5].lower()
        if model.startswith("n") or model.startswith("p"):
            return parse_mos(tokens)

    # Capacitor
    if tokens[0][0].upper() == 'C' and len(tokens) >= 4:
        return parse_cap(tokens)

    # Resistor
    if tokens[0][0].upper() == 'R' and len(tokens) >= 4:
        return parse_res(tokens)

    return None


# -------------------------------------------------
# Main Reader
# -------------------------------------------------

def read_netlist(filename):
    """
    Read SPICE/CDL netlist and return FLATTENED netlist
    """

    nl = Netlist()

    # STEP 1 — flatten hierarchy
    flat_lines = flatten_netlist(filename)

    # DEBUG: print flattened lines
    logging.debug("\n--- Flattened Netlist ---")
    for l in flat_lines:
        logging.debug(l)

    # STEP 2 — parse devices
    for line in flat_lines:

        line = line.strip()

        if not line or line.startswith('*'):
            continue

        dev = parse_line(line)

        if isinstance(dev, list):
            for d in dev:
                nl.add_device(d)
        elif dev:
            nl.add_device(dev)


    # STEP 3 — build connectivity
    nl.build_connectivity()

    return nl


def read_netlist_with_blocks(filename):
    """Read SPICE/CDL netlist and return FLATTENED netlist + block map.

    Returns:
        nl: Netlist object with devices and connectivity
        block_map: {device_name: {"instance": "XI0", "subckt": "Inverter"}}
    """

    nl = Netlist()

    # STEP 1 — flatten hierarchy with block tracking
    flat_lines, block_map = flatten_netlist_with_blocks(filename)

    # DEBUG: print flattened lines
    logging.debug("\n--- Flattened Netlist (block-aware) ---")
    for l in flat_lines:
        logging.debug(l)

    # STEP 2 — parse devices
    for line in flat_lines:
        line = line.strip()
        if not line or line.startswith('*'):
            continue
        dev = parse_line(line)
        if isinstance(dev, list):
            for d in dev:
                nl.add_device(d)
        elif dev:
            nl.add_device(dev)

    # STEP 3 — build connectivity
    nl.build_connectivity()

    return nl, block_map


# -------------------------------------------------
# Hierarchy-aware netlist reader
# -------------------------------------------------

def read_netlist_with_hierarchy(filename):
    """Read SPICE/CDL netlist and return a hierarchy-aware netlist.

    This entry point:
      1. Parses device lines (parse_mos expands m/nf into individual devices)
      2. Reconstructs DeviceHierarchy objects from the expanded devices
      3. Returns the flat netlist (with all expanded devices) plus hierarchies

    The netlist.devices dict contains all expanded finger devices (backward-
    compatible).  The hierarchies dict provides the logical grouping for
    placement and visualization.

    Returns
    -------
    nl            : Netlist object with all expanded leaf devices and connectivity
    hierarchies   : dict[name -> DeviceHierarchy] mapping parent names to hierarchies
    """
    from .hierarchy import build_device_hierarchy

    nl = Netlist()

    # STEP 1 — flatten hierarchy
    flat_lines = flatten_netlist(filename)

    logging.debug("\n--- Flattened Netlist (hierarchy-aware) ---")
    for l in flat_lines:
        logging.debug(l)

    # STEP 2 — parse devices (parse_mos returns expanded devices)
    parsed_devices = []
    for line in flat_lines:
        line = line.strip()
        if not line or line.startswith('*'):
            continue
        dev = parse_line(line)
        if isinstance(dev, list):
            parsed_devices.extend(dev)
        elif dev:
            parsed_devices.append(dev)

    # Add all devices to the netlist
    for dev in parsed_devices:
        nl.add_device(dev)

    # STEP 3 — reconstruct hierarchies from expanded devices
    hierarchies = build_device_hierarchy(parsed_devices)

    # STEP 4 — build connectivity
    nl.build_connectivity()

    return nl, hierarchies

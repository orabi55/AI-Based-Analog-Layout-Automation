
import re
from .device import Device
from .units import parse_value
from .netlist import Netlist
from .hierarchy import flatten_netlist


# -------------------------------------------------
# Device Parsers
# -------------------------------------------------

def parse_mos(tokens):
    """
    Generic MOS parser for CDL style:

    MM25 D G S B model param=value ...
    Expands multi-finger (nf) and multiplier (m).
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
                nf = int(val)

            if k == "m":
                m = int(val)

    pins = {"D": D, "G": G, "S": S, "B": B}

    # -------------------------------------------------
    # FINGER + MULTIPLIER EXPANSION
    # -------------------------------------------------

    total_instances = nf * m

    devices = []

    for i in range(total_instances):

        if total_instances > 1:
            new_name = f"{name}_f{i+1}"
        else:
            new_name = name

        # Each physical finger should have nf = 1
        new_params = params.copy()
        new_params["nf"] = 1
        new_params["parent"] = name

        devices.append(Device(new_name, dtype, pins, new_params))

    return devices


def parse_cap(tokens):
    """
    Cname node1 node2 value
    """
    name = tokens[0]
    n1, n2, val = tokens[1], tokens[2], tokens[3]

    return Device(name, "cap", {"1": n1, "2": n2},
                  {"value": parse_value(val)})


def parse_res(tokens):
    """
    Rname node1 node2 value
    """
    name = tokens[0]
    n1, n2, val = tokens[1], tokens[2], tokens[3]

    return Device(name, "res", {"1": n1, "2": n2},
                  {"value": parse_value(val)})


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
    print("\n--- Flattened Netlist ---")
    for l in flat_lines:
        print(l)

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
    from .hierarchy import flatten_netlist_with_blocks

    nl = Netlist()

    # STEP 1 — flatten hierarchy with block tracking
    flat_lines, block_map = flatten_netlist_with_blocks(filename)

    # DEBUG: print flattened lines
    print("\n--- Flattened Netlist (block-aware) ---")
    for l in flat_lines:
        print(l)

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

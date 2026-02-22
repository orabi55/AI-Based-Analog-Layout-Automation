class Subckt:
    def __init__(self, name, ports, lines):
        self.name = name
        self.ports = ports      # ordered port list
        self.lines = lines      # internal device lines


def extract_subckts(lines):
    subckts = {}
    current = None
    body = []

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
            subckts[current] = Subckt(current, ports, body)
            current = None
            continue

        if current:
            body.append(line)

    return subckts


def expand_instance(line, subckts, prefix=""):
    tokens = line.split()

    inst_name = tokens[0]          # X1
    nets = tokens[1:-1]            # vin vip vout vss
    subckt_name = tokens[-1]       # pair

    subckt = subckts[subckt_name]

    # Map subckt ports → actual nets
    port_map = dict(zip(subckt.ports, nets))

    expanded = []

    for internal_line in subckt.lines:

        parts = internal_line.split()
        devname = parts[0]

        # Rename instance
        parts[0] = f"{inst_name}_{devname}"

        # Replace nets token-by-token
        for i in range(1, len(parts)):
            token = parts[i]

            # Only replace exact net names
            if token in port_map:
                parts[i] = port_map[token]

        expanded.append(" ".join(parts))

    return expanded




def flatten_netlist(filename):

    with open(filename) as f:
        raw_lines = [l.strip() for l in f if l.strip()]

    subckts = extract_subckts(raw_lines)

    # If there are NO X instantiations,
    # just return raw lines (no flattening needed)
    has_instance = any(line.strip().startswith("X") for line in raw_lines)

    if not has_instance:
        return raw_lines

    flat_lines = []
    inside_subckt = False

    for line in raw_lines:

        tokens = line.split()
        if not tokens:
            continue

        keyword = tokens[0].upper()

        if keyword == ".SUBCKT":
            inside_subckt = True
            continue

        if keyword == ".ENDS":
            inside_subckt = False
            continue

        if inside_subckt:
            continue

        if tokens[0][0].upper() == 'X':
            flat_lines.extend(expand_instance(line, subckts))
        else:
            flat_lines.append(line)

    return flat_lines
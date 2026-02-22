import networkx as nx

def normalize_pin(pin):
    pin = pin.upper()
    if pin in ["D", "1"]: return "drain"
    if pin in ["G"]: return "gate"
    if pin in ["S", "2"]: return "source"
    if pin in ["B"]: return "bulk"
    return "other"


def add_device_nodes(G, netlist):
    for dev in netlist.devices.values():

        params = dev.params
        W = params.get("w",0)
        L = params.get("l",0)
        nf = params.get("nf",1)

        G.add_node(
            dev.name,
            type=dev.type,
            W=W,
            L=L,
            nf=nf
        )



def classify_net(net, connections):

    source_count = 0
    gate_count = 0
    drain_count = 0

    for dev, pin in connections:
        role = normalize_pin(pin)

        if role == "source": source_count += 1
        if role == "gate":   gate_count += 1
        if role == "drain":  drain_count += 1

    # heuristics
    if source_count >= 3 and gate_count == 0:
        return "bias"

    if drain_count >= 2 and gate_count == 0:
        return "signal"

    if gate_count >= 2:
        return "gate"

    return "other"





GLOBAL_NETS = {"vdd","vss","gnd","vcc","vdda","vssa"}


def add_net_edges(G, netlist):

    for net, connections in netlist.nets.items():

        # -------------------------------------------------
        # 1) Ignore supply nets (they destroy graph meaning)
        # -------------------------------------------------
        if net.lower() in GLOBAL_NETS:
            continue

        # -------------------------------------------------
        # 2) Classify electrical role of this net
        # -------------------------------------------------
        net_type = classify_net(net, connections)

        # -------------------------------------------------
        # 3) Compare all devices connected to this net
        # -------------------------------------------------
        for i in range(len(connections)):
            dev1, pin1 = connections[i]
            role1 = normalize_pin(pin1)

            for j in range(i+1, len(connections)):
                dev2, pin2 = connections[j]

                if dev1 == dev2:
                    continue

                role2 = normalize_pin(pin2)

                # -------------------------------------------------
                # 4) Assign relation based on role + net meaning
                # -------------------------------------------------
                relation = "connection"

                # SOURCE-SOURCE
                if role1 == "source" and role2 == "source":
                    if net_type == "bias":
                        relation = "shared_bias"
                    else:
                        relation = "shared_source"

                # GATE-GATE (mirror candidates)
                elif role1 == "gate" and role2 == "gate":
                    relation = "shared_gate"

                # DRAIN-DRAIN (load pair / diffpair)
                elif role1 == "drain" and role2 == "drain":
                    relation = "shared_drain"

                # Mixed roles → signal interaction
                else:
                    relation = "connection"

                # -------------------------------------------------
                # 5) Add edge
                # -------------------------------------------------
                G.add_edge(dev1, dev2,
                           relation=relation,
                           net=net)



def build_circuit_graph(netlist):

    G = nx.Graph()

    add_device_nodes(G, netlist)
    add_net_edges(G, netlist)

    return G


def print_graph(G):

    print("\n--- Nodes ---")
    for n,data in G.nodes(data=True):
        print(n,data)

    print("\n--- Edges ---")
    for u,v,data in G.edges(data=True):
        print(u,"<->",v,data)





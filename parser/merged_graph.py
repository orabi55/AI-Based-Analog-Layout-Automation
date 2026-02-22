import networkx as nx


def build_merged_graph(netlist, layout_devices, mapping):
    """
    Merge electrical netlist graph with layout geometry.
    """

    G = nx.Graph()

    # --------------------------------------------------
    # Add nodes with electrical + geometric features
    # --------------------------------------------------
    for dev in netlist.devices.values():

        name = dev.name
        layout_idx = mapping[name]
        geo = layout_devices[layout_idx]

        G.add_node(
            name,
            type=dev.type,
            l=dev.params.get("l", 0),
            nf=dev.params.get("nf", 1),
            nfin=dev.params.get("nfin", 1),
            x=geo["x"],
            y=geo["y"],
            width=geo["width"],
            height=geo["height"],
            orientation=geo["orientation"]
        )

    # --------------------------------------------------
    # Add electrical edges (reuse previous logic)
    # --------------------------------------------------
    for net, connections in netlist.nets.items():

        if net.lower() in {"vdd", "vss", "gnd"}:
            continue

        for i in range(len(connections)):
            dev1, pin1 = connections[i]

            for j in range(i+1, len(connections)):
                dev2, pin2 = connections[j]

                if dev1 == dev2:
                    continue

                G.add_edge(dev1, dev2, net=net)

    return G
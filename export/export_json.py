import json


def graph_to_json(G, output_file):
    """
    Convert merged NetworkX graph to JSON file
    for AI placement agent.
    """

    data = {
        "nodes": [],
        "edges": []
    }

    # ----------------------------
    # Export nodes
    # ----------------------------
    for node, attrs in G.nodes(data=True):

        node_entry = {
            "id": node,
            "type": attrs["type"],
            "electrical": {
                "l": attrs["l"],
                "nf": attrs["nf"],
                "nfin": attrs["nfin"]
            },
            "geometry": {
                "x": attrs["x"],
                "y": attrs["y"],
                "width": attrs["width"],
                "height": attrs["height"],
                "orientation": attrs["orientation"]
            }
        }

        data["nodes"].append(node_entry)

    # ----------------------------
    # Export edges
    # ----------------------------
    for u, v, attrs in G.edges(data=True):

        edge_entry = {
            "source": u,
            "target": v,
            "net": attrs.get("net", "")
        }

        data["edges"].append(edge_entry)

    # ----------------------------
    # Write file
    # ----------------------------
    with open(output_file, "w") as f:
        json.dump(data, f, indent=4)

    print(f"\nJSON exported to {output_file}")
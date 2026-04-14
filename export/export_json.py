import json


# ---------------------------------------------------------------------------
# Abutment Detection
# ---------------------------------------------------------------------------

def _detect_abutments(nodes):
    """
    Detect pairs of nodes whose placed positions are immediately adjacent:
    the right edge of node_i touches the left edge of node_j (same y-row).

    Returns a list of dicts:  {"left": id_i, "right": id_j}

    A tolerance of 1e-4 µm is used for floating-point comparison.
    """
    abutments = []
    tol = 1e-4  # µm

    for i, ni in enumerate(nodes):
        gi = ni.get("geometry", {})
        xi = float(gi.get("x", 0))
        yi = float(gi.get("y", 0))
        wi = float(gi.get("width", 0))
        right_edge_i = xi + wi

        for j, nj in enumerate(nodes):
            if i >= j:
                continue
            gj = nj.get("geometry", {})
            xj = float(gj.get("x", 0))
            yj = float(gj.get("y", 0))
            wj = float(gj.get("width", 0))

            if abs(yj - yi) > tol:
                continue

            if abs(xj - right_edge_i) < tol:          # ni is left of nj
                abutments.append({"left": ni["id"], "right": nj["id"]})
            elif abs(xi - (xj + wj)) < tol:            # nj is left of ni
                abutments.append({"left": nj["id"], "right": ni["id"]})

    return abutments


def _mark_abut_flags(nodes, abutments):
    """
    Stamp abut_left / abut_right boolean flags onto each node's geometry dict
    in-place, based on the detected abutments list.
    """
    node_map = {n["id"]: n for n in nodes}

    # Reset flags first so re-runs are idempotent
    for n in nodes:
        n["geometry"]["abut_left"]  = False
        n["geometry"]["abut_right"] = False

    for ab in abutments:
        left_node  = node_map.get(ab["left"])
        right_node = node_map.get(ab["right"])
        if left_node:
            left_node["geometry"]["abut_right"] = True
        if right_node:
            right_node["geometry"]["abut_left"] = True


# ---------------------------------------------------------------------------
# Main Export
# ---------------------------------------------------------------------------

def graph_to_json(G, output_file):
    """
    Convert merged NetworkX graph to JSON file for AI placement agent.

    Schema additions vs. original:
      - geometry.abut_left  : bool — this device shares its left diffusion
                                     edge with the immediately preceding device
      - geometry.abut_right : bool — this device shares its right diffusion
                                     edge with the immediately following device
      - top-level "abutments": list of {left, right} pairs identifying every
                                adjacent device boundary in the placed layout
    """

    data = {
        "nodes": [],
        "edges": [],
        "abutments": []          # ← NEW: explicit abutment pair list
    }

    # ----------------------------
    # Export nodes
    # ----------------------------
    for node, attrs in G.nodes(data=True):

        node_entry = {
            "id": node,
            "type": attrs["type"],
            "electrical": {
                "l":    attrs["l"],
                "nf":   attrs["nf"],
                "nfin": attrs["nfin"]
            },
            "geometry": {
                "x":           attrs["x"],
                "y":           attrs["y"],
                "width":       attrs["width"],
                "height":      attrs["height"],
                "orientation": attrs["orientation"],
                "abut_left":   False,   # ← NEW: stamped below
                "abut_right":  False,   # ← NEW: stamped below
            }
        }

        data["nodes"].append(node_entry)

    # ----------------------------
    # Detect abutments and stamp per-node flags
    # ----------------------------
    abutments = _detect_abutments(data["nodes"])
    _mark_abut_flags(data["nodes"], abutments)
    data["abutments"] = abutments

    # ----------------------------
    # Export edges
    # ----------------------------
    for u, v, attrs in G.edges(data=True):

        edge_entry = {
            "source": u,
            "target": v,
            "net":    attrs.get("net", "")
        }

        data["edges"].append(edge_entry)

    # ----------------------------
    # Write file
    # ----------------------------
    with open(output_file, "w") as f:
        json.dump(data, f, indent=4)

    n_abut = len(abutments)
    print(f"\nJSON exported to {output_file}  "
          f"({n_abut} abutment pair{'s' if n_abut != 1 else ''} detected)")
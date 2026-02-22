import gdstk


def extract_layout_instances(layout_file):

    if layout_file.endswith(".gds"):
        lib = gdstk.read_gds(layout_file)
    elif layout_file.endswith(".oas"):
        lib = gdstk.read_oas(layout_file)
    else:
        raise ValueError("Unsupported layout format")

    top_cell = lib.top_level()[0]

    devices = []

    for ref in top_cell.references:

        cell_name = ref.cell.name
        x, y = ref.origin

        rotation = ref.rotation if ref.rotation else 0
        mirrored = ref.x_reflection

        if mirrored:
            orientation = "MX"
        else:
            orientation = f"R{int(rotation)}"

        bbox = ref.cell.bounding_box()

        if bbox:
            (xmin, ymin), (xmax, ymax) = bbox
            width = xmax - xmin
            height = ymax - ymin
        else:
            width = 0
            height = 0

        devices.append({
            "cell": cell_name,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "orientation": orientation
        })

    return devices
"""
Replacement for _pad_rows_with_dummies in nodes.py (lines 705-813).
Run this to overwrite the function content with the new centered implementation.
"""
NEW_FUNCTION = '''# ── Row normalisation + dummy padding ────────────────────────────────────────
_DUMMY_PITCH = 0.294  # um standard finger pitch for dummy devices


def _pad_rows_with_dummies(nodes: list) -> list:
    """
    Normalise and pad all active rows to produce a perfectly rectangular layout.

    Algorithm (must run AFTER matching):
      1. SEPARATE:   Strip out any pre-existing dummy nodes (regenerated fresh).
      2. LEFT-ALIGN: Shift every active row so its leftmost device is at x=0.
      3. MEASURE:    Find global max width from ACTIVE rows only.
                     (Rogue dummy-only rows are excluded and discarded.)
      4. CENTER:     For each shorter row compute total dummies needed.
                     Split evenly: floor(N/2) LEFT, ceil(N/2) RIGHT.
                     Shift real devices right to make room for left dummies.
      5. RETURN:     Active nodes (updated x) + fresh dummy nodes.
    """
    from collections import defaultdict

    if not nodes:
        return nodes

    # Step 1: discard pre-existing dummies - we rebuild them from scratch
    active_nodes = [n for n in nodes if not n.get("is_dummy")]
    if not active_nodes:
        return nodes

    # Step 2: group active nodes by row (rounded y)
    row_map: dict = defaultdict(list)
    for n in active_nodes:
        geo = n.get("geometry")
        if not geo:
            continue
        ry = round(float(geo.get("y", 0.0)), 4)
        row_map[ry].append(n)

    if not row_map:
        return nodes

    # Step 3: left-align every row to x=0
    for ry, rnodes in row_map.items():
        leftmost_x = min(float(n["geometry"]["x"]) for n in rnodes)
        if abs(leftmost_x) > 1e-6:
            for n in rnodes:
                n["geometry"]["x"] = round(float(n["geometry"]["x"]) - leftmost_x, 6)
            print(f"[LAYOUT]  y={ry:>7.3f}: shifted left {leftmost_x:.3f}um", flush=True)

    # Step 4: measure global max width from active rows only
    global_max_right = 0.0
    for ry, rnodes in row_map.items():
        rightmost = max(rnodes, key=lambda n: float(n["geometry"]["x"]))
        row_right = float(rightmost["geometry"]["x"]) + _DUMMY_PITCH
        global_max_right = max(global_max_right, row_right)

    if global_max_right <= 0:
        return active_nodes

    # Snap to integer number of slots
    n_slots = round(global_max_right / _DUMMY_PITCH)
    global_max_right = n_slots * _DUMMY_PITCH

    print(f"[LAYOUT]  Global width = {global_max_right:.3f}um ({n_slots} slots)", flush=True)

    # Step 5: center each row with equal L/R dummy padding
    dummy_counter = 0
    new_dummies = []

    for ry, rnodes in sorted(row_map.items()):
        row_type     = str(rnodes[0].get("type", "nmos")).lower()
        dummy_type   = "pmos" if row_type.startswith("p") else "nmos"
        dummy_prefix = "DUMMYP" if dummy_type == "pmos" else "DUMMYN"
        row_dev_w    = float(rnodes[0].get("geometry", {}).get("width",  _DUMMY_PITCH))
        row_dev_h    = float(rnodes[0].get("geometry", {}).get("height", 0.5))

        n_active        = len(rnodes)
        n_total_dummies = n_slots - n_active

        if n_total_dummies <= 0:
            continue  # already full-width

        n_left  = n_total_dummies // 2       # floor -> left side
        n_right = n_total_dummies - n_left   # ceil  -> right side

        # Shift real transistors right to make room for left dummies
        x_offset = n_left * _DUMMY_PITCH
        for n in rnodes:
            n["geometry"]["x"] = round(float(n["geometry"]["x"]) + x_offset, 6)

        # Left dummies: slots 0 .. n_left-1
        for i in range(n_left):
            dummy_counter += 1
            new_dummies.append({
                "id": f"{dummy_prefix}_L_{dummy_counter}",
                "type": dummy_type,
                "is_dummy": True,
                "geometry": {
                    "x": round(i * _DUMMY_PITCH, 6),
                    "y": ry,
                    "width":  row_dev_w,
                    "height": row_dev_h,
                    "orientation": "R0",
                },
                "electrical": {"nf": 1},
                "abutment": {"abut_left": False, "abut_right": False},
            })

        # Right dummies: slots (n_left + n_active) .. n_slots-1
        right_start = n_left + n_active
        for i in range(n_right):
            dummy_counter += 1
            new_dummies.append({
                "id": f"{dummy_prefix}_R_{dummy_counter}",
                "type": dummy_type,
                "is_dummy": True,
                "geometry": {
                    "x": round((right_start + i) * _DUMMY_PITCH, 6),
                    "y": ry,
                    "width":  row_dev_w,
                    "height": row_dev_h,
                    "orientation": "R0",
                },
                "electrical": {"nf": 1},
                "abutment": {"abut_left": False, "abut_right": False},
            })

        print(f"[LAYOUT]  y={ry:>7.3f}: {n_active} active | "
              f"{n_left}L + {n_right}R dummies | total={n_slots}", flush=True)

    if dummy_counter > 0:
        print(f"[LAYOUT]  Done: {dummy_counter} dummies, "
              f"all rows = {global_max_right:.3f}um ({n_slots} slots)", flush=True)
    else:
        print(f"[LAYOUT]  All rows already full ({global_max_right:.3f}um)", flush=True)

    return active_nodes + new_dummies

'''
print(NEW_FUNCTION[:100])

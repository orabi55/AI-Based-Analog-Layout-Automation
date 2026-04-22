import math
import re
from typing import List, Dict, Tuple, Optional

class SymmetryError(Exception):
    """Raised when a placement cannot meet mathematical symmetry constraints."""
    pass

def generate_placement_grid(
    devices_in: Dict[str, int], 
    technique: str, 
    rows: int = 1,
    custom_str: Optional[str] = None
) -> List[Dict]:
    """
    Final Quadrant-Mirroring Symmetry Engine.
    
    Logic:
    1. Pad with Dummies for perfect rectangular symmetry.
    2. Seed the first quadrant/half using Ratio-based Interleaving.
    3. Mirror X, then Mirror Y (stacked/reversed).
    4. Assert Centroid equality.
    """
    technique = technique.upper()
    devices = devices_in.copy()

    # 1. Custom Pattern Bypass
    if technique == 'CUSTOM' and custom_str:
        return _handle_custom(devices, custom_str)

    # 2. Grid & Dummy Initialization
    is_2d = rows > 1
    # Factor: 2 for 1D CC, 4 for 2D CC (cross-quad)
    symmetry_factor = 4 if is_2d else 2
    
    total_fingers = sum(devices.values())
    if total_fingers == 0: return []

    # Ensure total is divisible by symmetry factor
    remainder = total_fingers % symmetry_factor
    if remainder != 0:
        padding = symmetry_factor - remainder
        devices["DUMMY"] = devices.get("DUMMY", 0) + padding
        total_fingers += padding

    # 3. Seed Generation
    if is_2d:
        if rows != 2:
            raise SymmetryError("COMMON_CENTROID_2D currently supports exactly 2 rows.")

        odd_devices = [
            dev_id for dev_id, count in devices.items()
            if dev_id != "DUMMY" and count % 2 != 0
        ]
        if odd_devices:
            raise SymmetryError(
                "COMMON_CENTROID_2D requires even finger counts per device: "
                + ", ".join(sorted(odd_devices))
            )

        # Build the full top row, then mirror it onto the bottom row.
        # This preserves point symmetry even for small valid cases like AB/BA.
        seed_counts = {d: count // 2 for d, count in devices.items()}
    else:
        # 1D common-centroid/interdigitated uses a half-row seed.
        seed_counts = {d: count // symmetry_factor for d, count in devices.items()}
    seed_total = sum(seed_counts.values())
    
    # Fill seed list using Ratio-based Interleaving
    # Ratio = Remaining / Required
    seed_list = []
    temp_seed_counts = seed_counts.copy()
    for _ in range(seed_total):
        best_dev = None
        max_ratio = -1.0
        for d, count in temp_seed_counts.items():
            if count > 0:
                # Use current/initial ratio to pick next
                ratio = count / seed_counts[d]
                if ratio > max_ratio:
                    max_ratio = ratio
                    best_dev = d
        if best_dev:
            seed_list.append(best_dev)
            temp_seed_counts[best_dev] -= 1

    # 4. Matrix Assembly
    cols = (total_fingers // rows)
    grid = [[None for _ in range(cols)] for _ in range(rows)]
    
    if not is_2d:
        # 1D Mirrored: [Seed] [Reversed(Seed)]
        grid[0][0:seed_total] = seed_list
        grid[0][seed_total:2*seed_total] = reversed(seed_list)
    else:
        # 2D point symmetry: build the full top row, then reverse it below.
        seed_len = len(seed_list)
        grid[0][0:seed_len] = seed_list
        grid[1][:] = list(reversed(grid[0]))

    # 5. Mathematical Audit
    _analytical_audit(grid)

    return _convert_grid_to_coords(grid)

def _analytical_audit(grid: List[List[str]]):
    rows = len(grid)
    cols = len(grid[0])
    
    stats = {} # {id: [sum_x, sum_y, count]}
    for r in range(rows):
        for c in range(cols):
            dev = grid[r][c]
            if not dev: continue
            if dev not in stats: stats[dev] = [0.0, 0.0, 0]
            stats[dev][0] += c
            stats[dev][1] += r
            stats[dev][2] += 1
            
    # Global grid center
    gx = (cols - 1) / 2.0
    gy = (rows - 1) / 2.0
    
    for dev, s in stats.items():
        if dev == "DUMMY": continue
        cx = s[0] / s[2]
        cy = s[1] / s[2]
        # Match centroids to within 0.001
        if abs(cx - gx) > 0.001 or abs(cy - gy) > 0.001:
            raise SymmetryError(f"Audit Failed: {dev} centroid ({cx:.3f}, {cy:.3f}) != Grid Center ({gx:.3f}, {gy:.3f})")

def _handle_custom(devices: Dict[str, int], custom_str: str) -> List[Dict]:
    from ai_agent.matching.universal_pattern_generator import parse_gui_string
    dev_list = sorted(devices.keys())
    raw_grid = parse_gui_string(custom_str, dev_list)
    
    # Check counts
    p_counts = {}
    for row in raw_grid:
        for cell in row:
            p_counts[cell] = p_counts.get(cell, 0) + 1
    for d, c in p_counts.items():
        if d != "DUMMY" and c > devices.get(d, 0):
            raise SymmetryError(f"Pattern uses {c} of {d}, but only {devices.get(d, 0)} available.")
    
    return _convert_grid_to_coords(raw_grid)

def _convert_grid_to_coords(grid: List[List[str]]) -> List[Dict]:
    coords = []
    for r in range(len(grid)):
        for c in range(len(grid[r])):
            if grid[r][c]:
                coords.append({"device": grid[r][c], "x_index": c, "y_index": r})
    return coords

def parse_gui_string(pattern_str: str, available_devices: List[str]) -> List[List[str]]:
    rows_raw = pattern_str.strip().split('/')
    grid = []
    char_map = {chr(ord('A') + i): dev for i, dev in enumerate(available_devices)}
    char_map.update({chr(ord('a') + i): dev for i, dev in enumerate(available_devices)})
    
    for row_raw in rows_raw:
        tokens = re.findall(r'[A-Za-z]', row_raw)
        mapped_row = [char_map.get(t, "DUMMY") for t in tokens]
        grid.append(mapped_row)
    return grid

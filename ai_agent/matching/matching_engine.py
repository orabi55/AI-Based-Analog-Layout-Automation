"""
Matching Engine (Extended)
==========================
Handles the mapping of logical device IDs to physical coordinates based on 
specified matching techniques and placement patterns, utilizing the 
Universal Pattern Generator.

Functions:
- generate_placement (in MatchingEngine): Generates physical placements for a list of devices.
  - Inputs: device_ids (list), technique (str), custom_str (optional str)
  - Outputs: list of dictionaries with ID, x, and y.
- _get_parent: Extracts the parent device ID from a finger/instance ID.
- _sort_key: Provides a numeric sort key for device IDs.
"""
    def __init__(self, device_items: Dict):
        """
        device_items: Dictionary mapping ID to device item objects 
                     which have .boundingRect() and current .pos()
        """
        self.device_items = device_items

    def generate_placement(self, device_ids: List[str], technique: str, custom_str: Optional[str] = None) -> List[Dict]:
        """
        Main entry point for matching.
        """
        # 1. Group by parent and count fingers
        parent_map = {} # {parent: [id1, id2, ...]}
        for did in device_ids:
            parent = self._get_parent(did)
            if parent not in parent_map:
                parent_map[parent] = []
            parent_map[parent].append(did)
            
        sorted_parents = sorted(parent_map.keys())
        counts = {p: len(parent_map[p]) for p in sorted_parents}
        
        # 2. Call the generator
        rows = 2 if technique.lower() == "common_centroid_2d" else 1
        # If custom pattern contains '/', it might have more rows
        if custom_str and '/' in custom_str:
            rows = custom_str.count('/') + 1
            
        gen_technique = technique.upper()
        
        # Map parents to Tokens (M0, M1, M2...)
        token_to_parent = {f"M{i}": p for i, p in enumerate(sorted_parents)}
        token_counts = {f"M{i}": counts[p] for i, p in enumerate(sorted_parents)}
        
        grid_coords = generate_placement_grid(token_counts, gen_technique, rows, custom_str)
        
        # 3. Map Grid to Physical Coordinates
        # We need representative dimensions (use first device)
        rep_id = device_ids[0]
        rep_item = self.device_items[rep_id]
        pixel_width = rep_item.boundingRect().width()
        
        # Consistent Row Height (0.668 um -> pixels? 
        # In main.py ROW_PITCH seems to be the target. 
        # For simplicity, we'll try to get it from context or hardcode if needed.
        # But wait, looking at main.py: ROW_PITCH = self.editor.ROW_PITCH if available
        # Let's use a standard multiplier based on pixel_width if possible, 
        # or just assume the passed item dimensions are correct.
        pixel_height = rep_item.boundingRect().height()
        # Row height is precisely the pixel_height to ensure "one ABOVE the other"
        # stacking without overlap or gaps.
        row_step = pixel_height 
        
        # Anchor position (top-left of current selection)
        anchor_x = min(self.device_items[did].pos().x() for did in device_ids if did in self.device_items)
        anchor_y = min(self.device_items[did].pos().y() for did in device_ids if did in self.device_items)
        
        # Group available IDs by parent for mapping
        available_ids = {p: sorted(parent_map[p], key=self._sort_key) for p in sorted_parents}
        
        placements = []
        for gc in grid_coords:
            token = gc["device"]
            parent = token_to_parent[token]
            
            # Pop the first available finger id for this parent
            if not available_ids[parent]: continue
            instance_id = available_ids[parent].pop(0)
            
            target_x = anchor_x + gc["x_index"] * pixel_width
            target_y = anchor_y + gc["y_index"] * row_step
            
            placements.append({
                "id": instance_id,
                "x": target_x,
                "y": target_y
            })
            
        return placements

    def _get_parent(self, did: str) -> str:
        # Match prefix before _m or _f or _finger
        m = re.match(r'^([A-Za-z]+\d+)', did)
        return m.group(1) if m else did

    def _sort_key(self, did: str):
        # Numeric sort for things like M1_f10
        nums = re.findall(r'\d+', did)
        return [int(x) for x in nums]

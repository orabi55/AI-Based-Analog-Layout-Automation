import os

class LayoutExporter:
    def __init__(self, filename="ai_placement.txt"):
        self.filename = filename
        self.instances = []

    def add_instance(self, name, x, y, orient="R0", params=None):
        """
        Prepares a transistor for layout export, including physical parameters.
        
        Args:
            name (str): Instance name (e.g., 'MM28' or 'M28'). Auto-cleans double 'M'.
            x (float): X coordinate in microns.
            y (float): Y coordinate in microns.
            orient (str): Orientation code (R0, R90, MX, etc.).
            params (dict): Dictionary of PCell parameters (e.g., {"nf": 4, "w": 0.1}).
        """
        # Ensure name matches the layout database
        clean_name = name[1:] if name.startswith("MM") else name
        
        # Strip finger/multiplier suffixes (e.g. M2_m1_f1 -> M2)
        if "_" in clean_name:
            clean_name = clean_name.split("_")[0]
        
        self.instances.append({
            "name": clean_name,
            "x": float(x),
            "y": float(y),
            "orient": orient,
            "params": params or {}
        })
    def add_multi_finger_device(self, base_name, m_index, total_fingers, start_x, y, cpp=0.15, external_left_abut=0, external_right_abut=0):
        """
        Automatically generates explicitly named unit cells for a multi-finger device.
        
        Args:
            base_name (str): e.g., 'MM1'
            m_index (int): Multiplier index (e.g., 2)
            total_fingers (int): Total fingers to generate (e.g., 4)
            start_x (float): X coordinate of the first finger
            y (float): Y coordinate
            cpp (float): Contacted Poly Pitch (the exact grid distance between fingers)
            external_left_abut: Set to 1 if Finger 1 abuts a completely different transistor.
            external_right_abut: Set to 1 if the Last Finger abuts a completely different transistor.
        """
        clean_name = base_name[1:] if base_name.startswith("MM") else base_name

        for f_index in range(1, total_fingers + 1):
            # Format the explicit name: e.g., M1_m2_f1
            inst_name = f"{clean_name}_m{m_index}_f{f_index}"
            
            # Calculate X coordinate perfectly on the FinFET grid
            current_x = start_x + ((f_index - 1) * cpp)
            
            # Base parameters for every unit cell (since it's a slice, nf is ALWAYS 1)
            params = {"l": 0.014, "nf": 1, "nfin": 4.0, "m": 1}
            
            # --- Internal & External Abutment Logic ---
            
            # Left side logic
            if f_index == 1:
                if external_left_abut: params["left_abut"] = 1
            else:
                params["left_abut"] = 1 # Internal abutment to previous finger

            # Right side logic
            if f_index == total_fingers:
                if external_right_abut: params["right_abut"] = 1
            else:
                params["right_abut"] = 1 # Internal abutment to next finger

            # Add it to the exporter list
            self.add_instance(inst_name, current_x, y, "R0", params)


    def export_for_tcl(self):
        """Writes the custom delimited text file for the Tcl script."""
        try:
            with open(self.filename, "w") as f:
                exported_names = set()
                count = 0
                for inst in self.instances:
                    if inst['name'] in exported_names:
                        continue
                        
                    # Filter and format parameters
                    valid_params = {}
                    for k, v in inst['params'].items():
                        # Skip internal metadata and zero-width
                        if v is None or k in ["parent", "multiplier_index", "finger_index", "array_index"]:
                            continue
                        if k == "w" and v == 0:
                            continue
                        
                        # Convert lengths in meters (e.g. 1.4e-08) to micrometers (0.014)
                        if k == "l" and isinstance(v, (float, int)):
                            if v < 1e-4:  # definitely in meters
                                v = v * 1e6
                            v = round(v, 3)
                        
                        valid_params[k] = v
                        
                    # Format: Name | X Y Orient | Param1=Val1 Param2=Val2
                    param_str = " ".join([f"{k}={v}" for k, v in valid_params.items()])
                    line = f"{inst['name']} | {inst['x']:.3f} {inst['y']:.3f} {inst['orient']} | {param_str}\n"
                    f.write(line)
                    
                    exported_names.add(inst['name'])
                    count += 1
                    
            print(f"Successfully exported {count} unique instances to {self.filename}")
            return True
        except Exception as e:
            print(f"Error exporting placement file: {e}")
            return False

# --- EXAMPLE USAGE ---
if __name__ == "__main__":
    exporter = LayoutExporter("ai_placement.txt")
    
    # Adding instances with parameters (e.g., updating fingers and width)
    exporter.add_instance("M25", 0.0, -3.057, "R0", params={"nf": 4, "w": 0.15})
    exporter.add_instance("M24", 0.0, -2.389, "R0", params={"nf": 4, "w": 0.15})
    
    exporter.export_for_tcl()

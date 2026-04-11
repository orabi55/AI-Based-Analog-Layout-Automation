import gdstk

def analyze_layout(file_path):
    print(f"Analyzing {file_path}...")
    try:
        if file_path.endswith(".gds"):
            lib = gdstk.read_gds(file_path)
        else:
            lib = gdstk.read_oas(file_path)
            
        top_cell = lib.top_level()[0]
        print(f"Top cell: {top_cell.name}")
        
        # Look for OD layer (diffusion). In SAED 14nm it's often (1, 0)
        # We'll list all layers present to be sure.
        layers = set()
        for poly in top_cell.polygons:
            layers.add((poly.layer, poly.datatype))
        
        for ref in top_cell.references:
            for poly in ref.cell.polygons:
                layers.add((poly.layer, poly.datatype))
        
        print(f"Layers found: {sorted(list(layers))}")
        
        # Analyze instances
        for ref in top_cell.references:
            print(f"\nInstance: {ref.cell.name} at {ref.origin}")
            bbox = ref.cell.bounding_box()
            print(f"  BBox: {bbox}")
            
            # Find diffusion polygons in this reference
            diff_polys = [p for p in ref.cell.polygons if p.layer == 1]
            if diff_polys:
                # Get the combined bbox of all diffusion polygons
                xmin = min(p.bounding_box()[0][0] for p in diff_polys)
                xmax = max(p.bounding_box()[1][0] for p in diff_polys)
                ymin = min(p.bounding_box()[0][1] for p in diff_polys)
                ymax = max(p.bounding_box()[1][1] for p in diff_polys)
                print(f"  Diffusion OD (Layer 1) BBox: ({xmin}, {ymin}), ({xmax}, {ymax})")
                
                # Check for individual fingers/strips
                print(f"  Number of OD polygons: {len(diff_polys)}")
                for i, p in enumerate(diff_polys):
                    pb = p.bounding_box()
                    print(f"    Poly {i}: {pb}")

    except Exception as e:
        print(f"Error: {e}")

analyze_layout("examples/xor/Xor_abut_ex.oas")

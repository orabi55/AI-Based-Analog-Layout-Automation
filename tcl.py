import json

def export_for_tcl(json_path, output_path):
    with open(json_path, 'r') as f:
        data = json.load(f)
    
    with open(output_path, 'w') as f:
        for node in data['nodes']:
            name = node['id']
            # Using geometry values directly
            x = node['geometry']['x']
            y = node['geometry']['y']
            orient = node['geometry']['orientation']
            f.write(f"{name} {x} {y} {orient}\n")

# Usage
export_for_tcl(r"D:\Senior 2\Layout_Project\Automation\new_main\AI-Based-Analog-Layout-Automation\examples\xor\Xor_Automation_initial_placement.json", 'ai_placement.txt')





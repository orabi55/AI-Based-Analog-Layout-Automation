import json

# Validation Script
def verify_area(old_json, new_json, device_id):
    # Calculate area from original fingers
    original_fingers = [n for n in old_json['nodes'] if n['electrical']['parent'] == device_id]
    if not original_fingers:
        print(f"Device {device_id} not found in old JSON")
        return
        
    total_original_width = (max(f['geometry']['x'] for f in original_fingers) + 0.294) - min(f['geometry']['x'] for f in original_fingers)
    
    # Compare to AI's new geometry
    new_width = new_json['devices'][device_id]['geometry']['w']
    
    error = abs(total_original_width - new_width)
    if error < 0.001:
        print(f"PASS: {device_id} Geometry is Perfect!")
    else:
        print(f"FAIL: {device_id} Geometry is OFF by {error} units! (Expected {total_original_width}, Got {new_width})")

if __name__ == "__main__":
    import sys
    with open(sys.argv[1], 'r') as f1:
        old_j = json.load(f1)
    with open(sys.argv[2], 'r') as f2:
        new_j = json.load(f2)
        
    for dev in new_j['devices'].keys():
        verify_area(old_j, new_j, dev)

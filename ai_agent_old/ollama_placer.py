import json
import requests

def ollama_generate_placement(input_json, output_json, model="llama3.2"):
    # 1. Load the input graph
    try:
        with open(input_json, "r") as f:
            graph_data = json.load(f)
    except FileNotFoundError:
        print(f"Error: Input file {input_json} not found.")
        return

    # 2. Prepare the prompt
    prompt = f"""
    You are an expert VLSI placement engineer.
    
    Given this transistor graph:
    Nodes: {json.dumps(graph_data.get("nodes", []), indent=2)}
    Edges: {json.dumps(graph_data.get("edges", []), indent=2)}
    
    Generate an initial placement.
    Rules:
    - NMOS at y = 0
    - PMOS at y = 10
    - Even spacing
    - Reduce net crossings
    - No overlapping devices
    
    Respond with a JSON object containing a "placements" array. Each object in the array must have "id", "x", "y", and "orientation" (default "R0").
    """

    # 3. Send to Ollama using strict JSON format
    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "format": "json"  # Forces Ollama to output strict JSON
            }
        )
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        print("Error: Could not connect to Ollama. Is the server running locally on port 11434?")
        return
    
    # 4. Parse and save the result
    result = response.json()
    raw_text = result.get("response", "{}")

    try:
        placement_data = json.loads(raw_text)
        with open(output_json, "w") as f:
            json.dump(placement_data, f, indent=4)
        print(f"Success! Placement saved to: {output_json}")
    except json.JSONDecodeError:
        print("Error: The model failed to return valid JSON.")
        print("Raw output:", raw_text)

# Example usage:
# ollama_generate_placement("input.json", "output.json")
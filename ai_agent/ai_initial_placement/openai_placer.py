import os
import json
from openai import OpenAI


def llm_generate_placement(input_json, output_json):

    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise ValueError("OPENAI_API_KEY not set")

    client = OpenAI(api_key=api_key)

    with open(input_json) as f:
        graph_data = json.load(f)

    prompt = f"""
You are an expert VLSI placement engineer.

Generate an initial transistor placement.

Nodes:
{json.dumps(graph_data["nodes"], indent=2)}

Return ONLY JSON in this format:

{{
  "placements": [
    {{
      "id": "...",
      "x": number,
      "y": number,
      "orientation": "R0"
    }}
  ]
}}
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )

    output_text = response.choices[0].message.content

    placement = json.loads(output_text)

    with open(output_json, "w") as f:
        json.dump(placement, f, indent=4)

    print("Placement saved.")
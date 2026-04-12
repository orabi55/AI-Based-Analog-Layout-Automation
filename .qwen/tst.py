from openai import OpenAI

client = OpenAI(
  base_url="https://api.openrouter.ai/api/v1",
  api_key="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", 
)

try:
    print("--- Testing OpenRouter Free Tier ---")
    
    completion = client.chat.completions.create(
      # IMPORTANT: You must add ':free' to the model name
      model="qwen/qwen3-coder:free", 
      messages=[
        {"role": "system", "content": "You are an expert in Analog IC Layout Automation."},
        {"role": "user", "content": "Write a Python snippet for a simple transistor placement script."}
      ]
    )
    
    print(f"Response:\n{completion.choices[0].message.content}")

except Exception as e:
    print(f"Error: {e}")
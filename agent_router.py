from openai import OpenAI

# Initialize with a custom User-Agent to bypass strict WAF blocks
client = OpenAI(
    base_url="https://agentrouter.org/v1",
    api_key="sk-f0PnzCip5u0sO5lPoVbgIG1Ml1Q3CsT7pEMEik0st6ZLtc4C", # Paste your brand new key right here
    default_headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
)

try:
    print("Sending request to Agent Router...")
    
    # Make the request using DeepSeek
    response = client.chat.completions.create(
        model="deepseek-v3.1",
        messages=[{"role": "user", "content": "Hello! Are you receiving this?"}]
    )
    
    print("\nSUCCESS! The AI says:")
    print(response.choices[0].message.content)

except Exception as e:
    print(f"\nFAILED! Error details:\n{e}")
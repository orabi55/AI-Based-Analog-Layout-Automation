import os
from dotenv import load_dotenv
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
import google.genai as genai

print(f"Key loaded: {bool(api_key)}")
client = genai.Client(api_key=api_key)
print("Client created. Requesting generation...")

try:
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents='Say hello world',
    )
    print("Response received:", response.text)
except Exception as e:
    print("Error:", repr(e))

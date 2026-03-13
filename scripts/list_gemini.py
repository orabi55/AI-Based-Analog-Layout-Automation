import os
from dotenv import load_dotenv

load_dotenv()
from google import genai
import google.genai.errors

try:
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    for m in client.models.list():
        if "gemma" in m.name.lower():
            print(m.name)
except Exception as e:
    print(f"Error: {e}")

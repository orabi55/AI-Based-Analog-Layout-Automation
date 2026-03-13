import os
from dotenv import load_dotenv

load_dotenv()
from ai_agent.llm_worker import run_llm

import ai_agent.llm_worker
# Temporarily patch print to also show the errors
original_print = print

def custom_print(*args, **kwargs):
    original_print(*args, **kwargs)
    if args and "All models failed" in str(args[0]):
        try:
            from ai_agent.llm_worker import _gemini_key_invalid
            original_print("Gemini key invalid flag:", _gemini_key_invalid)
        except Exception:
            pass

try:
    print("Testing LLM generation to see exact errors...")
    # we can just run the loop over models ourselves to see the exact error:
    from google import genai
    from google.genai import types as genai_types
    api_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)
    for model in ["gemma-3-27b", "gemini-2.0-flash"]:
        try:
            print(f"Testing direct call to {model}...")
            response = client.models.generate_content(
                model=model,
                contents="TEST_OK",
            )
            print(f"Success for {model}: {response.text}")
        except Exception as e:
            print(f"Exception for {model}: {type(e)} {e}")
            
except Exception as e:
    print(f"Top-level Error: {e}")

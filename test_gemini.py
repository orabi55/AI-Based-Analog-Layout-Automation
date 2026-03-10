import os
from dotenv import load_dotenv

load_dotenv()
from ai_agent.llm_worker import run_llm

try:
    print("Testing LLM generation with gemma-3-27b-it...")
    messages = [{"role": "user", "content": "Hello! Reply with exactly 'TEST_OK'."}]
    
    # We pass the same prompt to full_prompt for testing
    reply = run_llm(messages, "Hello! Reply with exactly 'TEST_OK'.")
    print(f"Reply: {reply}")
except Exception as e:
    print(f"Error: {e}")

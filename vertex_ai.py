from google import genai
from google.genai import types
client = genai.Client(
    vertexai=True, 
    project="project-03484c74-0ab0-4f9e-b48", 
    location="global",
)

print("--- Available Vertex AI Models ---")
try:
    for m in client.models.list():
        methods = getattr(m, 'supported_generation_methods', [])
        print(f"Model ID: {m.name}")
        print(f"  Display Name: {getattr(m, 'display_name', 'N/A')}")
        print(f"  Supported Methods: {', '.join(methods) if methods else 'None'}")
        print("-" * 30)
except Exception as e:
    print(f"Error listing models: {e}")

print("\n--- Running Generation Test ---\n")
# If your image is stored in Google Cloud Storage, you can use the from_uri class method to create a Part object.
IMAGE_URI = "gs://generativeai-downloads/images/scones.jpg"
model = "gemini-3.1-pro-preview"
response = client.models.generate_content(
 model=model,
 contents=[
   "What is shown in this image?",
   types.Part.from_uri(
     file_uri=IMAGE_URI,
     mime_type="image/png",
   ),
 ],
)
print(response.text, end="")
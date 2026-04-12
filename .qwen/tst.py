import google.generativeai as genai

# Replace with your Gemini API key
API_KEY = "AIzaSyA9f5nQRfW8qvPDZjXHaoXf_Wnu7oGI7Sc"

genai.configure(api_key=API_KEY)

try:
    print("🔍 Fetching available models...\n")

    models = genai.list_models()

    for model in models:
        print("📌 Model Name:", model.name)
        print("   Display Name:", model.display_name)
        print("   Description:", model.description)
        print("   Supported Methods:", model.supported_generation_methods)
        print("-" * 50)

    print("\n✅ Done!")

except Exception as e:
    print("❌ Error occurred!")
    print("Error:", str(e))
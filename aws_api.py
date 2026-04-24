import boto3
import json

# 1. Setup Credentials
# Replace with the keys you created in the IAM console
ACCESS_KEY = ""
SECRET_KEY = ""
REGION = "us-east-1" # e.g., us-east-1 or us-west-2

def test_aws_bedrock():
    # Initialize the Bedrock clients
    # 'bedrock' is for managing models/listing
    # 'bedrock-runtime' is for actually running the AI
    bedrock = boto3.client(
        service_name='bedrock',
        region_name=REGION,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY
    )
    
    runtime = boto3.client(
        service_name='bedrock-runtime',
        region_name=REGION,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY
    )

    print("--- 1. Listing Available Foundation Models ---")
    try:
        response = bedrock.list_foundation_models()
        for model in response['modelSummaries']:
            # Filter for text models to keep the list clean
            if 'TEXT' in model['inputModalities']:
                print(f"Model Name: {model['modelName']} | ID: {model['modelId']}")
    except Exception as e:
        print(f"Error listing models: {e}")

    print("\n--- 2. Testing Claude 3.5 Sonnet ---")
    # This is the model ID for Claude 3.5 Sonnet
    model_id = "qwen.qwen3-coder-30b-a3b-v1:0"
    
    prompt_data = "Briefly explain why common-centroid layout is used for differential pairs."
    
    # Bedrock requires the body to be a JSON string
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 500,
        "messages": [
            {"role": "user", "content": prompt_data}
        ]
    })

    try:
        response = runtime.invoke_model(
            body=body,
            modelId=model_id,
            accept='application/json',
            contentType='application/json'
        )
        
        response_body = json.loads(response.get('body').read())
        print("AI Response:")
        print(response_body.get('content')[0].get('text'))
        
    except Exception as e:
        print(f"Error invoking model: {e}")
        print("\nTIP: Make sure you have 'granted access' to Claude in the AWS Bedrock Console under 'Model Access'.")

if __name__ == "__main__":
    test_aws_bedrock()
#!/usr/bin/env python
"""Test summarizer model directly."""
import os
from openai import OpenAI, AuthenticationError, APIStatusError

key = os.getenv('OPENAI_API_KEY', '').strip()
base_url = os.getenv('KRYTH_BASE_URL', 'https://integrate.api.nvidia.com/v1')

print(f"Testing summarizer model with key: {key[:10]}...")
print(f"Base URL: {base_url}")

client = OpenAI(api_key=key, base_url=base_url, timeout=30)

model = 'stepfun-ai/step-3.5-flash'
print(f"\nCalling chat.completions with model: {model}")

try:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {'role': 'system', 'content': 'You are a helpful assistant.'},
            {'role': 'user', 'content': 'Say hello in one word.'}
        ],
        max_tokens=10,
        temperature=0
    )
    print("Success! Response:", resp.choices[0].message.content)
except AuthenticationError as e:
    print("AuthenticationError:", e)
    print("This means the API key is invalid or doesn't have access to the model.")
except APIStatusError as e:
    print(f"APIStatusError: {e.status_code} - {e.message}")
    print("Response body:", e.response.text if e.response else "No response")
except Exception as e:
    print("Other error:", type(e).__name__, str(e))
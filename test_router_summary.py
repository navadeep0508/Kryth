#!/usr/bin/env python
"""Test the summarizer model via the model_config router."""
import os
import sys

# Ensure we use the same key and base_url
os.environ['OPENAI_API_KEY'] = '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'
os.environ['KRYTH_BASE_URL'] = 'https://integrate.api.nvidia.com/v1'

# Add src to path
sys.path.insert(0, 'kryth/src')

from agent.model_config.router import get_llm

print("Getting LLM for 'summary' role...")
client, model = get_llm('summary')
print(f"Client base_url: {client.base_url}")
print(f"Model: {model}")

print("\nMaking a test chat completion...")
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
except Exception as e:
    print("Error:", type(e).__name__, str(e))
    import traceback
    traceback.print_exc()
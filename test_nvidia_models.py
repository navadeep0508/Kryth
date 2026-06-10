#!/usr/bin/env python
"""Test NVIDIA API key and available models."""
import os
from openai import OpenAI, AuthenticationError

key = os.getenv('OPENAI_API_KEY', '').strip()
base_url = os.getenv('KRYTH_BASE_URL', 'https://integrate.api.nvidia.com/v1')

print(f"Testing NVIDIA API key: {key[:10]}...")
print(f"Base URL: {base_url}")

client = OpenAI(api_key=key, base_url=base_url, timeout=30)

try:
    print("\nFetching models list...")
    models = client.models.list()
    model_ids = [m.id for m in models.data]
    print(f"Total models: {len(model_ids)}")
    print("\nFirst 30 models:")
    for mid in model_ids[:30]:
        print(f"  - {mid}")
    
    # Check for stepfun models
    stepfun_models = [m for m in model_ids if 'stepfun' in m.lower()]
    if stepfun_models:
        print(f"\nStepFun models available: {stepfun_models[:10]}")
    else:
        print("\nNo StepFun models found!")
        
    # Check for nvidia models
    nvidia_models = [m for m in model_ids if 'nvidia' in m.lower() or 'llama' in m.lower() or 'mistral' in m.lower()]
    print(f"\nNVIDIA/Meta/Mistral models (first 10): {nvidia_models[:10]}")
    
except AuthenticationError as e:
    print(f"\nAuthentication Error: {e}")
    print("The API key is invalid or doesn't have access to this endpoint.")
except Exception as e:
    print(f"\nOther error: {type(e).__name__}: {e}")
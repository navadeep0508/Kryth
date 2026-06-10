#!/usr/bin/env python
"""Test what load_config actually returns."""
import sys
import os

# Ensure we use the same environment
print("Environment variables:")
print("  OPENAI_API_KEY:", os.getenv('OPENAI_API_KEY', '')[:10] if os.getenv('OPENAI_API_KEY') else 'None')
print("  KRYTH_BASE_URL:", os.getenv('KRYTH_BASE_URL', 'None'))

sys.path.insert(0, 'kryth/src')
from agent.model_config.loader import load_config

cfg = load_config()
print("\nLoaded config:")
print("  mode:", cfg.mode)
print("  api_key:", getattr(cfg, 'api_key', None))
print("  base_url:", getattr(cfg, 'base_url', None))
print("  provider:", getattr(cfg, 'provider', None))
print("  model:", getattr(cfg, 'model', None))
print("  providers:", getattr(cfg, 'providers', {}))
print("  models:", getattr(cfg, 'models', {}))

# Now test get_llm
from agent.model_config.router import get_llm
print("\nGetting LLM for 'summary':")
client, model = get_llm('summary')
print("  client.base_url:", client.base_url)
print("  model:", model)
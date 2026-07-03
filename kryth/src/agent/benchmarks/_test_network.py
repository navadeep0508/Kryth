import os
from agent.llm import ask_llm_stream, MAIN_MODEL, BASE_URL, _make_client

c = _make_client()
print(f"Base URL: {c.base_url}")
print(f"API Key: {c.api_key[:10]}...{c.api_key[-4:]}")

# Check what model_router does
try:
    from agent.model_router import pick_main_model, RouteHints
    hints = RouteHints(payload_chars=10, has_tool_specs=False)
    model = pick_main_model(hints)
    print(f"Router picked: {model}")
except Exception as e:
    print(f"Router error: {e}")

# Check model_config
try:
    from agent.model_config.router import get_client, get_llm
    c2 = get_client("main")
    print(f"MC client: {c2}")
    llm_info = get_llm("main")
    print(f"MC LLM: {llm_info}")
except Exception as e:
    print(f"MC error: {e}")

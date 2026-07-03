import os
print("OPENAI_API_KEY:", repr(os.getenv("OPENAI_API_KEY", "")))
print("KRYTH_BASE_URL:", repr(os.getenv("KRYTH_BASE_URL", "")))
print("KRYTH_MAIN_MODEL:", repr(os.getenv("KRYTH_MAIN_MODEL", "")))

from agent.llm import MAIN_MODEL, BASE_URL
print("MAIN_MODEL:", MAIN_MODEL)
print("BASE_URL:", BASE_URL)

from agent.env import getenv
print("getenv KRYTH_MAIN_MODEL:", getenv("KRYTH_MAIN_MODEL", "default"))
print("getenv KRYTH_BASE_URL:", getenv("KRYTH_BASE_URL", "default"))

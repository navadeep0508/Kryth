"""Provider registry — maps model identifiers to provider profiles.

Every known provider is registered here. Unknown providers fall back
to the heuristic detector in fallback_adapter.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Provider(str, Enum):
    ANTHROPIC   = "anthropic"
    OPENAI      = "openai"
    GEMINI      = "gemini"
    DEEPSEEK    = "deepseek"
    QWEN        = "qwen"
    KIMI        = "kimi"
    STEPFUN     = "stepfun"
    LLAMA       = "llama"
    MISTRAL     = "mistral"
    GLM         = "glm"
    GROK        = "grok"
    OLLAMA      = "ollama"
    OPENROUTER  = "openrouter"
    NVIDIA      = "nvidia"
    GROQ        = "groq"
    UNKNOWN     = "unknown"


# Model name prefix / substring → provider
_MODEL_MAP: list[tuple[str, Provider]] = [
    # Anthropic
    ("claude",          Provider.ANTHROPIC),
    # OpenAI
    ("gpt-",            Provider.OPENAI),
    ("o1",              Provider.OPENAI),
    ("o3",              Provider.OPENAI),
    ("o4",              Provider.OPENAI),
    ("chatgpt",         Provider.OPENAI),
    ("text-davinci",    Provider.OPENAI),
    ("codex",           Provider.OPENAI),
    # Gemini
    ("gemini",          Provider.GEMINI),
    ("palm",            Provider.GEMINI),
    # DeepSeek
    ("deepseek",        Provider.DEEPSEEK),
    # Qwen
    ("qwen",            Provider.QWEN),
    ("qwq",             Provider.QWEN),
    # Kimi
    ("moonshot",        Provider.KIMI),
    ("kimi",            Provider.KIMI),
    # StepFun
    ("step-",           Provider.STEPFUN),
    ("stepfun",         Provider.STEPFUN),
    # Llama
    ("llama",           Provider.LLAMA),
    ("meta-llama",      Provider.LLAMA),
    ("codellama",       Provider.LLAMA),
    # Mistral
    ("mistral",         Provider.MISTRAL),
    ("mixtral",         Provider.MISTRAL),
    ("codestral",       Provider.MISTRAL),
    # GLM
    ("glm",             Provider.GLM),
    ("chatglm",         Provider.GLM),
    # Grok
    ("grok",            Provider.GROK),
    # Ollama (base_url clue)
    ("ollama",          Provider.OLLAMA),
    # Groq
    ("groq",            Provider.GROQ),
    # NVIDIA
    ("nvidia",          Provider.NVIDIA),
    ("nim/",            Provider.NVIDIA),
]

# base_url substring → provider
_URL_MAP: list[tuple[str, Provider]] = [
    ("api.anthropic.com",   Provider.ANTHROPIC),
    ("api.openai.com",      Provider.OPENAI),
    ("generativelanguage",  Provider.GEMINI),
    ("api.deepseek.com",    Provider.DEEPSEEK),
    ("dashscope",           Provider.QWEN),
    ("api.moonshot",        Provider.KIMI),
    ("api.stepfun",         Provider.STEPFUN),
    ("api.mistral.ai",      Provider.MISTRAL),
    ("api.together",        Provider.LLAMA),
    ("openrouter.ai",       Provider.OPENROUTER),
    ("api.groq.com",        Provider.GROQ),
    ("integrate.api.nvidia", Provider.NVIDIA),
    ("localhost:11434",     Provider.OLLAMA),
    ("127.0.0.1:11434",     Provider.OLLAMA),
    ("ollama",              Provider.OLLAMA),
    ("x.ai",                Provider.GROK),
    ("api.x.ai",            Provider.GROK),
]


def detect_provider(model_name: str = "", base_url: str = "") -> Provider:
    """Infer the provider from model name and/or base URL.

    URL takes priority over model name so that e.g. running llama3 through
    an Ollama server is correctly identified as OLLAMA.
    """
    name = (model_name or "").lower()
    url = (base_url or "").lower()

    # Check URL first — more specific than model name
    for substring, provider in _URL_MAP:
        if substring in url:
            return provider

    for prefix, provider in _MODEL_MAP:
        if prefix in name:
            return provider

    return Provider.UNKNOWN


def is_openai_compatible(provider: Provider) -> bool:
    """Return True if this provider uses an OpenAI-compatible API."""
    return provider not in (Provider.ANTHROPIC, Provider.GEMINI)

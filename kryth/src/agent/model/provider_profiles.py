"""Provider profiles — per-provider parsing configuration.

Each profile defines:
- Which tags contain reasoning content
- Which tags contain tool calls
- Which fields in the stream delta carry content/tool data
- How to extract tool name + arguments
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.model.provider_registry import Provider


@dataclass(frozen=True)
class ProviderProfile:
    """Parsing configuration for one provider."""
    provider: Provider

    # Tags that wrap chain-of-thought (stripped from UI output)
    reasoning_tags: tuple[str, ...] = ()

    # Tags that wrap tool calls in XML-format responses
    tool_tags: tuple[str, ...] = ()

    # How tool calls arrive in streaming delta
    # "openai"     → delta.tool_calls[].function.{name,arguments}
    # "anthropic"  → content block type=tool_use
    # "xml"        → parse <tool_call> / <function> XML blocks
    # "text"       → heuristic regex on plain text
    tool_format: str = "openai"

    # Tag used for hidden reasoning tokens (Anthropic thinking)
    thinking_tag: str = ""

    # Whether content arrives in delta.content (str) or delta.content[].text
    content_as_list: bool = False

    # Whether the provider sends usage as a separate final chunk
    usage_chunk: bool = True


_PROFILES: dict[Provider, ProviderProfile] = {
    Provider.ANTHROPIC: ProviderProfile(
        provider=Provider.ANTHROPIC,
        reasoning_tags=("<thinking>", "</thinking>"),
        tool_tags=("<tool_use>", "</tool_use>", "<tool_call>", "</tool_call>"),
        tool_format="xml",
        thinking_tag="thinking",
        content_as_list=True,
        usage_chunk=True,
    ),
    Provider.OPENAI: ProviderProfile(
        provider=Provider.OPENAI,
        tool_format="openai",
        usage_chunk=True,
    ),
    Provider.GEMINI: ProviderProfile(
        provider=Provider.GEMINI,
        tool_format="openai",   # Gemini uses OpenAI-compatible client
        usage_chunk=True,
    ),
    Provider.DEEPSEEK: ProviderProfile(
        provider=Provider.DEEPSEEK,
        reasoning_tags=("<think>", "</think>", "<reasoning>", "</reasoning>"),
        tool_format="openai",
        usage_chunk=True,
    ),
    Provider.QWEN: ProviderProfile(
        provider=Provider.QWEN,
        reasoning_tags=("<think>", "</think>", "<thinking>", "</thinking>"),
        tool_format="openai",
        usage_chunk=True,
    ),
    Provider.KIMI: ProviderProfile(
        provider=Provider.KIMI,
        tool_format="openai",
        usage_chunk=True,
    ),
    Provider.STEPFUN: ProviderProfile(
        provider=Provider.STEPFUN,
        reasoning_tags=("<think>", "</think>"),
        tool_format="openai",
        usage_chunk=True,
    ),
    Provider.LLAMA: ProviderProfile(
        provider=Provider.LLAMA,
        reasoning_tags=("<think>", "</think>"),
        tool_format="openai",
        usage_chunk=False,
    ),
    Provider.MISTRAL: ProviderProfile(
        provider=Provider.MISTRAL,
        tool_format="openai",
        usage_chunk=True,
    ),
    Provider.GLM: ProviderProfile(
        provider=Provider.GLM,
        reasoning_tags=("<think>", "</think>"),
        tool_format="openai",
        usage_chunk=True,
    ),
    Provider.GROK: ProviderProfile(
        provider=Provider.GROK,
        reasoning_tags=("<think>", "</think>"),
        tool_format="openai",
        usage_chunk=True,
    ),
    Provider.OLLAMA: ProviderProfile(
        provider=Provider.OLLAMA,
        reasoning_tags=("<think>", "</think>"),
        tool_format="text",
        usage_chunk=False,
    ),
    Provider.OPENROUTER: ProviderProfile(
        provider=Provider.OPENROUTER,
        reasoning_tags=("<think>", "</think>"),
        tool_format="openai",
        usage_chunk=True,
    ),
    Provider.NVIDIA: ProviderProfile(
        provider=Provider.NVIDIA,
        tool_format="openai",
        usage_chunk=True,
    ),
    Provider.GROQ: ProviderProfile(
        provider=Provider.GROQ,
        tool_format="openai",
        usage_chunk=True,
    ),
}

# Fallback profile for unknown providers
_FALLBACK = ProviderProfile(
    provider=Provider.UNKNOWN,
    reasoning_tags=("<think>", "</think>", "<thinking>", "</thinking>",
                    "<reasoning>", "</reasoning>", "<analysis>", "</analysis>"),
    tool_format="text",
    usage_chunk=False,
)


def get_profile(provider: Provider) -> ProviderProfile:
    """Return the ProviderProfile for *provider*, falling back to UNKNOWN."""
    return _PROFILES.get(provider, _FALLBACK)


def all_reasoning_open_tags() -> frozenset[str]:
    """Return all opening reasoning tags across all providers."""
    tags: set[str] = set()
    for profile in list(_PROFILES.values()) + [_FALLBACK]:
        for tag in profile.reasoning_tags:
            if not tag.startswith("</"):
                tags.add(tag)
    return frozenset(tags)


def all_reasoning_close_tags() -> frozenset[str]:
    tags: set[str] = set()
    for profile in list(_PROFILES.values()) + [_FALLBACK]:
        for tag in profile.reasoning_tags:
            if tag.startswith("</"):
                tags.add(tag)
    return frozenset(tags)

"""CapabilityDetector — automatically detect what a model can do.

Capabilities are determined from:
1. Model name heuristics (known capability signatures)
2. Provider profile defaults
3. Runtime probing (optional, not used by default)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.model.provider_registry import Provider


@dataclass
class ModelCapabilities:
    """Detected capabilities for a model."""
    reasoning: bool = False           # Has chain-of-thought / extended thinking
    hidden_reasoning: bool = False    # Reasoning tokens are hidden (Anthropic thinking)
    streaming: bool = True            # Supports streaming responses
    tool_calling: bool = True         # Supports function / tool calls
    parallel_tools: bool = False      # Can call multiple tools in one turn
    vision: bool = False              # Accepts image inputs
    structured_output: bool = False   # Supports JSON mode / structured output
    json_tools: bool = False          # Uses JSON-format tool calls (OpenAI style)
    xml_tools: bool = False           # Uses XML-format tool calls (Anthropic style)
    thinking_tokens: bool = False     # Has explicit thinking_tokens parameter
    image_generation: bool = False    # Can generate images
    context_length: int = 8_192       # Max context window (tokens)

    def to_dict(self) -> dict:
        return {
            "reasoning": self.reasoning,
            "hidden_reasoning": self.hidden_reasoning,
            "streaming": self.streaming,
            "tool_calling": self.tool_calling,
            "parallel_tools": self.parallel_tools,
            "vision": self.vision,
            "structured_output": self.structured_output,
            "json_tools": self.json_tools,
            "xml_tools": self.xml_tools,
            "thinking_tokens": self.thinking_tokens,
            "image_generation": self.image_generation,
            "context_length": self.context_length,
        }


# Provider-level defaults
_PROVIDER_DEFAULTS: dict[Provider, ModelCapabilities] = {
    Provider.ANTHROPIC: ModelCapabilities(
        reasoning=True, hidden_reasoning=False,
        streaming=True, tool_calling=True,
        xml_tools=True, parallel_tools=False,
        context_length=200_000,
    ),
    Provider.OPENAI: ModelCapabilities(
        reasoning=False, streaming=True,
        tool_calling=True, json_tools=True,
        parallel_tools=True, structured_output=True,
        context_length=128_000,
    ),
    Provider.GEMINI: ModelCapabilities(
        reasoning=False, streaming=True,
        tool_calling=True, json_tools=True,
        vision=True, context_length=1_000_000,
    ),
    Provider.DEEPSEEK: ModelCapabilities(
        reasoning=True, streaming=True,
        tool_calling=True, json_tools=True,
        context_length=64_000,
    ),
    Provider.QWEN: ModelCapabilities(
        reasoning=True, streaming=True,
        tool_calling=True, json_tools=True,
        context_length=128_000,
    ),
    Provider.KIMI: ModelCapabilities(
        reasoning=False, streaming=True,
        tool_calling=True, json_tools=True,
        context_length=128_000,
    ),
    Provider.LLAMA: ModelCapabilities(
        reasoning=False, streaming=True,
        tool_calling=True, json_tools=True,
        context_length=128_000,
    ),
    Provider.MISTRAL: ModelCapabilities(
        reasoning=False, streaming=True,
        tool_calling=True, json_tools=True,
        parallel_tools=True, context_length=128_000,
    ),
    Provider.GLM: ModelCapabilities(
        reasoning=True, streaming=True,
        tool_calling=True, json_tools=True,
        context_length=128_000,
    ),
    Provider.GROK: ModelCapabilities(
        reasoning=True, streaming=True,
        tool_calling=True, json_tools=True,
        context_length=128_000,
    ),
    Provider.OLLAMA: ModelCapabilities(
        reasoning=False, streaming=True,
        tool_calling=False, context_length=8_192,
    ),
    Provider.OPENROUTER: ModelCapabilities(
        reasoning=False, streaming=True,
        tool_calling=True, json_tools=True,
        context_length=128_000,
    ),
    Provider.NVIDIA: ModelCapabilities(
        reasoning=False, streaming=True,
        tool_calling=True, json_tools=True,
        context_length=128_000,
    ),
    Provider.GROQ: ModelCapabilities(
        reasoning=False, streaming=True,
        tool_calling=True, json_tools=True,
        context_length=128_000,
    ),
    Provider.STEPFUN: ModelCapabilities(
        reasoning=False, streaming=True,
        tool_calling=True, json_tools=True,
        context_length=256_000,
    ),
}

# Model-name fragment → capability patches
_MODEL_PATCHES: list[tuple[str, dict]] = [
    # Anthropic extended thinking
    ("claude-3-5", {"thinking_tokens": False, "reasoning": False}),
    ("claude-3-7", {"thinking_tokens": True, "reasoning": True, "hidden_reasoning": True}),
    ("claude-opus-4", {"thinking_tokens": True, "reasoning": True, "hidden_reasoning": True}),
    # OpenAI o-series reasoning
    ("o1",   {"reasoning": True, "hidden_reasoning": True}),
    ("o3",   {"reasoning": True, "hidden_reasoning": True}),
    ("o4",   {"reasoning": True, "hidden_reasoning": True}),
    # GPT-4o vision
    ("gpt-4o", {"vision": True, "parallel_tools": True}),
    # DeepSeek-R1 reasoning
    ("deepseek-r1", {"reasoning": True}),
    ("deepseek-reasoner", {"reasoning": True}),
    # QwQ reasoning
    ("qwq", {"reasoning": True}),
    # Llama 3 tool calling
    ("llama-3", {"tool_calling": True, "json_tools": True}),
    # Gemini vision
    ("gemini-pro-vision", {"vision": True}),
    ("gemini-1.5", {"vision": True, "context_length": 1_000_000}),
    ("gemini-2", {"vision": True, "context_length": 2_000_000}),
    # Grok reasoning
    ("grok-3", {"reasoning": True}),
]


class CapabilityDetector:
    """Detect model capabilities from name and provider."""

    def detect(self, model_name: str, provider: Provider) -> ModelCapabilities:
        """Return capabilities for *model_name* under *provider*."""
        caps = _PROVIDER_DEFAULTS.get(provider, ModelCapabilities())
        # Deep-copy via dataclass replace workaround
        caps = ModelCapabilities(**caps.to_dict())

        # Apply model-specific patches
        name = (model_name or "").lower()
        for fragment, patches in _MODEL_PATCHES:
            if fragment in name:
                for attr, val in patches.items():
                    setattr(caps, attr, val)

        return caps

    def from_model_id(self, model_name: str, base_url: str = "") -> ModelCapabilities:
        """One-shot helper: detect provider then capabilities."""
        from agent.model.provider_registry import detect_provider
        provider = detect_provider(model_name, base_url)
        return self.detect(model_name, provider)


_detector = CapabilityDetector()


def detect_capabilities(model_name: str, base_url: str = "") -> ModelCapabilities:
    """Module-level shortcut."""
    return _detector.from_model_id(model_name, base_url)

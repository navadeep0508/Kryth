"""KRYTH Universal Model Adapter.

Zero provider leakage — every model produces the same NormalizedChunk stream.

Quick start::

    from agent.model import ModelAdapter

    adapter = ModelAdapter.for_model("claude-opus-4-8")
    caps = adapter.capabilities
    print(caps.reasoning, caps.tool_calling)

    for chunk in adapter.normalize_stream(raw_llm_stream):
        if chunk.is_text:
            display(chunk.text)
        elif chunk.is_reasoning:
            show_thinking(chunk.reasoning)
        elif chunk.is_tool:
            run_tool(chunk.tool_name, chunk.tool_args)
"""

from agent.model.model_adapter import ModelAdapter
from agent.model.provider_registry import Provider, detect_provider, is_openai_compatible
from agent.model.provider_profiles import ProviderProfile, get_profile
from agent.model.capability_detector import ModelCapabilities, detect_capabilities
from agent.model.stream_normalizer import StreamNormalizer, NormalizedChunk, ChunkType
from agent.model.reasoning_detector import ReasoningDetector, ReasoningBlock, extract_reasoning
from agent.model.tool_parser import ToolParser, ParsedToolCall
from agent.model.response_parser import ResponseParser, clean_output, split_response
from agent.model.fallback_adapter import FallbackAdapter

__all__ = [
    "ModelAdapter",
    "Provider",
    "detect_provider",
    "is_openai_compatible",
    "ProviderProfile",
    "get_profile",
    "ModelCapabilities",
    "detect_capabilities",
    "StreamNormalizer",
    "NormalizedChunk",
    "ChunkType",
    "ReasoningDetector",
    "ReasoningBlock",
    "extract_reasoning",
    "ToolParser",
    "ParsedToolCall",
    "ResponseParser",
    "clean_output",
    "split_response",
    "FallbackAdapter",
]

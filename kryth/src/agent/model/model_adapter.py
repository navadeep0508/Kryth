"""ModelAdapter — unified interface for all LLM providers.

This is the main entry point for the Universal Model Pipeline.

Usage::

    adapter = ModelAdapter.for_model("claude-opus-4-8")
    for chunk in adapter.normalize_stream(raw_stream):
        if chunk.is_text:
            ui_emit_text(chunk.text)
        elif chunk.is_reasoning:
            ui_emit_reasoning(chunk.reasoning)
        elif chunk.is_tool:
            dispatch_tool(chunk.tool_name, chunk.tool_args)

Every model produces the same chunk types. The UI never inspects
provider-specific fields.
"""

from __future__ import annotations

import logging
from typing import Any, Iterator

from agent.model.capability_detector import ModelCapabilities, detect_capabilities
from agent.model.provider_profiles import ProviderProfile, get_profile
from agent.model.provider_registry import Provider, detect_provider, is_openai_compatible
from agent.model.reasoning_detector import ReasoningDetector, ReasoningPhase
from agent.model.response_parser import ResponseParser
from agent.model.stream_normalizer import ChunkType, NormalizedChunk, StreamNormalizer
from agent.model.tool_parser import ParsedToolCall, ToolParser
from agent.model.fallback_adapter import FallbackAdapter

logger = logging.getLogger(__name__)


class ModelAdapter:
    """Unified model adapter — normalizes any provider stream.

    Parameters
    ----------
    model_name:
        The model identifier (e.g. ``"claude-opus-4-8"``, ``"gpt-4o"``).
    base_url:
        Provider API base URL (used for provider detection).
    """

    def __init__(self, model_name: str = "", base_url: str = "") -> None:
        self.model_name = model_name
        self.base_url = base_url

        self.provider = detect_provider(model_name, base_url)
        self.capabilities = detect_capabilities(model_name, base_url)
        self.profile = get_profile(self.provider)

        self._rd = ReasoningDetector()
        self._normalizer = StreamNormalizer(self._rd)
        self._tool_parser = ToolParser()
        self._response_parser = ResponseParser()
        self._fallback = FallbackAdapter()

        logger.debug(
            "ModelAdapter: %s → provider=%s reasoning=%s tools=%s",
            model_name, self.provider.value,
            self.capabilities.reasoning,
            self.capabilities.tool_calling,
        )

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def for_model(cls, model_name: str, base_url: str = "") -> "ModelAdapter":
        """Create an adapter for a specific model."""
        return cls(model_name=model_name, base_url=base_url)

    @classmethod
    def from_env(cls) -> "ModelAdapter":
        """Create an adapter from KRYTH environment config."""
        try:
            from agent.model_config.router import get_llm
            _client, model_name = get_llm("main")
        except Exception:
            import os
            model_name = os.environ.get("KRYTH_MAIN_MODEL", "")
        base_url = ""
        try:
            import os
            base_url = os.environ.get("KRYTH_BASE_URL", "")
        except Exception:
            pass
        return cls(model_name=model_name, base_url=base_url)

    # ------------------------------------------------------------------
    # Stream normalization
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear per-stream state (reasoning detector + tool buffers).

        Call before reusing a cached adapter on a NEW stream when driving it
        chunk-by-chunk via ``normalize_chunk`` (``normalize_stream`` resets
        automatically).
        """
        self._rd.reset()
        self._normalizer.reset()

    def normalize_stream(self, raw_stream: Any) -> Iterator[NormalizedChunk]:
        """Iterate over *raw_stream* and yield ``NormalizedChunk`` objects.

        Handles OpenAI, Anthropic, Ollama, and unknown formats.
        """
        self._rd.reset()
        self._normalizer.reset()

        try:
            for raw_chunk in raw_stream:
                yield from self._normalize_one(raw_chunk)
        except Exception as exc:
            logger.warning("ModelAdapter: stream error: %s", exc)
            yield NormalizedChunk(chunk_type=ChunkType.ERROR, error=str(exc))

    def normalize_chunk(self, raw_chunk: Any) -> list[NormalizedChunk]:
        """Normalize a single chunk (for non-iterator usage)."""
        return self._normalize_one(raw_chunk)

    def normalize_complete_response(self, response: Any) -> tuple[str, list[ParsedToolCall], str]:
        """Normalize a complete (non-streaming) response.

        Returns ``(clean_text, tool_calls, reasoning_text)``.
        """
        raw_text = ""
        tool_calls: list[ParsedToolCall] = []

        # OpenAI message
        choices = getattr(response, "choices", []) or []
        if choices:
            msg = getattr(choices[0], "message", None)
            if msg:
                raw_text = getattr(msg, "content", "") or ""
                tool_calls = self._tool_parser.from_openai_message(msg)
        elif isinstance(response, dict):
            raw_text = response.get("content") or response.get("text") or ""

        # Anthropic content blocks
        content = getattr(response, "content", None)
        if isinstance(content, list):
            for block in content:
                btype = getattr(block, "type", "")
                if btype == "text":
                    raw_text += getattr(block, "text", "")
                elif btype == "tool_use":
                    tool_calls += self._tool_parser.from_anthropic_content([block])

        # split_reasoning_and_response returns (reasoning, clean)
        reasoning_text, clean_text = self._response_parser.split_reasoning_and_response(raw_text)
        clean_text = self._response_parser.clean(clean_text)
        return clean_text, tool_calls, reasoning_text

    # ------------------------------------------------------------------
    # Tool call parsing
    # ------------------------------------------------------------------

    def parse_tool_calls_from_text(self, text: str) -> list[ParsedToolCall]:
        """Extract tool calls from raw text (for fallback providers)."""
        return self._tool_parser.from_any(text)

    # ------------------------------------------------------------------
    # Clean text
    # ------------------------------------------------------------------

    def clean_text(self, text: str) -> str:
        """Remove all internal markup from *text*."""
        return self._response_parser.clean(text)

    # ------------------------------------------------------------------
    # Synthetic reasoning (for non-reasoning models)
    # ------------------------------------------------------------------

    def synthetic_reasoning_for(self, tool_name: str) -> list[str]:
        """Return synthetic reasoning phase messages for a tool call."""
        if self.capabilities.reasoning:
            return []   # Model has real reasoning, don't synthesize
        return self._rd.synthetic_phases(tool_name)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _normalize_one(self, chunk: Any) -> list[NormalizedChunk]:
        try:
            if is_openai_compatible(self.provider) or self.provider == Provider.UNKNOWN:
                return self._normalizer.normalize_openai_chunk(chunk)
            elif isinstance(chunk, str):
                return self._normalizer.normalize_text_chunk(chunk)
            else:
                return self._fallback.normalize_chunk(chunk)
        except Exception as exc:
            logger.debug("ModelAdapter._normalize_one error: %s", exc)
            return self._fallback.normalize_chunk(chunk)

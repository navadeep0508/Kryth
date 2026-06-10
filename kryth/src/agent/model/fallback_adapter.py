"""FallbackAdapter — handle unknown models and formats gracefully.

When the provider is unknown or the format is unexpected:
1. Try all parsing strategies in sequence
2. Clean any visible internal markup
3. Always produce valid NormalizedChunks
4. Never crash — degrade gracefully

The UI should never break regardless of what the model outputs.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from agent.model.stream_normalizer import NormalizedChunk, ChunkType

logger = logging.getLogger(__name__)


class FallbackAdapter:
    """Last-resort adapter for unknown providers and formats."""

    def __init__(self) -> None:
        from agent.model.reasoning_detector import ReasoningDetector
        from agent.model.tool_parser import ToolParser
        from agent.model.response_parser import ResponseParser
        self._rd = ReasoningDetector()
        self._tp = ToolParser()
        self._rp = ResponseParser()

    def normalize_chunk(self, chunk: Any) -> list[NormalizedChunk]:
        """Attempt to normalize *chunk* using all available heuristics."""
        out: list[NormalizedChunk] = []

        # Try OpenAI format first
        try:
            if hasattr(chunk, "choices") or (isinstance(chunk, dict) and "choices" in chunk):
                return self._try_openai(chunk)
        except Exception as exc:
            logger.debug("FallbackAdapter: OpenAI parse failed: %s", exc)

        # Try dict with "content" key
        try:
            if isinstance(chunk, dict):
                return self._try_dict(chunk)
        except Exception as exc:
            logger.debug("FallbackAdapter: dict parse failed: %s", exc)

        # Try plain string
        if isinstance(chunk, str):
            return self._try_text(chunk)

        # Unknown — emit a safe no-op
        return out

    def normalize_text(self, text: str) -> str:
        """Clean *text* for display, removing all internal markup."""
        return self._rp.clean(text)

    def extract_tool_calls(self, text: str) -> list[dict]:
        """Try to extract any tool calls from raw text."""
        calls = self._tp.from_any(text)
        return [c.to_dict() for c in calls]

    # ------------------------------------------------------------------
    # Internal strategies
    # ------------------------------------------------------------------

    def _try_openai(self, chunk: Any) -> list[NormalizedChunk]:
        from agent.model.stream_normalizer import StreamNormalizer
        normalizer = StreamNormalizer(self._rd)
        return normalizer.normalize_openai_chunk(chunk)

    def _try_dict(self, d: dict) -> list[NormalizedChunk]:
        out: list[NormalizedChunk] = []
        content = d.get("content") or d.get("text") or d.get("message") or ""
        if isinstance(content, str) and content:
            return self._try_text(content)
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    t = block.get("text", "")
                    if t:
                        out.extend(self._try_text(t))
        return out

    def _try_text(self, text: str) -> list[NormalizedChunk]:
        out: list[NormalizedChunk] = []
        visible, reasoning = self._rd.feed(text)
        if reasoning:
            out.append(NormalizedChunk(chunk_type=ChunkType.REASONING, reasoning=reasoning))
        if visible:
            # Check for embedded tool calls
            tool_calls = self._tp.from_any(visible)
            if tool_calls:
                for tc in tool_calls:
                    out.append(NormalizedChunk(
                        chunk_type=ChunkType.TOOL_CALL,
                        tool_name=tc.name,
                        tool_args=tc.arguments,
                        raw=visible,
                    ))
                # Clean the text of tool markup
                clean = self._rp.clean(visible)
                if clean:
                    out.append(NormalizedChunk(chunk_type=ChunkType.TEXT, text=clean))
            else:
                clean = self._rp.clean(visible)
                if clean:
                    out.append(NormalizedChunk(chunk_type=ChunkType.TEXT, text=clean))
        return out

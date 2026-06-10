"""StreamNormalizer — convert any provider's streaming chunks to a uniform format.

Every provider's streaming delta becomes a ``NormalizedChunk`` so the UI
and the rest of the pipeline never need to know which model is running.

Pipeline position:
    Raw provider stream → StreamNormalizer → ReasoningDetector → UI Event Bus
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator


class ChunkType(str, Enum):
    TEXT       = "text"        # Visible response text
    REASONING  = "reasoning"   # Thinking / chain-of-thought (hidden from user)
    TOOL_CALL  = "tool_call"   # Tool invocation
    USAGE      = "usage"       # Token usage stats
    ERROR      = "error"       # Error signal
    DONE       = "done"        # Stream finished


@dataclass
class NormalizedChunk:
    chunk_type: ChunkType
    text: str = ""
    reasoning: str = ""
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_call_id: str = ""
    usage_in: int = 0
    usage_out: int = 0
    error: str = ""
    raw: Any = None

    @property
    def is_text(self) -> bool:
        return self.chunk_type == ChunkType.TEXT

    @property
    def is_reasoning(self) -> bool:
        return self.chunk_type == ChunkType.REASONING

    @property
    def is_tool(self) -> bool:
        return self.chunk_type == ChunkType.TOOL_CALL

    def to_dict(self) -> dict:
        return {
            "chunk_type": self.chunk_type.value,
            "text": self.text,
            "reasoning": self.reasoning,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "usage_in": self.usage_in,
            "usage_out": self.usage_out,
        }


class StreamNormalizer:
    """Normalize streaming chunks from any OpenAI-compatible provider.

    For providers that use OpenAI's streaming format, the delta structure is:
      chunk.choices[0].delta.content           → text
      chunk.choices[0].delta.tool_calls        → tool calls
      chunk.choices[0].delta.reasoning_content → reasoning (DeepSeek etc.)

    For reasoning tokens in the text stream (Qwen, Llama, Ollama):
      The ReasoningDetector handles tag extraction inline.
    """

    def __init__(self, reasoning_detector=None) -> None:
        from agent.model.reasoning_detector import ReasoningDetector
        self._rd = reasoning_detector or ReasoningDetector()
        self._tool_buffers: dict[int, dict] = {}  # index → partial tool call

    def normalize_openai_chunk(self, chunk: Any) -> list[NormalizedChunk]:
        """Process one OpenAI streaming chunk → list of NormalizedChunks."""
        out: list[NormalizedChunk] = []

        choices = getattr(chunk, "choices", []) or []
        if not choices:
            # Usage-only chunk
            usage = getattr(chunk, "usage", None)
            if usage:
                out.append(NormalizedChunk(
                    chunk_type=ChunkType.USAGE,
                    usage_in=getattr(usage, "prompt_tokens", 0),
                    usage_out=getattr(usage, "completion_tokens", 0),
                    raw=chunk,
                ))
            return out

        choice = choices[0]
        delta = getattr(choice, "delta", None)
        finish = getattr(choice, "finish_reason", None)

        if delta is None:
            if finish == "stop":
                out.append(NormalizedChunk(chunk_type=ChunkType.DONE, raw=chunk))
            return out

        # --- Reasoning content (DeepSeek, etc.) ---
        reasoning_content = getattr(delta, "reasoning_content", None)
        if reasoning_content:
            out.append(NormalizedChunk(
                chunk_type=ChunkType.REASONING,
                reasoning=reasoning_content,
                raw=chunk,
            ))

        # --- Text content ---
        content = getattr(delta, "content", None) or ""
        if content:
            visible, inline_reasoning = self._rd.feed(content)
            if inline_reasoning:
                out.append(NormalizedChunk(
                    chunk_type=ChunkType.REASONING,
                    reasoning=inline_reasoning,
                    raw=chunk,
                ))
            if visible:
                out.append(NormalizedChunk(
                    chunk_type=ChunkType.TEXT,
                    text=visible,
                    raw=chunk,
                ))

        # --- Tool calls ---
        tool_calls = getattr(delta, "tool_calls", None) or []
        for tc in tool_calls:
            idx = getattr(tc, "index", 0)
            buf = self._tool_buffers.setdefault(idx, {"name": "", "args": "", "id": ""})

            fn = getattr(tc, "function", None)
            if fn:
                name_part = getattr(fn, "name", "") or ""
                args_part = getattr(fn, "arguments", "") or ""
                if name_part:
                    buf["name"] += name_part
                if args_part:
                    buf["args"] += args_part
            id_part = getattr(tc, "id", "") or ""
            if id_part:
                buf["id"] = id_part

        if finish == "tool_calls":
            for idx, buf in self._tool_buffers.items():
                import json
                try:
                    args = json.loads(buf["args"]) if buf["args"].strip() else {}
                except (json.JSONDecodeError, ValueError):
                    args = {"_raw": buf["args"]}
                out.append(NormalizedChunk(
                    chunk_type=ChunkType.TOOL_CALL,
                    tool_name=buf["name"],
                    tool_args=args,
                    tool_call_id=buf["id"],
                    raw=chunk,
                ))
            self._tool_buffers.clear()

        if finish == "stop":
            out.append(NormalizedChunk(chunk_type=ChunkType.DONE, raw=chunk))

        # Usage in final chunk
        usage = getattr(chunk, "usage", None)
        if usage:
            out.append(NormalizedChunk(
                chunk_type=ChunkType.USAGE,
                usage_in=getattr(usage, "prompt_tokens", 0),
                usage_out=getattr(usage, "completion_tokens", 0),
                raw=chunk,
            ))

        return out

    def normalize_text_chunk(self, text: str) -> list[NormalizedChunk]:
        """Normalize a plain text chunk (Ollama, plain text providers)."""
        out: list[NormalizedChunk] = []
        if not text:
            return out
        visible, reasoning = self._rd.feed(text)
        if reasoning:
            out.append(NormalizedChunk(chunk_type=ChunkType.REASONING, reasoning=reasoning))
        if visible:
            out.append(NormalizedChunk(chunk_type=ChunkType.TEXT, text=visible))
        return out

    def reset(self) -> None:
        self._tool_buffers.clear()
        self._rd.reset()

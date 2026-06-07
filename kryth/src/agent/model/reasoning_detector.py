"""ReasoningDetector — extract, strip, and synthesize reasoning blocks.

For models with native reasoning (DeepSeek, Qwen, Claude extended thinking):
- Extract content between reasoning tags
- Strip it from the visible text
- Emit it as a REASONING event so the UI can render it cleanly

For models WITHOUT reasoning (GPT-3.5, Llama, Ollama plain text):
- Synthesize virtual reasoning phases based on tool activity
- Creates the "Analyzing → Planning → Executing → Verifying" UI flow
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator


class ReasoningPhase(str, Enum):
    ANALYZING   = "Analyzing"
    PLANNING    = "Planning"
    EXECUTING   = "Executing"
    VERIFYING   = "Verifying"
    COMPLETING  = "Completing"


@dataclass
class ReasoningBlock:
    content: str
    phase: str = ""        # if synthesized
    is_synthetic: bool = False
    duration_hint: float = 0.0


# Compiled patterns for all known reasoning wrappers
_OPEN_PATTERN = re.compile(
    r"<(think|thinking|reasoning|analysis|reflect|ponder|consider)>"
    r"|/\*\* *thinking \*\*/",
    re.IGNORECASE,
)
_CLOSE_PATTERN = re.compile(
    r"</(think|thinking|reasoning|analysis|reflect|ponder|consider)>"
    r"|\*\* *end thinking \*\*/",
    re.IGNORECASE,
)


class ReasoningDetector:
    """Stream-level reasoning block detector.

    Feed chunks through ``feed(chunk)`` — it yields clean text tuples.
    """

    def __init__(self) -> None:
        self._in_reasoning = False
        self._reasoning_buf: list[str] = []
        self._pending: list[str] = []
        self._blocks: list[ReasoningBlock] = []
        self._start_ts: float = 0.0

    # ------------------------------------------------------------------
    # Streaming API
    # ------------------------------------------------------------------

    def feed(self, chunk: str) -> tuple[str, str | None]:
        """Process a raw streaming chunk.

        Returns ``(visible_text, reasoning_text | None)``.
        Reasoning text should be sent to the UI as a reasoning chunk;
        visible text is what gets rendered in the response.
        """
        if not chunk:
            return "", None

        visible: list[str] = []
        reasoning: list[str] = []
        i = 0

        while i < len(chunk):
            if not self._in_reasoning:
                m = _OPEN_PATTERN.search(chunk, i)
                if m:
                    visible.append(chunk[i:m.start()])
                    self._in_reasoning = True
                    self._start_ts = time.time()
                    i = m.end()
                else:
                    visible.append(chunk[i:])
                    break
            else:
                m = _CLOSE_PATTERN.search(chunk, i)
                if m:
                    reasoning.append(chunk[i:m.start()])
                    block_text = "".join(self._reasoning_buf + reasoning)
                    self._blocks.append(ReasoningBlock(content=block_text.strip()))
                    self._reasoning_buf.clear()
                    reasoning.clear()
                    self._in_reasoning = False
                    i = m.end()
                else:
                    reasoning.append(chunk[i:])
                    break

        if reasoning:
            self._reasoning_buf.extend(reasoning)

        return "".join(visible), "".join(reasoning) if reasoning else None

    def is_reasoning(self) -> bool:
        return self._in_reasoning

    def collected_blocks(self) -> list[ReasoningBlock]:
        return list(self._blocks)

    def reset(self) -> None:
        self._in_reasoning = False
        self._reasoning_buf.clear()
        self._pending.clear()
        self._blocks.clear()

    # ------------------------------------------------------------------
    # Synthesis for non-reasoning models
    # ------------------------------------------------------------------

    @staticmethod
    def synthetic_phases(tool_name: str = "") -> list[str]:
        """Generate synthetic reasoning phase messages for a tool call.

        Returns short phrases to display in the thinking panel.
        """
        base = [
            "Analyzing current project state",
            "Planning execution approach",
        ]
        if tool_name:
            base.append(f"Preparing {_friendly_tool(tool_name)}")
        base += [
            "Verifying prerequisites",
            "Ready to execute",
        ]
        return base


def _friendly_tool(name: str) -> str:
    mapping = {
        "read_file": "file read",
        "write_file": "file write",
        "edit_file": "code edit",
        "run_command": "command execution",
        "shell_exec": "terminal command",
        "search_code": "code search",
        "browser_use_task": "browser automation",
        "spawn_agent": "agent deployment",
    }
    return mapping.get(name, name.replace("_", " "))


def extract_reasoning(text: str) -> tuple[str, str]:
    """Extract reasoning from a complete text string.

    Returns ``(reasoning_text, clean_text)``.
    """
    reasoning_parts: list[str] = []
    clean = _OPEN_PATTERN.sub("__REASONING_START__", text)
    clean = _CLOSE_PATTERN.sub("__REASONING_END__", clean)

    parts = clean.split("__REASONING_START__")
    clean_parts = [parts[0]]
    for part in parts[1:]:
        if "__REASONING_END__" in part:
            reason, rest = part.split("__REASONING_END__", 1)
            reasoning_parts.append(reason.strip())
            clean_parts.append(rest)
        else:
            reasoning_parts.append(part)

    return "\n\n".join(reasoning_parts).strip(), "".join(clean_parts).strip()

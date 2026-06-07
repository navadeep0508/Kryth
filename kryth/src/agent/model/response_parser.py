"""ResponseParser — clean raw model output for display.

Strips all internal markup that should never reach the user:
- Reasoning tags (<think>, <thinking>, <reasoning>, <analysis>)
- Tool call tags (<tool_call>, <function>, <parameter>, <invoke>)
- XML payloads
- JSON blobs that are tool arguments
- Provider-specific metadata tokens

The output is clean prose the UI can render directly.
"""

from __future__ import annotations

import re


# Tags to completely remove (tag + contents)
_STRIP_BLOCKS: list[tuple[re.Pattern, re.Pattern]] = [
    # Reasoning blocks
    (re.compile(r"<think\s*>", re.IGNORECASE),
     re.compile(r"</think\s*>", re.IGNORECASE)),
    (re.compile(r"<thinking\s*>", re.IGNORECASE),
     re.compile(r"</thinking\s*>", re.IGNORECASE)),
    (re.compile(r"<reasoning\s*>", re.IGNORECASE),
     re.compile(r"</reasoning\s*>", re.IGNORECASE)),
    (re.compile(r"<analysis\s*>", re.IGNORECASE),
     re.compile(r"</analysis\s*>", re.IGNORECASE)),
    (re.compile(r"<reflect\s*>", re.IGNORECASE),
     re.compile(r"</reflect\s*>", re.IGNORECASE)),
    # Tool call blocks
    (re.compile(r"<tool_call\s*>", re.IGNORECASE),
     re.compile(r"</tool_call\s*>", re.IGNORECASE)),
    (re.compile(r"<function_call\s*>", re.IGNORECASE),
     re.compile(r"</function_call\s*>", re.IGNORECASE)),
    (re.compile(r"<invoke\s*>", re.IGNORECASE),
     re.compile(r"</invoke\s*>", re.IGNORECASE)),
    (re.compile(r"<tool_use\s*>", re.IGNORECASE),
     re.compile(r"</tool_use\s*>", re.IGNORECASE)),
]

# Self-closing or single tags to remove
_STRIP_TAGS = re.compile(
    r"</?(?:tool_name|function_name|parameters|arguments|input|parameter"
    r"|tool_call|function|invoke|tool_use|think|thinking|reasoning|analysis)"
    r"(?:\s[^>]*)?>",
    re.IGNORECASE,
)

# JSON blocks that look like tool arguments
_JSON_TOOL_BLOCK = re.compile(
    r"```(?:json|tool_call)\s*\n\{[^`]*?\}\s*```",
    re.DOTALL,
)

# Orphaned JSON objects starting with {"name": or {"function":
_ORPHAN_JSON = re.compile(
    r'\{"(?:name|function|tool)"\s*:\s*"[^"]+"\s*,\s*"(?:arguments?|parameters?|input)"',
)


class ResponseParser:
    """Clean model output text for display."""

    def clean(self, text: str) -> str:
        """Remove all internal markup from *text* and return clean prose."""
        if not text:
            return text

        result = text

        # Remove block regions (tag + contents)
        for open_re, close_re in _STRIP_BLOCKS:
            result = _remove_blocks(result, open_re, close_re)

        # Remove JSON tool call fences
        result = _JSON_TOOL_BLOCK.sub("", result)

        # Remove orphaned single tags
        result = _STRIP_TAGS.sub("", result)

        # Remove trailing whitespace / blank lines artifacts
        result = re.sub(r"\n{3,}", "\n\n", result)
        result = result.strip()
        return result

    def has_tool_calls(self, text: str) -> bool:
        """Quickly check if *text* contains any tool call markup."""
        for open_re, _ in _STRIP_BLOCKS[4:]:  # tool call blocks only
            if open_re.search(text):
                return True
        if _JSON_TOOL_BLOCK.search(text):
            return True
        return False

    def extract_prose(self, text: str) -> str:
        """Return only the clean prose sections, stripping everything else."""
        return self.clean(text)

    def split_reasoning_and_response(self, text: str) -> tuple[str, str]:
        """Split text into ``(reasoning_text, clean_response)``.

        Reasoning is everything inside <think>/reasoning tags.
        Clean response is everything outside.
        """
        from agent.model.reasoning_detector import extract_reasoning
        return extract_reasoning(text)  # returns (reasoning, clean)


def _remove_blocks(text: str, open_re: re.Pattern, close_re: re.Pattern) -> str:
    """Remove all regions between open and close tags."""
    result_parts: list[str] = []
    pos = 0
    while pos < len(text):
        m_open = open_re.search(text, pos)
        if not m_open:
            result_parts.append(text[pos:])
            break
        result_parts.append(text[pos:m_open.start()])
        m_close = close_re.search(text, m_open.end())
        if m_close:
            pos = m_close.end()
        else:
            # No closing tag — skip to end
            break
    return "".join(result_parts)


# Module-level singleton
_parser = ResponseParser()


def clean_output(text: str) -> str:
    """Remove all LLM-internal markup from *text*."""
    return _parser.clean(text)


def split_response(text: str) -> tuple[str, str]:
    """Return ``(reasoning_text, clean_response)`` from *text*."""
    return _parser.split_reasoning_and_response(text)

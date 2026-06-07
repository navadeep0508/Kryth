"""ToolParser — parse tool calls from any provider format.

Supports:
- OpenAI JSON function calls (delta.tool_calls[].function.{name,arguments})
- Anthropic XML tool_use blocks
- Generic XML: <tool_call>, <function>, <invoke>
- Plain text heuristics (model outputs tool calls inline)
- JSON blocks embedded in markdown code fences
"""

from __future__ import annotations

import json
import re
import types
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedToolCall:
    name: str
    arguments: dict = field(default_factory=dict)
    call_id: str = ""
    raw: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "arguments": self.arguments,
                "call_id": self.call_id}


# XML patterns
_XML_TOOL_OPEN = re.compile(
    r"<(?:tool_call|function_call|invoke|tool_use)\s*>",
    re.IGNORECASE,
)
_XML_TOOL_CLOSE = re.compile(
    r"</(?:tool_call|function_call|invoke|tool_use)\s*>",
    re.IGNORECASE,
)
_XML_NAME = re.compile(r"<tool_name\s*>([^<]+)</tool_name\s*>", re.IGNORECASE)
_XML_FUNC_NAME = re.compile(r"<function_name\s*>([^<]+)</function_name\s*>", re.IGNORECASE)
_XML_PARAMS = re.compile(r"<parameters\s*>(.*?)</parameters\s*>", re.IGNORECASE | re.DOTALL)
_XML_ARGS = re.compile(r"<arguments\s*>(.*?)</arguments\s*>", re.IGNORECASE | re.DOTALL)
_XML_INPUT = re.compile(r"<input\s*>(.*?)</input\s*>", re.IGNORECASE | re.DOTALL)

# JSON blocks in markdown fences
_JSON_FENCE = re.compile(r"```(?:json|tool_call)?\s*\n({.*?})\s*```", re.DOTALL)

# Anthropic tool_use block in content list
_ANTHROPIC_TOOL_TYPE = "tool_use"


class ToolParser:
    """Parse tool calls from streaming or complete model output."""

    # ------------------------------------------------------------------
    # OpenAI-format (from delta.tool_calls)
    # ------------------------------------------------------------------

    def from_openai_delta(
        self,
        tool_calls: list[Any],
    ) -> list[ParsedToolCall]:
        """Parse OpenAI streaming tool_calls from a delta."""
        result: list[ParsedToolCall] = []
        for tc in (tool_calls or []):
            try:
                if isinstance(tc, dict):
                    fn_raw = tc.get("function", {})
                    fn = types.SimpleNamespace(**fn_raw) if isinstance(fn_raw, dict) else fn_raw
                    call_id = tc.get("id", "")
                else:
                    fn = getattr(tc, "function", None) or {}
                    call_id = getattr(tc, "id", "") or ""
                if isinstance(fn, dict):
                    name = fn.get("name", "")
                    args_str = fn.get("arguments", "") or ""
                else:
                    name = getattr(fn, "name", "") or ""
                    args_str = getattr(fn, "arguments", "") or ""
                if not name:
                    continue
                try:
                    args = json.loads(args_str) if args_str.strip() else {}
                except (json.JSONDecodeError, ValueError):
                    args = {"_raw": args_str}
                result.append(ParsedToolCall(name=name, arguments=args, call_id=call_id, raw=args_str))
            except Exception:
                pass
        return result

    def from_openai_message(self, message: Any) -> list[ParsedToolCall]:
        """Parse tool calls from a complete (non-streaming) OpenAI message."""
        tool_calls = getattr(message, "tool_calls", None) or []
        return self.from_openai_delta(tool_calls)

    # ------------------------------------------------------------------
    # Anthropic-format (from content blocks)
    # ------------------------------------------------------------------

    def from_anthropic_content(self, content: list[Any]) -> list[ParsedToolCall]:
        """Parse Anthropic tool_use content blocks."""
        result: list[ParsedToolCall] = []
        for block in (content or []):
            block_type = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
            if block_type == _ANTHROPIC_TOOL_TYPE:
                name = getattr(block, "name", None) or (block.get("name") if isinstance(block, dict) else "")
                args = getattr(block, "input", {}) or (block.get("input", {}) if isinstance(block, dict) else {})
                call_id = getattr(block, "id", "") or (block.get("id", "") if isinstance(block, dict) else "")
                if name:
                    result.append(ParsedToolCall(name=name, arguments=args or {}, call_id=call_id))
        return result

    # ------------------------------------------------------------------
    # XML text-based parsing (Anthropic legacy, custom XML)
    # ------------------------------------------------------------------

    def from_xml_text(self, text: str) -> list[ParsedToolCall]:
        """Extract tool calls from XML-tagged text."""
        result: list[ParsedToolCall] = []
        # Find all XML tool call blocks
        starts = [m.start() for m in _XML_TOOL_OPEN.finditer(text)]
        ends = [m.end() for m in _XML_TOOL_CLOSE.finditer(text)]

        for i, start in enumerate(starts):
            end = ends[i] if i < len(ends) else len(text)
            block = text[start:end]

            name = ""
            m = _XML_NAME.search(block) or _XML_FUNC_NAME.search(block)
            if m:
                name = m.group(1).strip()

            args: dict = {}
            pm = _XML_PARAMS.search(block) or _XML_ARGS.search(block) or _XML_INPUT.search(block)
            if pm:
                param_text = pm.group(1).strip()
                try:
                    args = json.loads(param_text)
                except (json.JSONDecodeError, ValueError):
                    args = _parse_xml_params(param_text)

            if name:
                result.append(ParsedToolCall(name=name, arguments=args, raw=block))

        return result

    # ------------------------------------------------------------------
    # Markdown JSON fence parsing
    # ------------------------------------------------------------------

    def from_json_fences(self, text: str) -> list[ParsedToolCall]:
        """Extract tool calls from JSON code blocks in markdown."""
        result: list[ParsedToolCall] = []
        for m in _JSON_FENCE.finditer(text):
            try:
                obj = json.loads(m.group(1))
                if isinstance(obj, dict):
                    name = obj.get("name") or obj.get("function") or obj.get("tool")
                    args = obj.get("arguments") or obj.get("parameters") or obj.get("input") or {}
                    if name and isinstance(name, str):
                        result.append(ParsedToolCall(name=name, arguments=args, raw=m.group(0)))
            except (json.JSONDecodeError, ValueError):
                pass
        return result

    # ------------------------------------------------------------------
    # Universal fallback
    # ------------------------------------------------------------------

    def from_any(self, text: str) -> list[ParsedToolCall]:
        """Try all parsers in sequence and return the union of findings."""
        calls = self.from_xml_text(text)
        if not calls:
            calls = self.from_json_fences(text)
        return calls


def _parse_xml_params(text: str) -> dict:
    """Parse simple <key>value</key> XML parameter blocks."""
    result: dict = {}
    pattern = re.compile(r"<(\w+)\s*>([^<]*)</\1\s*>")
    for m in pattern.finditer(text):
        key, val = m.group(1), m.group(2).strip()
        try:
            result[key] = json.loads(val)
        except (json.JSONDecodeError, ValueError):
            result[key] = val
    return result

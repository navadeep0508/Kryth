"""ToolParser — parse tool calls from provider-native structured formats.

Runtime v2 — NO XML, NO free-form text heuristics. Supports:
- OpenAI native function calls (delta.tool_calls[].function.{name,arguments})
- Anthropic native tool_use content blocks
- A strict, VALIDATED JSON object as the only text-mode fallback (the
  "structured output" path) — malformed payloads are rejected, not coerced.

Tool names are sanitized of Harmony/gpt-oss channel tokens so a contaminated
name like ``read_file<|channel|>json`` resolves to ``read_file``.
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


# Tool/function names are bare identifiers. Models that speak the OpenAI
# Harmony channel protocol (gpt-oss family) can bleed channel markers into the
# name field, e.g. ``read_file<|channel|>json``. Strip any ``<|...|>`` token and
# keep the first identifier-shaped token so the dispatcher resolves the real
# tool. Mirrors agent.llm._sanitize_tool_name (kept local to avoid coupling the
# model/ package to llm.py).
_TOOL_NAME_ID_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _sanitize_tool_name(name: str) -> str:
    if not name:
        return ""
    cleaned = re.sub(r"<\|[^|>]*\|>", " ", name)
    cleaned = cleaned.replace("<|", " ").replace("|>", " ")
    m = _TOOL_NAME_ID_RE.search(cleaned)
    return m.group(0) if m else cleaned.strip()


# Runtime v2: XML tool-call parsing has been removed. Tool calls come from the
# provider's NATIVE structured format (OpenAI delta / Anthropic tool_use), with
# a strict VALIDATED JSON object as the only text-mode fallback.

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
                name = _sanitize_tool_name(name)
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
                name = _sanitize_tool_name(name or "")
                if name:
                    result.append(ParsedToolCall(name=name, arguments=args or {}, call_id=call_id))
        return result

    # ------------------------------------------------------------------
    # Structured JSON parsing (validated — the only text-mode fallback)
    # ------------------------------------------------------------------
    #
    # Runtime v2: free-form XML tool-call recovery is REMOVED. The only
    # accepted non-native format is a strict, VALIDATED JSON object. Anything
    # that is not a well-formed {"name": str, "arguments": obj} is rejected
    # (returns no call) rather than being coerced from free-form text.

    def from_json_fences(self, text: str) -> list[ParsedToolCall]:
        """Extract tool calls from JSON code blocks in markdown.

        Each candidate object is VALIDATED: it must have a string ``name`` and
        an object/absent ``arguments``. Malformed payloads are rejected.
        """
        result: list[ParsedToolCall] = []
        for m in _JSON_FENCE.finditer(text):
            call = self._validate_json_tool_obj(m.group(1), raw=m.group(0))
            if call:
                result.append(call)
        return result

    @staticmethod
    def _validate_json_tool_obj(payload: str, raw: str = "") -> "ParsedToolCall | None":
        """Validate a JSON string as a tool call. Returns None if malformed."""
        try:
            obj = json.loads(payload)
        except (json.JSONDecodeError, ValueError, TypeError):
            return None
        if not isinstance(obj, dict):
            return None
        name = obj.get("name") or obj.get("function") or obj.get("tool")
        if not isinstance(name, str) or not name.strip():
            return None
        name = _sanitize_tool_name(name)
        if not name:
            return None
        args = obj.get("arguments")
        if args is None:
            args = obj.get("parameters")
        if args is None:
            args = obj.get("input")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return None  # arguments must be a structured object
        return ParsedToolCall(name=name, arguments=args, raw=raw or payload)

    # ------------------------------------------------------------------
    # Universal fallback — native first, then strict JSON. NO XML.
    # ------------------------------------------------------------------

    def from_any(self, text: str) -> list[ParsedToolCall]:
        """Validated structured-output fallback. Never parses XML.

        Tries a bare top-level JSON object, then JSON code fences. Free-form
        text and XML tool-call markup yield no calls.
        """
        stripped = (text or "").strip()
        if stripped.startswith("{"):
            call = self._validate_json_tool_obj(stripped)
            if call:
                return [call]
        return self.from_json_fences(text)

"""Phase 7 — Log Compressor.

Instead of 5000 lines, extract: Error, Cause, Location, Suggested Fix.
Stores compressed summaries so the LLM only ever sees dense context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from agent.terminal.output_parser import OutputParser, output_parser


@dataclass
class CompressedLog:
    error: str = ""
    cause: str = ""
    location: str = ""
    suggested_fix: str = ""
    key_lines: list[str] = None  # type: ignore
    original_lines: int = 0
    compressed_lines: int = 0

    def __post_init__(self):
        if self.key_lines is None:
            self.key_lines = []

    def format(self) -> str:
        parts = []
        if self.error:
            parts.append(f"Error:    {self.error}")
        if self.cause:
            parts.append(f"Cause:    {self.cause}")
        if self.location:
            parts.append(f"Location: {self.location}")
        if self.suggested_fix:
            parts.append(f"Fix:      {self.suggested_fix}")
        if self.key_lines:
            parts.append("Context:")
            parts.extend(f"  {l}" for l in self.key_lines[:8])
        ratio = (
            f" ({self.compressed_lines}/{self.original_lines} lines kept)"
            if self.original_lines > 0
            else ""
        )
        parts.append(f"[compressed{ratio}]")
        return "\n".join(parts)


# Lines that are high-signal even without specific pattern match
_HIGH_SIGNAL = re.compile(
    r"(?:error|exception|traceback|failed|fatal|assert|cannot|no such|"
    r"not found|permission denied|refused|timeout|abort|panic|oom|"
    r"syntax error|import error|warning|undefined|null pointer)",
    re.I,
)

# Lines that are pure noise
_NOISE_RE = re.compile(
    r"^\s*(?:\[info\]|\[log\]|Downloading|Extracting|Fetching|Resolving|"
    r"npm notice|yarn info|Progress:|▕|━|◼|░|█|%|\.\.\.|✔|✓)\s*",
    re.I,
)


class LogCompressor:
    """Compress verbose log output to only what matters."""

    def __init__(self, parser: OutputParser | None = None):
        self._parser = parser or output_parser

    def compress(self, raw: str, exit_code: int = 0) -> CompressedLog:
        lines = raw.splitlines()
        original_lines = len(lines)

        # Parse structured errors first
        parsed = self._parser.parse(raw, exit_code)

        compressed = CompressedLog(original_lines=original_lines)

        if parsed.errors:
            primary = parsed.errors[0]
            compressed.error = (
                f"[{primary.kind.value}] {primary.error_type}: {primary.message}"
                if primary.error_type
                else f"[{primary.kind.value}] {primary.message}"
            )
            if primary.location and primary.location.file:
                loc = primary.location
                parts = [loc.file]
                if loc.line:
                    parts.append(f"line {loc.line}")
                if loc.function:
                    parts.append(f"in {loc.function}")
                compressed.location = ", ".join(parts)
            compressed.suggested_fix = primary.suggested_fix
            if primary.raw_snippet:
                compressed.key_lines = primary.raw_snippet.splitlines()[:8]

        if len(parsed.errors) > 1:
            compressed.cause = "; ".join(
                f"{e.error_type or e.kind.value}: {e.message[:60]}"
                for e in parsed.errors[1:3]
            )

        # Extract key lines if not already set
        if not compressed.key_lines:
            compressed.key_lines = self._extract_key_lines(lines)

        compressed.compressed_lines = len(compressed.key_lines)

        # Build cause from key lines if still empty
        if not compressed.cause and compressed.key_lines:
            compressed.cause = compressed.key_lines[0]

        return compressed

    def _extract_key_lines(self, lines: list[str]) -> list[str]:
        """Select the most informative lines from verbose output."""
        key: list[str] = []
        seen: set[str] = set()

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if _NOISE_RE.match(stripped):
                continue
            norm = stripped.lower()
            if norm in seen:
                continue
            seen.add(norm)
            if _HIGH_SIGNAL.search(stripped):
                key.append(stripped)

        # If nothing found, just take first + last 3 non-empty lines
        if not key:
            non_empty = [l.strip() for l in lines if l.strip()]
            head = non_empty[:3]
            tail = non_empty[-3:] if len(non_empty) > 3 else []
            key = head + (["..."] if tail else []) + tail

        return key[:20]

    def compress_for_llm(self, raw: str, exit_code: int = 0) -> str:
        """Return a compressed string safe to send to the LLM."""
        if len(raw) < 500:
            return raw  # short enough, no compression needed
        return self.compress(raw, exit_code).format()


# Module-level singleton
log_compressor = LogCompressor()

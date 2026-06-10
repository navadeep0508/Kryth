"""Context Supervisor — prevents context window overflow for infinite-duration missions.

The LLM is the reasoning engine, not the storage engine. This module ensures
the context window is always within safe limits by:

  1. Monitoring token usage against a tiered budget
  2. Compressing/archiving old messages before overflow
  3. Replacing raw tool outputs with compact summaries
  4. Emitting live UI feedback so users see what's happening

Token Budget Tiers (configurable via env):
  WARN        70% of max  → muted notice, no action
  COMPRESS    80%         → Python-fallback compression (instant, no LLM)
  AGGRESSIVE  88%         → heavy compression + archive tool outputs
  EMERGENCY   94%         → archive everything except last N messages

Usage:
    supervisor = ContextSupervisor(session)
    supervisor.check()   # call before each LLM turn; compresses if needed
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Budget configuration
# ---------------------------------------------------------------------------

def _model_max_tokens() -> int:
    """Best-effort estimate of the current model's context window."""
    try:
        from agent.llm import MAIN_MODEL
        m = MAIN_MODEL.lower()
        if "gpt-4o" in m:
            return 128_000
        if "claude" in m:
            return 200_000
        if "gemini-1.5" in m:
            return 1_000_000
        if "llama" in m or "mistral" in m or "qwen" in m or "nemotron" in m:
            return 32_000
    except Exception:
        pass
    try:
        raw = os.getenv("KRYTH_MODEL_MAX_TOKENS", "")
        if raw:
            return int(raw)
    except ValueError:
        pass
    return 128_000


@dataclass
class BudgetTiers:
    warn: float = 0.70
    compress: float = 0.80
    aggressive: float = 0.88
    emergency: float = 0.94
    hard_limit: float = 0.97


_TIERS = BudgetTiers()


# ---------------------------------------------------------------------------
# Tool output compressor
# ---------------------------------------------------------------------------

# Patterns for large, low-signal tool outputs that can be stub-replaced
_COMPRESSIBLE_TOOLS = {
    "run_command", "shell_exec", "browser_use_task", "browser_get_html",
    "extract_data", "search_code", "grep", "glob",
}
_KEEP_OUTPUT_LINES = 30   # keep head + tail of compressible outputs


def _compress_tool_msg(msg: dict, aggressive: bool = False) -> dict:
    """Compress a tool result message in place. Returns modified copy."""
    content = msg.get("content", "")
    if not isinstance(content, str) or len(content) < 500:
        return msg
    tool_name = msg.get("name", "")

    # Aggressive: compress ALL large tool outputs
    # Normal: only compress known-bulky tools
    should_compress = aggressive or tool_name in _COMPRESSIBLE_TOOLS
    if not should_compress:
        return msg

    lines = content.splitlines()
    if len(lines) <= _KEEP_OUTPUT_LINES:
        return msg

    half = _KEEP_OUTPUT_LINES // 2
    head = "\n".join(lines[:half])
    tail = "\n".join(lines[-half:])
    dropped = len(lines) - _KEEP_OUTPUT_LINES
    compressed = f"{head}\n…[{dropped} lines compressed — {len(content)} chars]…\n{tail}"

    new = dict(msg)
    new["content"] = compressed
    return new


def _compress_messages(messages: list, aggressive: bool = False) -> tuple[list, int]:
    """Compress tool outputs in the message list. Returns (new_list, chars_freed)."""
    freed = 0
    result = []
    for m in messages:
        if m.get("role") == "tool":
            compressed = _compress_tool_msg(m, aggressive)
            freed += max(0, len(m.get("content", "")) - len(compressed.get("content", "")))
            result.append(compressed)
        else:
            result.append(m)
    return result, freed


# ---------------------------------------------------------------------------
# Mission summary generator (no LLM — pure heuristic)
# ---------------------------------------------------------------------------

_DONE_PATTERNS = re.compile(
    r"\b(created|wrote|built|implemented|added|fixed|completed|finished|done)\b.{0,80}",
    re.IGNORECASE,
)


def _extract_accomplishments(messages: list, limit: int = 15) -> list[str]:
    """Extract brief accomplishment lines from assistant messages."""
    hits = []
    for m in reversed(messages):
        if m.get("role") != "assistant":
            continue
        text = str(m.get("content") or "")
        for match in _DONE_PATTERNS.finditer(text):
            line = match.group(0).strip()[:120]
            if line and line not in hits:
                hits.append(line)
            if len(hits) >= limit:
                return hits
    return hits


def _make_mission_summary(messages: list) -> str:
    """Build a compact mission-progress summary from message history."""
    accomplishments = _extract_accomplishments(messages)
    if not accomplishments:
        return ""
    lines = ["[Mission progress summary]"]
    lines += [f"✓ {a}" for a in accomplishments]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core supervisor
# ---------------------------------------------------------------------------

class ContextSupervisor:
    """Monitors and manages context window usage for a session.

    Call `check()` before each LLM turn. It decides which tier of
    compression to apply (if any) and performs it without blocking.
    """

    def __init__(self, session) -> None:
        self._session = session
        self._max_tokens = _model_max_tokens()
        self._last_tier = "ok"

    def token_fraction(self) -> float:
        """Current token usage as a fraction of the model's context window."""
        try:
            used = self._session.total_tokens()
        except Exception:
            return 0.0
        return used / max(self._max_tokens, 1)

    def check(self) -> None:
        """Run the supervision cycle. Call before each LLM turn."""
        frac = self.token_fraction()

        if frac < _TIERS.warn:
            self._last_tier = "ok"
            return

        try:
            from agent import ui
        except Exception:
            ui = None  # type: ignore

        if frac >= _TIERS.emergency:
            self._emergency_archive(ui)
        elif frac >= _TIERS.aggressive:
            self._aggressive_compress(ui)
        elif frac >= _TIERS.compress:
            self._normal_compress(ui)
        elif frac >= _TIERS.warn:
            if self._last_tier != "warn":
                if ui:
                    ui.muted(f"  ◈ Context at {frac:.0%} — approaching limit")
                self._last_tier = "warn"

    # ------------------------------------------------------------------
    # Compression levels
    # ------------------------------------------------------------------

    def _normal_compress(self, ui) -> None:
        """Light: compress bulky tool outputs only."""
        if self._last_tier == "compress":
            return  # already compressed this tier
        msgs, freed = _compress_messages(self._session.messages, aggressive=False)
        self._session.messages = msgs
        self._last_tier = "compress"
        if ui and freed > 0:
            ui.muted(f"  ◈ Context at {self.token_fraction():.0%} — compressed {freed:,} chars")
        try:
            import sys; _d = sys.modules.get("agent.ui.dashboard")
            if _d and _d.get_active():
                _d.push_event("intel", message=f"Context compressed ({freed//1000}k chars freed)")
        except Exception:
            pass

    def _aggressive_compress(self, ui) -> None:
        """Heavy: compress all large outputs + replace old history with summary."""
        if self._last_tier in ("aggressive", "emergency"):
            return
        msgs, freed = _compress_messages(self._session.messages, aggressive=True)
        self._session.messages = msgs

        # Replace old non-system messages with a mission summary block
        _keep_recent = 10
        sys_msgs = [m for m in self._session.messages if m.get("role") == "system"]
        rest = [m for m in self._session.messages if m.get("role") != "system"]
        if len(rest) > _keep_recent + 4:
            to_archive = rest[:-_keep_recent]
            keep = rest[-_keep_recent:]
            summary = _make_mission_summary(to_archive)
            if summary:
                self._session.messages = sys_msgs + [
                    {"role": "system", "content": summary}
                ] + keep
        self._last_tier = "aggressive"
        frac = self.token_fraction()
        if ui:
            ui.muted(f"  ◈ Context at {frac:.0%} — aggressive compression applied")

    def _emergency_archive(self, ui) -> None:
        """Emergency: keep only system messages + last 6 messages."""
        if self._last_tier == "emergency":
            return
        _keep = 6
        sys_msgs = [m for m in self._session.messages if m.get("role") == "system"]
        rest = [m for m in self._session.messages if m.get("role") != "system"]
        summary = _make_mission_summary(rest)
        keep = rest[-_keep:]
        summary_block = [{"role": "system", "content": summary}] if summary else []
        self._session.messages = sys_msgs + summary_block + keep
        self._last_tier = "emergency"
        frac = self.token_fraction()
        if ui:
            ui.warn(
                f"  ⚠ Context at {frac:.0%} — emergency archive triggered. "
                f"Kept last {_keep} messages + mission summary."
            )

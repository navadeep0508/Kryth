"""History checkpointing — rolling structured compression of message history.

After every CHECKPOINT_EVERY_TOOLS tool calls (or when history chars exceed
CHECKPOINT_HISTORY_CHARS), replace old messages with a compact JSON checkpoint:

  {
    "type": "checkpoint",
    "goal": "...",           # from first user message
    "completed": [...],      # assistant "created/wrote/built/..." patterns
    "files_modified": [...], # filenames from write_file/edit_file tool args
    "decisions": [...],      # "chose/decided/using/..." patterns
    "open_issues": [...]     # recent [ERROR ...] / "failed" patterns
  }

The last KEEP_RECENT_TURNS raw message pairs are always preserved verbatim
so the model has immediate context for the current step.

Usage:
    from agent.checkpoint_manager import should_checkpoint, apply_checkpoint
    if should_checkpoint(session, tool_calls_since_last):
        session.messages, freed = apply_checkpoint(session.messages)
"""
from __future__ import annotations

import json
import os
import re
from typing import Any


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CHECKPOINT_EVERY_TOOLS  = int(os.environ.get("KRYTH_CHECKPOINT_EVERY_TOOLS",  "20"))
CHECKPOINT_HISTORY_CHARS = int(os.environ.get("KRYTH_CHECKPOINT_CHARS", str(30_000)))
KEEP_RECENT_TURNS       = int(os.environ.get("KRYTH_CHECKPOINT_KEEP_TURNS",   "8"))


# ---------------------------------------------------------------------------
# Pattern extractors
# ---------------------------------------------------------------------------

_DONE_RE = re.compile(
    r"\b(created?|wrote|written|built|implemented|added|fixed|completed?|finished|"
    r"installed|deleted|removed|refactored|updated?|migrated?)\b[^.\n]{0,120}",
    re.IGNORECASE,
)
_FILE_RE = re.compile(
    r"[`'\"]?([\w./\\-]+\.\w{1,8})[`'\"]?",
    re.IGNORECASE,
)
_DECISION_RE = re.compile(
    r"\b(chose?|decided?|using|switched? to|went with|opted? for|selected?)\b[^.\n]{0,120}",
    re.IGNORECASE,
)
_ERROR_RE = re.compile(
    r"(\[ERROR[^\]]*\]|Error:|exception:|failed to|traceback|assert.*failed)[^.\n]{0,120}",
    re.IGNORECASE,
)
_WRITE_TOOLS = frozenset({
    "write_file", "edit_file", "create_file", "patch_file",
    "append_file", "save_file", "overwrite_file",
})


def _extract_goal(messages: list) -> str:
    """Pull first user message as the task goal."""
    for m in messages:
        if m.get("role") == "user":
            text = str(m.get("content") or "").strip()
            return text[:200] + ("..." if len(text) > 200 else "")
    return ""


def _extract_completed(messages: list) -> list[str]:
    seen: set[str] = set()
    hits: list[str] = []
    for m in messages:
        if m.get("role") != "assistant":
            continue
        text = str(m.get("content") or "")
        for match in _DONE_RE.finditer(text):
            line = match.group(0).strip()[:140]
            if line and line not in seen:
                seen.add(line)
                hits.append(line)
            if len(hits) >= 20:
                return hits
    return hits


def _extract_files_modified(messages: list) -> list[str]:
    """Collect filenames from write/edit tool call arguments."""
    seen: set[str] = set()
    files: list[str] = []
    for m in messages:
        # From tool call arguments in assistant messages
        for tc in m.get("tool_calls", []) or []:
            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
            name = fn.get("name", "") if isinstance(fn, dict) else ""
            if name in _WRITE_TOOLS:
                args_raw = fn.get("arguments", "{}") if isinstance(fn, dict) else "{}"
                try:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                except Exception:
                    args = {}
                path = args.get("path") or args.get("file_path") or args.get("filename") or ""
                if path and path not in seen:
                    seen.add(str(path))
                    files.append(str(path))
        # Also scan assistant text for filename patterns
        if m.get("role") == "assistant":
            text = str(m.get("content") or "")
            for match in _FILE_RE.finditer(text):
                fname = match.group(1)
                if "/" in fname or "." in fname:
                    if fname not in seen:
                        seen.add(fname)
                        files.append(fname)
    return files[:30]


def _extract_decisions(messages: list) -> list[str]:
    seen: set[str] = set()
    hits: list[str] = []
    for m in messages:
        if m.get("role") != "assistant":
            continue
        text = str(m.get("content") or "")
        for match in _DECISION_RE.finditer(text):
            line = match.group(0).strip()[:140]
            if line and line not in seen:
                seen.add(line)
                hits.append(line)
            if len(hits) >= 10:
                return hits
    return hits


def _extract_open_issues(messages: list, recent_n: int = 8) -> list[str]:
    """Scan the last N messages for error patterns."""
    recent = messages[-recent_n:] if len(messages) > recent_n else messages
    seen: set[str] = set()
    hits: list[str] = []
    for m in recent:
        text = str(m.get("content") or "")
        for match in _ERROR_RE.finditer(text):
            line = match.group(0).strip()[:140]
            if line and line not in seen:
                seen.add(line)
                hits.append(line)
            if len(hits) >= 5:
                return hits
    return hits


# ---------------------------------------------------------------------------
# Build checkpoint
# ---------------------------------------------------------------------------

def build_checkpoint(messages: list) -> dict[str, Any]:
    """Build a structured checkpoint dict from the given message list."""
    return {
        "type": "checkpoint",
        "goal": _extract_goal(messages),
        "completed": _extract_completed(messages),
        "files_modified": _extract_files_modified(messages),
        "decisions": _extract_decisions(messages),
        "open_issues": _extract_open_issues(messages),
    }


def _checkpoint_to_system_msg(checkpoint: dict) -> dict:
    """Serialise a checkpoint as a compact system message."""
    return {
        "role": "system",
        "content": f"[checkpoint]\n{json.dumps(checkpoint, ensure_ascii=False, indent=2)}",
    }


# ---------------------------------------------------------------------------
# Apply checkpoint — compress old history, keep recent turns raw
# ---------------------------------------------------------------------------

def apply_checkpoint(
    messages: list,
    keep_recent: int = KEEP_RECENT_TURNS,
) -> tuple[list, int]:
    """Replace archived turns with a compact checkpoint, keep last N raw.

    Returns (new_messages, chars_freed).
    """
    # Separate system messages (keep all) from conversation turns
    sys_msgs  = [m for m in messages if m.get("role") == "system"
                 and not m.get("content", "").startswith("[checkpoint]")]
    rest      = [m for m in messages if m.get("role") != "system"]

    if len(rest) <= keep_recent:
        # Not enough history to compress
        return messages, 0

    to_archive = rest[:-keep_recent]
    keep       = rest[-keep_recent:]

    # Remove old checkpoint blocks from sys_msgs (merge into new one)
    old_checkpoints = [m for m in messages
                       if m.get("content", "").startswith("[checkpoint]")]
    # Merge old checkpoint data with new extraction
    merged_archive = to_archive
    for oc in old_checkpoints:
        try:
            old_data = json.loads(oc["content"][len("[checkpoint]\n"):])
            # Synthesize a fake history entry so extractors can pick it up
            synthetic = {
                "role": "assistant",
                "content": " ".join(
                    old_data.get("completed", []) +
                    old_data.get("decisions", [])
                ),
            }
            merged_archive = [synthetic] + merged_archive
        except Exception:
            pass

    checkpoint = build_checkpoint(merged_archive)
    checkpoint_msg = _checkpoint_to_system_msg(checkpoint)

    chars_before = sum(len(str(m.get("content") or "")) for m in messages)
    new_messages = sys_msgs + [checkpoint_msg] + keep
    chars_after  = sum(len(str(m.get("content") or "")) for m in new_messages)

    return new_messages, max(0, chars_before - chars_after)


# ---------------------------------------------------------------------------
# Trigger condition
# ---------------------------------------------------------------------------

def should_checkpoint(
    session,
    tool_calls_since_last: int = 0,
) -> bool:
    """Return True if a checkpoint should be applied now.

    Triggers when:
    - tool_calls_since_last >= CHECKPOINT_EVERY_TOOLS, OR
    - total conversation chars >= CHECKPOINT_HISTORY_CHARS
    """
    if tool_calls_since_last >= CHECKPOINT_EVERY_TOOLS:
        return True

    try:
        total_chars = sum(
            len(str(m.get("content") or ""))
            for m in session.messages
            if m.get("role") != "system"
        )
        if total_chars >= CHECKPOINT_HISTORY_CHARS:
            return True
    except Exception:
        pass

    return False

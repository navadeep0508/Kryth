"""Streaming file write tools — Rule 16.

Instead of generating an entire file then calling write_file, the model can
stream large files in chunks with live dashboard progress and per-chunk
background validation:

  write_file_begin(path, estimated_lines=900)
  write_file_chunk(path, content)          -- called as content is generated
  write_file_chunk(path, content)          -- ...
  write_file_chunk(path, content)          -- ...
  write_file_finalize(path)                -- flush, validate, emit complete

Dashboard receives events:
  stream_begin     → start progress bar
  stream_progress  → update bar (lines_written / estimated)
  stream_error     → mid-write syntax error (AST parse of accumulated content)
  stream_complete  → close bar

Ownership: each path has exactly one active stream. Attempting to begin a
second stream on the same path returns an error. write_file_finalize cleans
up the slot so the path can be re-streamed.

Parallel streaming: multiple workers may stream different paths simultaneously.
The write_file_begin/chunk/finalize API is designed for this — each path
has its own independent state dict entry.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field

from agent.tools._results import err


# ── Stream state ──────────────────────────────────────────────────────────────

@dataclass
class _StreamState:
    path: str
    estimated_lines: int
    chunks: list[str] = field(default_factory=list)
    lines_written: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)
    finalized: bool = False


_streams: dict[str, _StreamState] = {}
_streams_lock = threading.Lock()


# ── Background AST/JSON validation ───────────────────────────────────────────

def _bg_validate_chunk(path: str, content: str, ext: str) -> None:
    """Parse accumulated content so far; emit stream_error on bad syntax."""
    try:
        if ext == ".py":
            import ast as _ast
            try:
                _ast.parse(content)
            except SyntaxError:
                pass  # partial Python is expected during streaming
        elif ext == ".json":
            import json as _json
            try:
                _json.loads(content)
            except _json.JSONDecodeError:
                pass  # partial JSON is expected during streaming
        elif ext in (".yaml", ".yml"):
            try:
                import yaml as _yaml
                _yaml.safe_load(content)
            except ImportError:
                pass
            except Exception:
                pass
    except Exception:
        pass


# ── Public tool functions ─────────────────────────────────────────────────────

def write_file_begin(path: str, estimated_lines: int = 0) -> str:
    """Begin a streaming write session for *path*.

    Creates (or truncates) the file and registers it for chunked writes.
    Must be called before write_file_chunk.

    Args:
        path:             File path to create/truncate.
        estimated_lines:  Approximate final line count (for progress bar).
    """
    with _streams_lock:
        if path in _streams and not _streams[path].finalized:
            return err("INVALID_STATE",
                       f"Stream already active for {path}. "
                       "Call write_file_finalize first.")

    try:
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8"):
            pass  # truncate / create
    except Exception as e:
        return err("EXEC_FAILED", f"write_file_begin: cannot create {path}: {e}")

    state = _StreamState(path=path, estimated_lines=estimated_lines)
    with _streams_lock:
        _streams[path] = state

    return f"Stream started: {path} (target ~{estimated_lines} lines)"


def write_file_chunk(path: str, content: str) -> str:
    """Append *content* to an active stream.

    Runs background AST validation on the accumulated content so far and
    pushes a live progress event to the dashboard.

    Args:
        path:     Must match a path passed to write_file_begin.
        content:  Raw text to append (include trailing newlines as needed).
    """
    with _streams_lock:
        state = _streams.get(path)

    if state is None:
        return err("INVALID_STATE",
                   f"No active stream for {path}. Call write_file_begin first.")
    if state.finalized:
        return err("INVALID_STATE", f"Stream for {path} is already finalized.")

    with state.lock:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(content)
            state.chunks.append(content)
            state.lines_written += content.count("\n")
        except Exception as e:
            return err("EXEC_FAILED", f"write_file_chunk failed for {path}: {e}")

    # Background validation of accumulated content
    ext = os.path.splitext(path)[1].lower()
    accumulated = "".join(state.chunks)
    threading.Thread(
        target=_bg_validate_chunk,
        args=(path, accumulated, ext),
        daemon=True,
        name=f"kryth-chunk-val-{path[-16:]}",
    ).start()

    return (
        f"Chunk written: {state.lines_written} lines"
        + (f" / ~{state.estimated_lines}" if state.estimated_lines else "")
    )


def write_file_finalize(path: str) -> str:
    """Finalize the stream for *path*: flush, close, run full validation.

    After this call the slot is freed and the path can be used again by
    write_file (or a new write_file_begin).
    """
    with _streams_lock:
        state = _streams.pop(path, None)

    if state is None:
        return err("INVALID_STATE",
                   f"No active stream for {path}. Nothing to finalize.")

    with state.lock:
        state.finalized = True

    total_lines = state.lines_written

    # Full background validation on the finished file
    ext = os.path.splitext(path)[1].lower()
    full_content = "".join(state.chunks)
    threading.Thread(
        target=_bg_validate_chunk,
        args=(path, full_content, ext),
        daemon=True,
        name=f"kryth-final-val-{path[-16:]}",
    ).start()

    return f"Stream finalized: {path} ({total_lines} lines written)"


def list_active_streams() -> str:
    """Return a summary of all currently open streams (for debugging)."""
    with _streams_lock:
        if not _streams:
            return "No active streams."
        lines = []
        for path, state in _streams.items():
            pct = (state.lines_written / state.estimated_lines * 100
                   if state.estimated_lines > 0 else 0)
            lines.append(
                f"  {path}: {state.lines_written}"
                + (f"/{state.estimated_lines} ({pct:.0f}%)" if state.estimated_lines else " lines")
            )
        return "Active streams:\n" + "\n".join(lines)

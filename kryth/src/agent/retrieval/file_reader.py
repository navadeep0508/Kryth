"""Intelligent file reading with automatic strategy selection.

Strategy selection based on file size:
  size < MMAP_THRESHOLD (1 MB)   → normal read
  size < STREAM_THRESHOLD (50 MB) → mmap
  size >= STREAM_THRESHOLD        → streaming generator

Encoding detection uses chardet or charset-normalizer if available,
with UTF-8 as the final fallback.

Concurrent batch reads use ThreadPoolExecutor (compatible with the
project's threading model — no asyncio required).
"""
from __future__ import annotations

import mmap
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Generator, Iterable, Optional

from agent.retrieval import config as cfg

_HAS_CHARDET = False
_HAS_CHARSET_NORMALIZER = False

try:
    import chardet as _chardet

    _HAS_CHARDET = True
except ImportError:
    pass

try:
    from charset_normalizer import from_bytes as _from_bytes

    _HAS_CHARSET_NORMALIZER = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Encoding detection
# ---------------------------------------------------------------------------


def detect_encoding(raw: bytes, sample_size: int = 8192) -> str:
    """Detect file encoding from raw bytes. Returns 'utf-8' as default."""
    sample = raw[:sample_size]

    if _HAS_CHARDET:
        result = _chardet.detect(sample)
        enc = (result.get("encoding") or "utf-8").lower()
        # Normalise common aliases
        if enc in ("ascii", "utf-8-sig", "utf-8"):
            return "utf-8"
        return enc

    if _HAS_CHARSET_NORMALIZER:
        result = _from_bytes(sample)
        best = result.best()
        if best:
            enc = best.encoding or "utf-8"
            # ASCII is a strict subset of UTF-8; normalise for consistency
            if enc.lower() == "ascii":
                return "utf-8"
            return enc

    # Heuristic: if there are no bytes > 0x7F, it's ASCII/UTF-8
    if all(b < 0x80 for b in sample):
        return "utf-8"

    return "utf-8"


# ---------------------------------------------------------------------------
# Binary detection
# ---------------------------------------------------------------------------


def is_binary(path: str) -> bool:
    """Quick binary check using the first 512 bytes."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(512)
        if not chunk:
            return False
        # Null bytes are a strong binary signal
        if b"\x00" in chunk:
            return True
        # High ratio of non-printable bytes
        non_printable = sum(
            1 for b in chunk if b < 9 or (b > 13 and b < 32 and b != 27)
        )
        return (non_printable / len(chunk)) > 0.30
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Reading strategies
# ---------------------------------------------------------------------------


def read_text(path: str, encoding: Optional[str] = None) -> str:
    """Read a text file. Automatically uses mmap for medium-sized files."""
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0

    if size > cfg.MAX_FILE_SIZE:
        raise ValueError(
            f"File too large to read fully: {size} bytes "
            f"(limit {cfg.MAX_FILE_SIZE}). Use read_stream() instead."
        )

    if cfg.ENABLE_MMAP and size >= cfg.MMAP_THRESHOLD:
        return _read_mmap(path, encoding)

    with open(path, "rb") as f:
        raw = f.read()

    if encoding is None:
        encoding = detect_encoding(raw)

    return raw.decode(encoding, errors="replace")


def _read_mmap(path: str, encoding: Optional[str] = None) -> str:
    """Memory-mapped read. Faster for files > MMAP_THRESHOLD."""
    with open(path, "rb") as f:
        size = os.path.getsize(path)
        if size == 0:
            return ""
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            raw = mm.read()

    if encoding is None:
        encoding = detect_encoding(raw[:8192])

    return raw.decode(encoding, errors="replace")


def read_bytes(path: str) -> bytes:
    """Read raw bytes from a file."""
    with open(path, "rb") as f:
        return f.read()


def read_stream(
    path: str, chunk_size: int = 65536
) -> Generator[str, None, None]:
    """Stream a large file in chunks. Yields decoded string chunks."""
    with open(path, "rb") as f:
        first = f.read(chunk_size)
        if not first:
            return
        encoding = detect_encoding(first)
        yield first.decode(encoding, errors="replace")

        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            yield chunk.decode(encoding, errors="replace")


def read_smart(path: str) -> str:
    """Auto-select the best reading strategy based on file size.

    Returns the full file content as a string.
    For very large files (> STREAM_THRESHOLD), streams and joins.
    This avoids OOM for large files while still returning all content.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0

    if size > cfg.MAX_FILE_SIZE:
        raise ValueError(
            f"File exceeds MAX_FILE_SIZE ({cfg.MAX_FILE_SIZE} bytes): {path}"
        )

    if size >= cfg.STREAM_THRESHOLD:
        # Stream and join — caller wanted full content despite size
        return "".join(read_stream(path))

    return read_text(path)


def read_lines(
    path: str, offset: int = 0, limit: int = 2000
) -> list[str]:
    """Read a specific window of lines efficiently.

    Uses mmap for files above MMAP_THRESHOLD to avoid loading
    the entire file into memory just to read a slice.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0

    if cfg.ENABLE_MMAP and size >= cfg.MMAP_THRESHOLD:
        return _read_lines_mmap(path, offset, limit)

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for _ in range(offset):
            if not f.readline():
                return []
        lines = []
        for _ in range(limit):
            line = f.readline()
            if not line:
                break
            lines.append(line)
        return lines


def _read_lines_mmap(path: str, offset: int, limit: int) -> list[str]:
    """Extract a line window from a memory-mapped file."""
    with open(path, "rb") as f:
        size = os.path.getsize(path)
        if size == 0:
            return []
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            pos = 0
            current_line = 0

            # Skip to offset
            while current_line < offset:
                nl = mm.find(b"\n", pos)
                if nl == -1:
                    return []
                pos = nl + 1
                current_line += 1

            lines: list[str] = []
            for _ in range(limit):
                nl = mm.find(b"\n", pos)
                if nl == -1:
                    chunk = mm[pos:]
                    if chunk:
                        lines.append(chunk.decode("utf-8", errors="replace"))
                    break
                lines.append(mm[pos : nl + 1].decode("utf-8", errors="replace"))
                pos = nl + 1
                if pos >= mm.size():
                    break

            return lines


# ---------------------------------------------------------------------------
# Concurrent batch reading
# ---------------------------------------------------------------------------


def read_batch(
    paths: Iterable[str],
    max_workers: Optional[int] = None,
) -> dict[str, str | Exception]:
    """Read multiple files concurrently.

    Returns ``{path: content}`` for success or ``{path: Exception}`` for
    failures. Uses ThreadPoolExecutor to parallelise I/O-bound reads.
    """
    paths_list = list(paths)
    if not paths_list:
        return {}

    workers = min(
        max_workers or cfg.MAX_CONCURRENT_READS,
        len(paths_list),
        32,
    )

    results: dict[str, str | Exception] = {}

    def _read_one(p: str) -> tuple[str, str | Exception]:
        try:
            return p, read_smart(p)
        except Exception as e:
            return p, e

    if workers <= 1 or len(paths_list) == 1:
        for p in paths_list:
            path, result = _read_one(p)
            results[path] = result
        return results

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="kryth-reader") as ex:
        futures = {ex.submit(_read_one, p): p for p in paths_list}
        for future in as_completed(futures):
            path, result = future.result()
            results[path] = result

    return results


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def capabilities() -> dict:
    return {
        "mmap": cfg.ENABLE_MMAP,
        "chardet": _HAS_CHARDET,
        "charset_normalizer": _HAS_CHARSET_NORMALIZER,
        "mmap_threshold": cfg.MMAP_THRESHOLD,
        "stream_threshold": cfg.STREAM_THRESHOLD,
        "max_file_size": cfg.MAX_FILE_SIZE,
        "max_concurrent_reads": cfg.MAX_CONCURRENT_READS,
    }


def read_file(path: str, *args, **kwargs) -> str:
    """Compatibility wrapper: read a file using smart strategy."""
    return read_smart(path)

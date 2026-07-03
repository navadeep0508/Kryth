"""
Intelligent Read Memory — caches full tool results, tracks file hashes,
and provides smart re-read logic. Replaces hard blocking with caching.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional, List, Tuple


@dataclass
class CachedRead:
    """Cached result of a read operation."""
    tool_name: str
    args: dict
    result: str
    summary: str
    file_hash: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    read_count: int = 1
    ranges_read: List[Tuple[int, int]] = field(default_factory=list)


@dataclass
class ReadMemory:
    """Per-session read memory with intelligent caching."""
    files: dict = field(default_factory=dict)          # path -> CachedRead
    directories: dict = field(default_factory=dict)    # path -> CachedRead
    searches: dict = field(default_factory=dict)       # query_hash -> CachedRead
    file_hashes: dict = field(default_factory=dict)    # path -> hash

    # Knowledge graph
    known_files: set = field(default_factory=set)
    known_functions: dict = field(default_factory=dict)  # file -> [functions]
    known_classes: dict = field(default_factory=dict)    # file -> [classes]
    known_imports: dict = field(default_factory=dict)    # file -> [imports]

    # Token budget for context injection
    max_context_tokens: int = 1000


class ReadMemoryManager:
    """Thread-safe manager for per-session read memory."""

    def __init__(self):
        self._sessions: dict[int, ReadMemory] = {}
        self._lock = threading.RLock()

    def _get_memory(self, session_id: int) -> ReadMemory:
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = ReadMemory()
            return self._sessions[session_id]

    @staticmethod
    def _resolve_read_key(path: str, known_keys: set) -> str:
        """Resolve path to stored ReadMemory key with basename fallback."""
        norm = path.replace("\\", "/")
        if norm in known_keys:
            return norm
        base = os.path.basename(norm)
        for stored in known_keys:
            if os.path.basename(stored.replace("\\", "/")) == base:
                return stored
        return path

    def clear_session(self, session_id: int) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    # ── File operations ──────────────────────────────────────────────────────

    def _hash_file(self, path: str) -> Optional[str]:
        """Compute file hash for change detection."""
        try:
            abs_path = os.path.abspath(path)
            with open(abs_path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()[:16]
        except Exception:
            return None

    def _hash_args(self, args: dict) -> str:
        """Create hash of tool args for search deduplication."""
        return hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()[:16]

    def _make_summary(self, content: str, max_chars: int = 300) -> str:
        """Create a compact summary of file content."""
        lines = content.strip().splitlines()
        if len(lines) <= 20:
            return content[:max_chars]
        head = "\n".join(lines[:10])
        tail = "\n".join(lines[-5:])
        return f"{head}\n... ({len(lines)} lines total) ...\n{tail}"[:max_chars]

    def _extract_knowledge(self, path: str, content: str, mem: ReadMemory) -> None:
        """Extract functions, classes, imports from file content."""
        import re

        # Python functions
        funcs = re.findall(r'^\s*(?:async\s+)?def\s+(\w+)\s*\(', content, re.MULTILINE)
        if funcs:
            mem.known_functions[path] = funcs

        # Python classes
        classes = re.findall(r'^\s*class\s+(\w+)\s*[\(:]', content, re.MULTILINE)
        if classes:
            mem.known_classes[path] = classes

        # Python imports
        imports = re.findall(r'^(?:from\s+(\S+)\s+)?import\s+(.+)$', content, re.MULTILINE)
        if imports:
            mem.known_imports[path] = [f"{f} {i}" if f else f"import {i}" for f, i in imports]

    def record_read_file(
        self,
        session_id: int,
        path: str,
        args: dict,
        result: str,
        ranges: List[Tuple[int, int]] = None
    ) -> CachedRead:
        """Record a read_file operation with full result caching."""
        mem = self._get_memory(session_id)
        file_hash = self._hash_file(path)

        with self._lock:
            # Resolve to existing key if path alias
            resolved = self._resolve_read_key(path, set(mem.files.keys()))
            cached = mem.files.get(resolved)
            if cached and cached.file_hash == file_hash:
                cached.read_count += 1
                if ranges:
                    cached.ranges_read.extend(ranges)
                return cached

            # New or changed file - create fresh cache entry
            summary = self._make_summary(result)
            cached = CachedRead(
                tool_name="read_file",
                args=args,
                result=result,
                summary=summary,
                file_hash=file_hash,
                read_count=1,
                ranges_read=ranges or [(1, len(result.splitlines()))],
            )
            mem.files[resolved] = cached
            mem.file_hashes[resolved] = file_hash
            mem.known_files.add(resolved)
            self._extract_knowledge(resolved, result, mem)
            return cached

    def get_cached_read(
        self,
        session_id: int,
        path: str,
        ranges: List[Tuple[int, int]] = None
    ) -> Optional[CachedRead]:
        """Get cached read if valid. Returns None if file changed or not cached."""
        mem = self._get_memory(session_id)

        with self._lock:
            resolved = self._resolve_read_key(path, set(mem.files.keys()))
            cached = mem.files.get(resolved)
            if not cached:
                return None

            # Check if file changed
            current_hash = self._hash_file(path)
            if cached.file_hash != current_hash:
                # File changed - invalidate cache
                mem.files.pop(resolved, None)
                mem.file_hashes.pop(resolved, None)
                return None

            # If specific ranges requested, check if covered
            if ranges:
                covered = any(
                    r[0] <= req[0] and r[1] >= req[1]
                    for r in cached.ranges_read
                    for req in ranges
                )
                if not covered:
                    return None

            cached.read_count += 1
            if ranges:
                cached.ranges_read.extend(ranges)
            return cached

    def record_list_files(self, session_id: int, path: str, args: dict, result: str) -> CachedRead:
        """Record a list_files operation."""
        mem = self._get_memory(session_id)
        args_hash = self._hash_args(args)

        with self._lock:
            cached = CachedRead(
                tool_name="list_files",
                args=args,
                result=result,
                summary=self._make_summary(result, max_chars=200),
                read_count=1,
            )
            mem.directories[path] = cached
            return cached

    def get_cached_listdir(self, session_id: int, path: str, args: dict) -> Optional[CachedRead]:
        """Get cached directory listing if available."""
        mem = self._get_memory(session_id)
        args_hash = self._hash_args(args)

        with self._lock:
            cached = mem.directories.get(path)
            if cached and cached.args == args:
                cached.read_count += 1
                return cached
            return None

    def record_search(
        self,
        session_id: int,
        tool_name: str,
        args: dict,
        result: str
    ) -> CachedRead:
        """Record a search operation."""
        mem = self._get_memory(session_id)
        query_hash = self._hash_args(args)

        with self._lock:
            cached = CachedRead(
                tool_name=tool_name,
                args=args,
                result=result,
                summary=self._make_summary(result, max_chars=200),
                read_count=1,
            )
            mem.searches[query_hash] = cached
            return cached

    def get_cached_search(
        self,
        session_id: int,
        tool_name: str,
        args: dict
    ) -> Optional[CachedRead]:
        """Get cached search result if identical query."""
        mem = self._get_memory(session_id)
        query_hash = self._hash_args(args)

        with self._lock:
            cached = mem.searches.get(query_hash)
            if cached and cached.tool_name == tool_name:
                cached.read_count += 1
                return cached
            return None

    # ── Context injection ────────────────────────────────────────────────────

    def get_context_summary(self, session_id: int, max_tokens: int = None) -> str:
        """Get compact memory summary for injection into LLM context."""
        mem = self._get_memory(session_id)
        max_tok = max_tokens or mem.max_context_tokens

        with self._lock:
            parts = []

            if mem.known_files:
                files = sorted(mem.known_files)[:15]
                parts.append(f"Files already read ({len(mem.known_files)}): " + ", ".join(files))

            if mem.known_functions:
                funcs = []
                for f, fn_list in list(mem.known_functions.items())[:8]:
                    funcs.append(f"{f}: {', '.join(fn_list[:5])}")
                if funcs:
                    parts.append("Known functions:\n  " + "\n  ".join(funcs))

            if mem.known_classes:
                classes = []
                for f, cl_list in list(mem.known_classes.items())[:8]:
                    classes.append(f"{f}: {', '.join(cl_list[:3])}")
                if classes:
                    parts.append("Known classes:\n  " + "\n  ".join(classes))

            if mem.directories:
                dirs = sorted(mem.directories.keys())[:8]
                parts.append("Directories already listed: " + ", ".join(dirs))

            if mem.searches:
                parts.append(f"Searches already executed: {len(mem.searches)}")

        if not parts:
            return ""

        # Rough token estimation (4 chars ~ 1 token)
        full = "[READ MEMORY — already known, do NOT re-read]\n" + "\n".join(parts)
        if len(full) > max_tok * 4:
            return full[:max_tok * 4] + "\n... (truncated)"
        return full

    def get_stats(self, session_id: int) -> dict:
        """Get cache statistics for monitoring."""
        mem = self._get_memory(session_id)
        with self._lock:
            return {
                "files_cached": len(mem.files),
                "directories_cached": len(mem.directories),
                "searches_cached": len(mem.searches),
                "known_files": len(mem.known_files),
                "known_functions": sum(len(v) for v in mem.known_functions.values()),
                "known_classes": sum(len(v) for v in mem.known_classes.values()),
            }


# Global instance
_read_memory_manager = ReadMemoryManager()


def get_read_memory(session_id: int) -> ReadMemory:
    """Get or create read memory for a session."""
    return _read_memory_manager._get_memory(session_id)


def clear_read_memory(session_id: int) -> None:
    """Clear read memory for a session."""
    _read_memory_manager.clear_session(session_id)


def clear_session(session_id: int) -> None:
    """Compatibility alias for clear_read_memory."""
    clear_read_memory(session_id)


# Convenience functions for use in agent_loop
def record_read_file(session_id: int, path: str, args: dict, result: str, ranges: list = None) -> CachedRead:
    return _read_memory_manager.record_read_file(session_id, path, args, result, ranges)


def get_cached_read_file(session_id: int, path: str, ranges: list = None) -> Optional[CachedRead]:
    return _read_memory_manager.get_cached_read(session_id, path, ranges)


def record_list_files(session_id: int, path: str, args: dict, result: str) -> CachedRead:
    return _read_memory_manager.record_list_files(session_id, path, args, result)


def get_cached_listdir(session_id: int, path: str, args: dict) -> Optional[CachedRead]:
    return _read_memory_manager.get_cached_listdir(session_id, path, args)


def get_cached_read(session_id: int, path: str, ranges: list = None) -> Optional[CachedRead]:
    """Compatibility alias for get_cached_read_file."""
    return get_cached_read_file(session_id, path, ranges)


def get_cached_search(session_id: int, tool_name: str, args: dict) -> Optional[CachedRead]:
    return _read_memory_manager.get_cached_search(session_id, tool_name, args)


def record_search_tool(session_id: int, tool_name: str, args: dict, result: str) -> CachedRead:
    return _read_memory_manager.record_search(session_id, tool_name, args, result)


def record_search(session_id: int, tool_name: str, args: dict, result: str) -> CachedRead:
    """Alias for record_search_tool for compatibility."""
    return record_search_tool(session_id, tool_name, args, result)


def get_read_memory_context(session_id: int, max_tokens: int = 1000) -> str:
    return _read_memory_manager.get_context_summary(session_id, max_tokens)


# Compatibility alias
get_context_summary = get_read_memory_context


# Session-based summarizer functions (wrappers around post_read_summarizer)
def summarize_file_session(session_id: int, path: str, content: str) -> "FileSummary":
    """Summarize a file and store in session read_memory."""
    from agent.memory.post_read_summarizer import summarize_file as _summarize
    summary = _summarize(path, content)
    # Also store in session.read_memory dict
    mem = _read_memory_manager._get_memory(session_id)
    with _read_memory_manager._lock:
        mem.read_memory[path] = summary
    return summary


def get_cached_summary(session_id: int, path: str) -> Optional["FileSummary"]:
    """Get cached summary for a file from session memory."""
    mem = _read_memory_manager._get_memory(session_id)
    with _read_memory_manager._lock:
        return mem.read_memory.get(path)


def should_reread_file(session_id: int, path: str, force: bool = False) -> bool:
    """Determine if file should be re-read from disk."""
    if force:
        return True
    mem = _read_memory_manager._get_memory(session_id)
    with _read_memory_manager._lock:
        cached = mem.read_memory.get(path)
        if not cached:
            return True  # Never read before
        # Check file hash
        import hashlib
        try:
            with open(path, "rb") as f:
                current_hash = hashlib.sha256(f.read()).hexdigest()[:16]
            return cached.file_hash != current_hash
        except Exception:
            return True  # If can't check, re-read
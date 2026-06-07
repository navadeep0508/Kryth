"""Fast file discovery using fd (fd-find) with os.walk fallback.

fd respects .gitignore automatically, uses parallel traversal internally,
and is 2-10x faster than os.walk for large repositories.

Both fd names are tried: 'fd' (most distros) and 'fdfind' (Debian/Ubuntu).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import List, Optional

from agent.context import IGNORE_DIRS, SOURCE_SUFFIXES


_fd_bin: Optional[str] = None
_fd_checked: bool = False


def _find_fd() -> Optional[str]:
    """Locate the fd binary. Caches the result for the session."""
    global _fd_bin, _fd_checked
    if _fd_checked:
        return _fd_bin
    _fd_checked = True
    _fd_bin = shutil.which("fd") or shutil.which("fdfind")
    return _fd_bin


def discover_files(
    directory: str = ".",
    extensions: Optional[List[str]] = None,
    max_results: int = 0,
    timeout: int = 30,
) -> List[str]:
    """Discover source files in *directory*.

    Args:
        directory:   Root directory to search.
        extensions:  File extensions to include (default: SOURCE_SUFFIXES).
        max_results: Cap the result count (0 = unlimited).
        timeout:     Max seconds to wait for fd.

    Returns:
        File paths sorted by modification time (newest first).
    """
    if extensions is None:
        extensions = list(SOURCE_SUFFIXES)

    fd = _find_fd()
    if fd:
        return _discover_fd(fd, directory, extensions, max_results, timeout)

    return _discover_walk(directory, extensions, max_results)


def _discover_fd(
    fd_bin: str,
    directory: str,
    extensions: List[str],
    max_results: int,
    timeout: int,
) -> List[str]:
    cmd = [fd_bin, "--type", "f"]

    # Extension filters
    for ext in extensions:
        cmd += ["--extension", ext.lstrip(".")]

    # Exclude standard noise directories
    for ignore in [
        "node_modules", "__pycache__", ".git", "venv", ".venv",
        "dist", "build", ".next", ".cache", ".pytest_cache",
        ".mypy_cache", ".ruff_cache", ".eggs",
    ]:
        cmd += ["--exclude", ignore]

    cmd.append(directory)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode in (0, 1):
            paths = [p for p in result.stdout.splitlines() if p.strip()]
            if max_results > 0:
                paths = paths[:max_results]
            return _sort_by_mtime(paths)
    except (subprocess.TimeoutExpired, OSError):
        pass

    # fd failed — fallback to os.walk
    return _discover_walk(directory, extensions, max_results)


def _discover_walk(
    directory: str,
    extensions: List[str],
    max_results: int,
) -> List[str]:
    """Pure-Python file discovery using os.walk."""
    ext_set = {e if e.startswith(".") else f".{e}" for e in extensions}
    candidates: List[str] = []

    for root, dirs, files in os.walk(directory):
        dirs[:] = [
            d for d in dirs
            if d not in IGNORE_DIRS and not d.startswith(".")
        ]
        for fname in files:
            _, ext = os.path.splitext(fname)
            if ext.lower() not in ext_set:
                continue
            candidates.append(os.path.join(root, fname))
            if max_results > 0 and len(candidates) >= max_results:
                return _sort_by_mtime(candidates)

    return _sort_by_mtime(candidates)


def _sort_by_mtime(paths: List[str]) -> List[str]:
    def _mtime(p: str) -> float:
        try:
            return os.path.getmtime(p)
        except OSError:
            return 0.0

    return sorted(paths, key=_mtime, reverse=True)


def fd_available() -> bool:
    """Return True if fd/fdfind is on PATH."""
    return _find_fd() is not None

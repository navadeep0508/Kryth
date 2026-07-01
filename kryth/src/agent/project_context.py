"""Project context loader — cascading memory layers + git snapshot.

A "memory layer" is a Markdown file the user (or another agent) wrote
to tell the agent things it should remember about a project: coding
conventions, deployment quirks, the user's preferred libraries, the
fact that integration tests need a live database, etc.

Three scopes, loaded in this order so the model reads from general
to specific (specifics override generals naturally):

    user      ~/.kryth/MEMORY.md            # cross-project preferences
    project   <project_root>/<first match>     # team-shared conventions
    subdir    <ancestor_of_cwd>/<first match>  # area-specific (between
                                                 project root and cwd)

The "project root" is the nearest ancestor containing ``.git``,
``package.json``, ``pyproject.toml``, ``Cargo.toml``, ``go.mod``, or
one of the memory file names itself. The first match for each scope
wins — no merging across name aliases, which keeps the lookup obvious.

Public surface:

    load_memory_layers(start='.')   list[MemoryLayer]   # structured
    load_context_file(start='.')    str                 # legacy shim
    find_context_file(start='.')    str | None          # legacy shim
    git_status_snapshot(start='.')  str
    project_root(start='.')         Path

``load_context_file`` is the entry point used by the agent loop today;
it now returns the layered memory joined with explicit scope headers,
so the model can see which layer a given instruction came from.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agent.env import home_dir


# ---------------------------------------------------------------------------
# Memory file conventions
# ---------------------------------------------------------------------------

# User-global memory — single file under ~/.kryth/.
USER_MEMORY_NAMES: tuple[str, ...] = ("MEMORY.md", "memory.md")

# Project-scoped memory. First match wins — listed in priority order so
# AGENTS.md (the de-facto standard) and CLAUDE.md (Claude Code's
# convention) take precedence over legacy ai_coder names.
PROJECT_MEMORY_NAMES: tuple[str, ...] = (
    "AGENTS.md",
    "CLAUDE.md",
    "AI_CODER.md",
    "AI-CODER.md",
    "KRYTH.md",
    "kryth.md",
    ".kryth.md",
    ".cursor/rules",
)

# Markers used to detect a project root by walking up from cwd. Order
# is not significant — any one match is enough.
_PROJECT_ROOT_MARKERS = (
    ".git",
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "deno.json",
    "Gemfile",
    ".kryth",
    ".kryth",
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MemoryLayer:
    """One loaded memory file. ``scope`` is ``'user'``, ``'project'``,
    or ``'subdir'``; ``path`` is the absolute path on disk; ``content``
    is the file's text contents."""
    scope: str
    path: Path
    content: str


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _user_memory_dir() -> Path:
    """Where user-global memory lives."""
    return home_dir()


def project_root(start: str | Path = ".") -> Path:
    """Heuristic project root. Walks up from ``start``, returns the
    highest ancestor with a project marker. If any ancestor has a
    ``.git`` directory, returns that ancestor immediately as the
    definitive root. Falls back to ``start`` itself."""
    start_path = Path(start).resolve()
    cur = start_path
    best = start_path
    while True:
        if _is_project_root(cur):
            if (cur / ".git").exists():
                return cur  # .git is the definitive root
            best = cur  # Remember best non-.git root
        parent = cur.parent
        if parent == cur:
            return best
        cur = parent


def _is_project_root(p: Path) -> bool:
    """Memory files are NOT included here on purpose: they can live in
    subdirs as area-specific layers, so detecting them as a "root"
    would shadow the real project root and silently drop the cascade.
    Hard markers only.
    """
    for marker in _PROJECT_ROOT_MARKERS:
        if (p / marker).exists():
            return True
    return False


def _read_text(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Layer loaders
# ---------------------------------------------------------------------------

def _load_user_memory() -> MemoryLayer | None:
    base = _user_memory_dir()
    for name in USER_MEMORY_NAMES:
        p = base / name
        if p.is_file():
            content = _read_text(p)
            if content is not None:
                return MemoryLayer("user", p, content)
    return None


def _load_project_memory(root: Path) -> MemoryLayer | None:
    for name in PROJECT_MEMORY_NAMES:
        p = root / name
        if p.is_file():
            content = _read_text(p)
            if content is not None:
                return MemoryLayer("project", p, content)
    return None


def _load_subdir_memory(start: Path, project_root_path: Path) -> list[MemoryLayer]:
    """Walk from ``start`` up to (but not including) ``project_root_path``,
    collecting any memory files in intermediate directories. Returned in
    outer→inner order so when concatenated the model reads ancestors
    first and the deepest (most specific) layer lands closest to the
    user message."""
    if start == project_root_path:
        return []

    layers: list[MemoryLayer] = []
    cur = start
    while cur != project_root_path:
        for name in PROJECT_MEMORY_NAMES:
            p = cur / name
            if p.is_file():
                content = _read_text(p)
                if content is not None:
                    layers.append(MemoryLayer("subdir", p, content))
                break
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return list(reversed(layers))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_memory_layers(start: str | Path = ".") -> list[MemoryLayer]:
    """Return the ordered list of memory layers visible from ``start``.

    Order: user → project → subdir(outermost→innermost). The caller can
    join them in this order; the model will see general guidance first
    and specific guidance closer to the active task.
    """
    start_path = Path(start).resolve()
    if not start_path.is_dir():
        start_path = start_path.parent

    layers: list[MemoryLayer] = []

    user = _load_user_memory()
    if user is not None:
        layers.append(user)

    root = project_root(start_path)
    proj = _load_project_memory(root)
    if proj is not None:
        layers.append(proj)
        layers.extend(_load_subdir_memory(start_path, root))

    return layers


def load_context_file(start: str | Path = ".", max_chars: int = 8000) -> str:
    """Layered memory joined for direct injection into a system prompt.

    Each layer is preceded by a header naming its scope and path so the
    model can attribute guidance back to its source — and so the user
    can audit ``/memory`` to see exactly what's loaded.

    ``max_chars`` caps the total combined content to prevent large AGENTS.md
    files from bloating the system prompt (~2,000 tok at 8,000 chars).
    """
    layers = load_memory_layers(start)
    if not layers:
        return ""
    parts: list[str] = []
    for layer in layers:
        header = f"[Memory · {layer.scope} · {layer.path}]"
        parts.append(f"{header}\n{layer.content.rstrip()}")
    combined = "\n\n".join(parts)
    if max_chars > 0 and len(combined) > max_chars:
        truncated = len(combined) - max_chars
        combined = combined[:max_chars] + f"\n...[{truncated} chars truncated; use read_file for full content]"
    return combined


def find_context_file(start: str | Path = ".") -> Optional[str]:
    """Legacy entry point — returns just the absolute path of the
    project-scope memory file, or ``None`` if none was found. Kept so
    older callers keep compiling."""
    root = project_root(Path(start).resolve())
    proj = _load_project_memory(root)
    return str(proj.path) if proj is not None else None


# ---------------------------------------------------------------------------
# Git snapshot
# ---------------------------------------------------------------------------

def git_status_snapshot(start: str = ".") -> str:
    try:
        is_repo = subprocess.run(
            ["git", "-C", start, "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if is_repo.returncode != 0:
        return ""

    try:
        status = subprocess.run(
            ["git", "-C", start, "status", "--short", "--branch"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""

    out = (status.stdout or "").strip()
    if not out:
        return "[git] clean working tree"
    lines = out.splitlines()
    if len(lines) > 40:
        lines = lines[:40] + [f"...({len(out.splitlines()) - 40} more)"]
    return "[git status]\n" + "\n".join(lines)


__all__ = [
    "MemoryLayer",
    "USER_MEMORY_NAMES",
    "PROJECT_MEMORY_NAMES",
    "project_root",
    "load_memory_layers",
    "load_context_file",
    "find_context_file",
    "git_status_snapshot",
]

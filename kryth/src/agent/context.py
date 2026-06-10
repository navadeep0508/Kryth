"""Project file map + smart context loading.

Two modes:
1. ``build_project_map()`` — full map (for initial session setup)
2. ``build_focused_map(intent)`` — loads only files relevant to the
   user's intent, skipping unrelated dirs (tests, docs, marketing).
   This is the "smart context" that avoids loading 300 files for a
   bug fix in auth/.
3. ``ProjectSnapshot`` — caches the project map to disk, invalidating
   when source files change (mtime-based). Avoids re-scanning thousands
   of files on every KRYTH run.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import List, Optional

from agent.env import home_dir

# Use orjson for faster JSON serialization when available
try:
    import orjson as _json_lib

    def _json_loads(s):
        return _json_lib.loads(s)

    def _json_dumps(obj):
        return _json_lib.dumps(obj).decode()

except ImportError:
    import json as _json_lib  # type: ignore[no-redef]

    def _json_loads(s):
        return _json_lib.loads(s)

    def _json_dumps(obj):
        return _json_lib.dumps(obj)


IGNORE_DIRS = {
    "node_modules", ".git", "__pycache__", "venv", ".venv", "env",
    "dist", "build", ".next", ".cache", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".idea", ".vscode", "coverage", ".coverage",
    ".eggs", "*.egg-info",
}

SOURCE_SUFFIXES = (
    ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".rb", ".php", ".c", ".h",
    ".cpp", ".hpp", ".cs", ".swift", ".sh", ".yml", ".yaml",
    ".toml", ".json", ".md",
)

MAX_PROJECT_MAP_FILES = 300

# Directories that are skipped in focused mode for non-matching intents
_SKIP_DIRS_BY_INTENT = {
    "fix":     ["docs", "tests", "test", "__tests__", "marketing", "public", "static", "assets"],
    "test":    ["docs", "marketing", "public", "static", "assets"],
    "deploy":  ["tests", "test", "__tests__", "marketing"],
    "docs":    ["tests", "test", "__tests__", "node_modules"],
    "style":   ["tests", "test", "__tests__", "backend", "api", "db", "database"],
    "default": [],
}

_INTENT_KEYWORDS = {
    "fix":     ["fix", "bug", "error", "crash", "debug", "issue", "problem", "broken"],
    "test":    ["test", "spec", "coverage", "pytest", "jest", "vitest"],
    "deploy":  ["deploy", "docker", "kubernetes", "ci", "cd", "pipeline", "production"],
    "docs":    ["readme", "docs", "documentation", "comment"],
    "style":   ["style", "css", "tailwind", "design", "ui", "color", "font"],
}


def _detect_intent(user_input: str) -> str:
    text = user_input.lower()
    for intent, keywords in _INTENT_KEYWORDS.items():
        if any(k in text for k in keywords):
            return intent
    return "default"


def _relevant_dirs(user_input: str) -> Optional[List[str]]:
    """Return a list of directory-name fragments to focus on, or None
    if no focused directories can be inferred."""
    text = user_input.lower()

    # Auth / login area
    if any(k in text for k in ["auth", "login", "signup", "jwt", "session", "password"]):
        return ["auth", "login", "user", "account", "session", "middleware"]

    # Payment area
    if any(k in text for k in ["payment", "stripe", "billing", "subscription"]):
        return ["payment", "billing", "stripe", "subscription", "webhook"]

    # Database / models area
    if any(k in text for k in ["database", "model", "schema", "migration", "query", "orm"]):
        return ["models", "db", "database", "migrations", "schema"]

    # API / routes area
    if any(k in text for k in ["api", "endpoint", "route", "router", "controller"]):
        return ["api", "routes", "router", "controllers", "endpoints", "views"]

    # Frontend / UI area
    if any(k in text for k in ["frontend", "component", "ui", "page", "layout", "navbar"]):
        return ["components", "pages", "views", "layouts", "src", "app"]

    # Testing area
    if any(k in text for k in ["test", "spec", "coverage"]):
        return ["tests", "test", "__tests__", "spec"]

    return None  # no focused dirs — use full map


def build_project_map(directory: str = ".", limit: int = MAX_PROJECT_MAP_FILES) -> str:
    """Return a newline-joined list of source files, capped at ``limit``.

    Files are ranked by (depth ascending, mtime descending) so the cap
    favours top-level structural files over deeply nested generated ones.
    Uses fd for fast discovery when available.
    """
    candidates = []

    # Try fd-based discovery first (2-10x faster for large repos)
    try:
        from agent.retrieval.fd_discovery import discover_files, fd_available
        if fd_available():
            raw_paths = discover_files(directory, extensions=list(SOURCE_SUFFIXES))
            for path in raw_paths:
                depth = path.count(os.sep)
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    mtime = 0.0
                candidates.append((depth, -mtime, path))
            candidates.sort()
            paths = [c[2] for c in candidates[:limit]]
            out = "\n".join(paths)
            if len(candidates) > limit:
                out += f"\n...({len(candidates) - limit} more files; use list_files / glob to explore)"
            return out
    except Exception:
        pass  # Fall through to os.walk

    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]
        for file in files:
            if not file.endswith(SOURCE_SUFFIXES):
                continue
            path = os.path.join(root, file)
            depth = path.count(os.sep)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = 0.0
            candidates.append((depth, -mtime, path))

    candidates.sort()
    paths = [c[2] for c in candidates[:limit]]
    out = "\n".join(paths)
    if len(candidates) > limit:
        out += f"\n...({len(candidates) - limit} more files; use list_files / glob to explore)"
    return out


def build_focused_map(user_input: str, directory: str = ".") -> str:
    """Smart context: load only files relevant to the user's request.

    For a "fix login bug" request, this loads auth/, login/, middleware/
    and skips docs/, tests/, marketing/ — reducing context by 60-90%
    while keeping all relevant code visible.
    """
    intent = _detect_intent(user_input)
    skip_dirs = set(_SKIP_DIRS_BY_INTENT.get(intent, []))
    focus_dirs = _relevant_dirs(user_input)

    candidates = []
    for root, dirs, files in os.walk(directory):
        # Always skip global noise
        dirs[:] = [
            d for d in dirs
            if d not in IGNORE_DIRS and not d.startswith(".")
            and d.lower() not in skip_dirs
        ]

        # In focused mode, deprioritise dirs that don't match focus
        if focus_dirs:
            rel_root = os.path.relpath(root, directory)
            parts = rel_root.lower().replace("\\", "/").split("/")
            is_focused = any(
                any(f in part for f in focus_dirs)
                for part in parts
            )
            priority = 0 if is_focused else 1  # focused files sort first
        else:
            priority = 0

        for file in files:
            if not file.endswith(SOURCE_SUFFIXES):
                continue
            path = os.path.join(root, file)
            depth = path.count(os.sep)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = 0.0
            candidates.append((priority, depth, -mtime, path))

    candidates.sort()
    limit = MAX_PROJECT_MAP_FILES // 2 if focus_dirs else MAX_PROJECT_MAP_FILES
    paths = [c[3] for c in candidates[:limit]]
    out = "\n".join(paths)
    if len(candidates) > limit:
        out += f"\n...({len(candidates) - limit} more files skipped; use list_files to explore)"
    if focus_dirs:
        out = f"[Focused on: {', '.join(focus_dirs)}]\n" + out
    return out


# ---------------------------------------------------------------------------
# Project Snapshot — disk-cached project map with mtime invalidation
# ---------------------------------------------------------------------------

_SNAPSHOT_DIR = home_dir() / "snapshots"
_SNAPSHOT_TTL_SEC = 3600  # re-scan after 1 hour regardless


class ProjectSnapshot:
    """Caches the project map to ~/.kryth/snapshots/<project_hash>.json.

    Invalidation: if ANY source file's mtime is newer than the cache,
    the cache is stale. This is O(N) but fast (stat only, no reads).
    """

    def __init__(self, directory: str = ".") -> None:
        self._dir = os.path.abspath(directory)
        self._cache_path = _SNAPSHOT_DIR / (self._project_key() + ".json")

    def _project_key(self) -> str:
        return hashlib.md5(self._dir.encode()).hexdigest()[:12]

    def _newest_mtime(self) -> float:
        newest = 0.0
        for root, dirs, files in os.walk(self._dir):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]
            for f in files:
                if f.endswith(SOURCE_SUFFIXES):
                    try:
                        m = os.path.getmtime(os.path.join(root, f))
                        if m > newest:
                            newest = m
                    except OSError:
                        pass
        return newest

    def _load_cache(self) -> Optional[dict]:
        if not self._cache_path.exists():
            return None
        try:
            with open(self._cache_path, "r", encoding="utf-8") as f:
                data = _json_loads(f.read())
            # Expired?
            if time.time() - data.get("saved_at", 0) > _SNAPSHOT_TTL_SEC:
                return None
            # Stale?
            newest = self._newest_mtime()
            if newest > data.get("newest_mtime", 0):
                return None
            return data
        except Exception:
            return None

    def _save_cache(self, project_map: str, newest_mtime: float) -> None:
        _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        try:
            payload = _json_dumps({
                "project_map": project_map,
                "newest_mtime": newest_mtime,
                "saved_at": time.time(),
                "directory": self._dir,
            })
            with open(self._cache_path, "w", encoding="utf-8") as f:
                f.write(payload)
        except Exception:
            pass

    def get_or_build(self) -> tuple[str, bool]:
        """Return (project_map, was_cached)."""
        cached = self._load_cache()
        if cached:
            return cached["project_map"], True

        project_map = build_project_map(self._dir)
        newest_mtime = self._newest_mtime()
        self._save_cache(project_map, newest_mtime)
        return project_map, False

    def invalidate(self) -> None:
        try:
            self._cache_path.unlink(missing_ok=True)
        except Exception:
            pass

"""Tree-sitter AST cache layer.

Provides persistent caching of parsed ASTs to avoid reparsing unchanged files.
Integrates with existing cache.py and watcher.py for invalidation.

Architecture:
    File → tree-sitter parse → AST → cache (by xxhash) → reuse

On file change: invalidate cache entry.
On query: check cache first, parse only if missing/stale.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

from agent.retrieval import config as cfg
from agent.retrieval.cache import file_fingerprint, get_cache


# ---------------------------------------------------------------------------
# Tree-sitter integration (optional)
# ---------------------------------------------------------------------------

_HAS_TREE_SITTER = False
try:
    import tree_sitter  # type: ignore[import]
    from tree_sitter import Language, Parser
    _HAS_TREE_SITTER = True
except ImportError:
    pass

# Language detection by file extension
_LANGUAGE_MAP = {
    '.py': 'python',
    '.js': 'javascript',
    '.ts': 'typescript',
    '.jsx': 'javascript',
    '.tsx': 'typescript',
    '.java': 'java',
    '.go': 'go',
    '.rs': 'rust',
    '.c': 'c',
    '.cpp': 'cpp',
    '.h': 'c',
    '.hpp': 'cpp',
    '.rb': 'ruby',
    '.php': 'php',
    '.swift': 'swift',
    '.kt': 'kotlin',
    '.scala': 'scala',
    '.ml': 'ocaml',
    '.mli': 'ocaml',
}

# Cache key prefix
_CACHE_PREFIX = "ast_cache:"


def _get_language_from_path(path: str) -> Optional[str]:
    """Infer tree-sitter language from file extension."""
    ext = os.path.splitext(path)[1].lower()
    return _LANGUAGE_MAP.get(ext)


def _load_language(lang_name: str) -> Optional[Language]:
    """Load tree-sitter language library (lazy, cached)."""
    # In a full implementation, we would load .so/.dll files for each language
    # For now, return None - this is a skeleton that will be completed
    # when tree-sitter language packs are available
    return None


def parse_file(path: str, source_code: Optional[bytes] = None) -> Optional[Any]:
    """Parse a file with tree-sitter, using cache if possible.

    Returns: AST object (tree-sitter Tree) or None if unavailable.
    """
    if not cfg.ENABLE_AST_CACHE or not _HAS_TREE_SITTER:
        return None

    lang_name = _get_language_from_path(path)
    if not lang_name:
        return None

    # Compute fingerprint for cache key
    fp = file_fingerprint(path)
    cache_key = f"{_CACHE_PREFIX}{lang_name}:{fp}"

    cache = get_cache("ast_cache")
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # Cache miss - parse the file
    if source_code is None:
        try:
            with open(path, 'rb') as f:
                source_code = f.read()
        except Exception:
            return None

    if source_code is None:
        return None

    try:
        language = _load_language(lang_name)
        if language is None:
            return None

        parser = Parser()
        parser.set_language(language)
        tree = parser.parse(source_code)
        # Cache the AST (note: tree-sitter trees are not directly serializable,
        # so in production we'd need to serialize or store differently)
        # For now, we'll cache a tuple of (root_node_data, ...) or skip caching
        # the actual AST and just cache the fingerprint to avoid re-parsing
        cache.set(cache_key, True, expire=cfg.CACHE_TTL)  # Mark as parsed
        return tree
    except Exception:
        return None


def invalidate_file(path: str) -> None:
    """Invalidate cached AST for a file (called on file change)."""
    if not cfg.ENABLE_AST_CACHE or not _HAS_TREE_SITTER:
        return

    fp = file_fingerprint(path)
    lang_name = _get_language_from_path(path)
    if not lang_name:
        return

    cache_key = f"{_CACHE_PREFIX}{lang_name}:{fp}"
    cache = get_cache("ast_cache")
    cache.delete(cache_key)


def get_cached_ast(path: str) -> Optional[Any]:
    """Get cached AST for a file, or None if not cached."""
    if not cfg.ENABLE_AST_CACHE or not _HAS_TREE_SITTER:
        return None

    fp = file_fingerprint(path)
    lang_name = _get_language_from_path(path)
    if not lang_name:
        return None

    cache_key = f"{_CACHE_PREFIX}{lang_name}:{fp}"
    cache = get_cache("ast_cache")
    return cache.get(cache_key)


def is_ast_cached(path: str) -> bool:
    """Check if a file's AST is cached."""
    return get_cached_ast(path) is not None


def capabilities() -> Dict[str, bool]:
    """Return AST cache capabilities."""
    return {
        "tree_sitter": _HAS_TREE_SITTER,
        "ast_cache_enabled": cfg.ENABLE_AST_CACHE,
    }
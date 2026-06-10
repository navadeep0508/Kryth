"""Multi-layer cache: xxhash fingerprints + diskcache persistent storage.

Graceful fallback chain:
  xxhash not installed       → hashlib.md5 for fingerprints
  diskcache not installed    → in-memory dict (no persistence)
  orjson not installed       → stdlib json

Usage:
    from agent.retrieval.cache import get_cache, file_fingerprint

    cache = get_cache(".")
    cache.set("my_key", result, expire=3600)
    value = cache.get("my_key")
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any, Optional

from agent.retrieval import config as cfg


_HAS_XXHASH = False
_HAS_DISKCACHE = False
_HAS_ORJSON = False

try:
    import xxhash as _xxhash

    _HAS_XXHASH = True
except ImportError:
    pass

try:
    import diskcache as _diskcache

    _HAS_DISKCACHE = True
except ImportError:
    pass

try:
    import orjson as _orjson

    _HAS_ORJSON = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def fast_hash(data: bytes | str) -> str:
    """Compute a fast non-cryptographic hash. Uses xxhash if available."""
    if isinstance(data, str):
        data = data.encode()
    if _HAS_XXHASH:
        return _xxhash.xxh64(data).hexdigest()
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def file_fingerprint(path: str) -> str:
    """Fingerprint a file: size + mtime + hash of first 4 KB.

    Much faster than hashing the whole file while still detecting most changes.
    Returns empty string on I/O error.
    """
    try:
        stat = os.stat(path)
        size = stat.st_size
        mtime = stat.st_mtime
        with open(path, "rb") as f:
            head = f.read(4096)
        content_hash = fast_hash(head)
        return f"{size}:{mtime:.3f}:{content_hash}"
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------


def dumps(obj: Any) -> bytes:
    """Serialize to bytes. Uses orjson if available (3-10x faster)."""
    if _HAS_ORJSON:
        return _orjson.dumps(obj)
    import json

    return json.dumps(obj).encode()


def loads(data: bytes | str) -> Any:
    """Deserialize. Uses orjson if available."""
    if _HAS_ORJSON:
        return _orjson.loads(data)
    import json

    if isinstance(data, bytes):
        data = data.decode()
    return json.loads(data)


# ---------------------------------------------------------------------------
# Cache backends
# ---------------------------------------------------------------------------


class _MemCache:
    """In-memory cache with TTL. Fallback when diskcache is unavailable."""

    MAX_ITEMS = 2000

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str, default: Any = None) -> Any:
        entry = self._store.get(key)
        if entry is None:
            return default
        value, expires_at = entry
        if time.time() > expires_at:
            del self._store[key]
            return default
        return value

    def set(self, key: str, value: Any, expire: Optional[int] = None) -> None:
        if len(self._store) >= self.MAX_ITEMS:
            oldest_key = min(self._store, key=lambda k: self._store[k][1])
            del self._store[oldest_key]
        ttl = expire if expire is not None else cfg.CACHE_TTL
        self._store[key] = (value, time.time() + ttl)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _home_dir() -> Path:
    try:
        from agent.env import home_dir

        return home_dir()
    except ImportError:
        return Path.home() / ".kryth"


_CACHE_ROOT = _home_dir() / "cache"
_cache_instances: dict[str, Any] = {}


def _project_key(directory: str) -> str:
    return fast_hash(os.path.abspath(directory).encode())[:16]


def get_cache(directory: str = ".") -> Any:
    """Return a cache for the given project directory.

    Returns a ``diskcache.Cache`` instance if the package is installed,
    otherwise a ``_MemCache``. Both share the same get/set/delete/close API.
    """
    if not cfg.ENABLE_CACHE:
        return _MemCache()

    key = _project_key(directory)
    if key in _cache_instances:
        return _cache_instances[key]

    if _HAS_DISKCACHE:
        cache_path = _CACHE_ROOT / key
        cache_path.mkdir(parents=True, exist_ok=True)
        try:
            c = _diskcache.Cache(
                str(cache_path),
                size_limit=cfg.CACHE_SIZE_BYTES,
            )
            _cache_instances[key] = c
            return c
        except Exception:
            pass

    mem = _MemCache()
    _cache_instances[key] = mem
    return mem


def cache_key(*parts: str) -> str:
    """Build a namespaced cache key from multiple parts."""
    return fast_hash(":".join(parts).encode())


def capabilities() -> dict:
    return {
        "xxhash": _HAS_XXHASH,
        "diskcache": _HAS_DISKCACHE,
        "orjson": _HAS_ORJSON,
    }

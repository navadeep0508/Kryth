"""Feature flags and thresholds for the retrieval engine.

All settings are overridable via environment variables.
Settings can also be provided in .kryth/settings.json under "retrieval".
No hardcoded values — every threshold is configurable.
"""
from __future__ import annotations

import os


def _env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.lower() in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------

ENABLE_GRAPHIFY = _env_bool("ENABLE_GRAPHIFY", True)
ENABLE_RIPGREP = _env_bool("ENABLE_RIPGREP", True)
ENABLE_AST_GREP = _env_bool("ENABLE_AST_GREP", True)
ENABLE_FTS = _env_bool("ENABLE_FTS", True)
ENABLE_MMAP = _env_bool("ENABLE_MMAP", True)
ENABLE_ASYNC_IO = _env_bool("ENABLE_ASYNC_IO", True)
ENABLE_CACHE = _env_bool("ENABLE_CACHE", True)
ENABLE_WATCHER = _env_bool("ENABLE_WATCHER", False)

# ---------------------------------------------------------------------------
# Advanced features (new)
# ---------------------------------------------------------------------------

ENABLE_LSP = _env_bool("ENABLE_LSP", True)
ENABLE_SYMBOL_INDEX = _env_bool("ENABLE_SYMBOL_INDEX", True)
ENABLE_AST_CACHE = _env_bool("ENABLE_AST_CACHE", True)
ENABLE_DEP_GRAPH = _env_bool("ENABLE_DEP_GRAPH", True)
ENABLE_COST_OPTIMIZER = _env_bool("ENABLE_COST_OPTIMIZER", True)
ENABLE_PARALLEL_RETRIEVAL = _env_bool("ENABLE_PARALLEL_RETRIEVAL", True)
ENABLE_CONTEXT_COMPRESSION = _env_bool("ENABLE_CONTEXT_COMPRESSION", True)
ENABLE_TELEMETRY = _env_bool("ENABLE_TELEMETRY", True)
ENABLE_ADAPTIVE_ROUTING = _env_bool("ENABLE_ADAPTIVE_ROUTING", True)
ENABLE_REFACTORING_INTELLIGENCE = _env_bool("ENABLE_REFACTORING_INTELLIGENCE", True)
ENABLE_KNOWLEDGE_CACHE = _env_bool("ENABLE_KNOWLEDGE_CACHE", True)

# ---------------------------------------------------------------------------
# File size thresholds (bytes)
# ---------------------------------------------------------------------------

MMAP_THRESHOLD = _env_int("MMAP_THRESHOLD", 1 * 1024 * 1024)       # 1 MB
STREAM_THRESHOLD = _env_int("STREAM_THRESHOLD", 50 * 1024 * 1024)  # 50 MB
MAX_FILE_SIZE = _env_int("MAX_FILE_SIZE", 100 * 1024 * 1024)        # 100 MB

# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

MAX_CONCURRENT_READS = _env_int("MAX_CONCURRENT_READS", 8)

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

# CACHE_SIZE env var is in GB (float); stored as bytes internally
CACHE_SIZE_BYTES = int(_env_float("CACHE_SIZE", 1.0) * 1024 ** 3)
CACHE_TTL = _env_int("CACHE_TTL", 3600)  # seconds

# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------

INDEX_BATCH_SIZE = _env_int("INDEX_BATCH_SIZE", 100)

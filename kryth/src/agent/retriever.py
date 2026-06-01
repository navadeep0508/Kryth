"""Optional semantic retrieval over project source.

Uses ``sentence-transformers`` to embed source chunks and rank them by
cosine similarity to a query. Lazy-loaded so it does not slow REPL
startup — the heavy ``sentence_transformers`` / ``numpy`` import only
happens the first time a caller asks for a retrieval. Failure to import
is fatal-free: ``available()`` returns False and the tool surface falls
back to grep.

Ranking blends cosine similarity with a gentle recency factor: a fresh
file with a slightly weaker semantic match outranks a five-year-old
util with a near-identical embedding. The blend is weighted toward
semantic ``_SIMILARITY_WEIGHT`` so recency tunes order rather than
dominating it.
"""

from __future__ import annotations

import os
import time
from typing import List, Tuple

from agent.context import IGNORE_DIRS, SOURCE_SUFFIXES


_MAX_FILES = 600
_MAX_CHARS_PER_FILE = 4000

# Ranking blend: 0.7 * semantic + 0.3 * recency. Both terms are
# normalised to [0, 1]; the recency term decays exponentially with the
# half-life ``_RECENCY_HALF_LIFE_DAYS``, so files older than ~90 days
# barely contribute and 24-hour-fresh files contribute almost fully.
_SIMILARITY_WEIGHT = 0.7
_RECENCY_WEIGHT = 0.3
_RECENCY_HALF_LIFE_DAYS = 30.0

_model = None
_paths: List[str] = []
_documents: List[str] = []
_embeddings = None
_mtimes: List[float] = []
_indexed_root: str | None = None
_load_error: str | None = None
# Set when index_project hit ``_MAX_FILES`` and there were more
# eligible files we silently dropped. Surfaced via ``status()`` so the
# semantic_search tool can warn the model.
_truncated: bool = False
_total_eligible: int = 0
# When ``True`` the next call to ``retrieve_files`` rebuilds the index
# before answering. Set by ``invalidate()`` when files change on disk.
_dirty: bool = False


def _try_load_model():
    """Import sentence_transformers and load the MiniLM model.

    Returns the model on success, ``None`` on failure (e.g. package not
    installed, model download blocked).
    """
    global _load_error
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except Exception as e:
        _load_error = f"sentence-transformers not installed: {e}"
        return None
    try:
        return SentenceTransformer("all-MiniLM-L6-v2")
    except Exception as e:
        _load_error = f"failed to load embedding model: {e}"
        return None


def available() -> bool:
    """True if the retriever can run (model loadable + numpy importable)."""
    global _model
    if _model is None and _load_error is None:
        _model = _try_load_model()
    return _model is not None


def _iter_source_files(root: str):
    for r, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]
        for f in files:
            if f.endswith(SOURCE_SUFFIXES):
                yield os.path.join(r, f)


def index_project(directory: str = ".") -> int:
    """Build (or rebuild) the embedding index.

    Returns the number of indexed files. No-op + 0 if retriever
    unavailable. ``_truncated`` / ``_total_eligible`` capture how many
    files were available vs how many we actually embedded.
    """
    global _paths, _documents, _embeddings, _mtimes, _indexed_root
    global _truncated, _total_eligible, _dirty

    if not available():
        return 0

    paths: List[str] = []
    documents: List[str] = []
    mtimes: List[float] = []
    total_eligible = 0
    truncated = False
    for path in _iter_source_files(directory):
        total_eligible += 1
        if len(paths) >= _MAX_FILES:
            truncated = True
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(_MAX_CHARS_PER_FILE)
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if not content.strip():
            continue
        paths.append(path)
        documents.append(content)
        mtimes.append(mtime)

    _truncated = truncated
    _total_eligible = total_eligible
    _dirty = False

    if not paths:
        _paths, _documents, _embeddings, _mtimes, _indexed_root = (
            [], [], None, [], directory,
        )
        return 0

    assert _model is not None
    embeddings = _model.encode(
        documents,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    _paths, _documents, _embeddings, _mtimes = paths, documents, embeddings, mtimes
    _indexed_root = directory
    return len(paths)


def _recency_score(mtime: float, now: float) -> float:
    """Exponential decay in [0, 1]. 1.0 at now, 0.5 at half-life, ~0
    past ~3 half-lives. ``now`` is passed in so a single retrieval call
    uses one consistent reference time for all files.
    """
    age_seconds = max(0.0, now - mtime)
    age_days = age_seconds / 86_400.0
    # 0.5 ** (age / half_life) → exponential half-life decay.
    return 0.5 ** (age_days / _RECENCY_HALF_LIFE_DAYS)


def retrieve_files(
    query: str, top_k: int = 5, directory: str = "."
) -> List[Tuple[str, float]]:
    """Return up to ``top_k`` (path, blended_score) results.

    Blended score: ``0.7 * cosine_similarity + 0.3 * recency_decay``.
    Lazy-builds the index on first call, or rebuilds after ``invalidate()``.
    Empty list if retriever is unavailable or project is empty.
    """
    if not available():
        return []

    if _embeddings is None or _indexed_root != directory or _dirty:
        if index_project(directory) == 0:
            return []

    assert _model is not None
    query_emb = _model.encode(
        [query],
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )[0]

    sims = (_embeddings @ query_emb).tolist()  # type: ignore[operator]

    now = time.time()
    blended: list[tuple[str, float]] = []
    for path, sim, mtime in zip(_paths, sims, _mtimes):
        recency = _recency_score(mtime, now)
        score = _SIMILARITY_WEIGHT * sim + _RECENCY_WEIGHT * recency
        blended.append((path, score))

    blended.sort(key=lambda pair: pair[1], reverse=True)
    return blended[:top_k]


def invalidate() -> None:
    """Mark the index stale so the next ``retrieve_files`` rebuilds.

    Cheap call site — just flips a flag. The actual re-embed happens
    only if/when someone queries. Use after file mutations so newly
    created or edited files appear in subsequent semantic searches.
    """
    global _dirty
    _dirty = True


def truncated() -> bool:
    """True when the last index build dropped files past ``_MAX_FILES``."""
    return _truncated


def total_eligible() -> int:
    """Number of eligible source files seen during the last index build,
    including any that were dropped due to ``_MAX_FILES``."""
    return _total_eligible


def status() -> str:
    if _model is None and _load_error:
        return f"retriever: unavailable ({_load_error})"
    if _embeddings is None:
        return "retriever: idle (no index yet)"
    base = f"retriever: indexed {len(_paths)} files under {_indexed_root}"
    if _truncated:
        base += (
            f" (truncated — {_total_eligible} eligible, "
            f"cap {_MAX_FILES})"
        )
    if _dirty:
        base += " · stale, will rebuild on next query"
    return base

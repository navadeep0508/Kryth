"""Vector code memory — lightweight cosine similarity store.

Backend cascade (picks the best available):
  1. chromadb  — persistent vector DB (pip install chromadb)
  2. numpy     — in-memory cosine similarity (pip install numpy)
  3. BM25      — term-frequency fallback (always available, no deps)

All backends expose the same interface:
    store = VectorStore(directory=".")
    store.add(chunks)          # chunks from semantic_index.chunk_file()
    results = store.search("login auth", top_k=8)
    # → [{"file": ..., "name": ..., "score": ..., "sig": ...}]

The store auto-selects the best backend and degrades gracefully.
Token cost: stored as text chunks, retrieval returns ≤top_k items.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

def _has_chromadb() -> bool:
    try:
        import chromadb  # type: ignore
        return True
    except ImportError:
        return False


def _has_numpy() -> bool:
    try:
        import numpy  # type: ignore
        return True
    except ImportError:
        return False


def backend_name() -> str:
    if _has_chromadb():
        return "chromadb"
    if _has_numpy():
        return "numpy"
    return "bm25"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset({
    "the", "and", "for", "not", "with", "this", "that", "self", "cls",
    "def", "class", "return", "import", "from", "pass", "none", "true",
    "false", "else", "elif", "if", "in", "is", "or", "as", "try",
    "except", "finally", "raise", "print", "str", "int", "list", "dict",
})

_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _tokenize(text: str) -> list[str]:
    raw = re.split(r"[\s\W]+", text.lower())
    toks: list[str] = []
    for t in raw:
        if not t or len(t) < 2:
            continue
        for part in t.split("_"):
            for sub in _CAMEL_RE.sub(" ", part).lower().split():
                if sub and len(sub) >= 2 and sub not in _STOPWORDS:
                    toks.append(sub)
    return toks


def _chunk_text(chunk: dict) -> str:
    parts = [
        chunk.get("type", ""),
        chunk.get("name", ""),
        chunk.get("sig", ""),
        " ".join(chunk.get("tokens", [])),
    ]
    return " ".join(p for p in parts if p)


def _home_dir() -> Path:
    try:
        from agent.env import home_dir as _h
        return _h()
    except Exception:
        return Path.home() / ".kryth"


# ---------------------------------------------------------------------------
# BM25 backend (always available)
# ---------------------------------------------------------------------------

class _BM25Backend:
    K1 = 1.5
    B  = 0.75

    def __init__(self) -> None:
        self._chunks: list[dict] = []
        self._tokens: list[list[str]] = []

    def add(self, chunks: list[dict]) -> None:
        for c in chunks:
            text = _chunk_text(c)
            self._chunks.append(c)
            self._tokens.append(_tokenize(text))

    def search(self, query: str, top_k: int = 8) -> list[dict]:
        if not self._chunks:
            return []
        q_toks = _tokenize(query)
        if not q_toks:
            return []

        # Compute IDF
        import math
        n = len(self._chunks)
        df: dict[str, int] = {}
        for toks in self._tokens:
            for tok in set(toks):
                df[tok] = df.get(tok, 0) + 1

        avg_dl = sum(len(t) for t in self._tokens) / max(n, 1)
        scores: list[tuple[dict, float]] = []

        for i, (chunk, toks) in enumerate(zip(self._chunks, self._tokens)):
            dl = len(toks)
            norm = 1 - self.B + self.B * (dl / max(avg_dl, 1))
            tf_map: dict[str, int] = {}
            for tok in toks:
                tf_map[tok] = tf_map.get(tok, 0) + 1

            score = 0.0
            for q in q_toks:
                freq = df.get(q, 0)
                if freq == 0:
                    continue
                idf = math.log((n - freq + 0.5) / (freq + 0.5) + 1)
                tf = tf_map.get(q, 0)
                score += idf * (tf * (self.K1 + 1)) / (tf + self.K1 * norm)
            if score > 0:
                scores.append((chunk, score))

        scores.sort(key=lambda x: -x[1])
        return [
            {**c, "score": round(s, 3)}
            for c, s in scores[:top_k]
        ]

    def count(self) -> int:
        return len(self._chunks)


# ---------------------------------------------------------------------------
# NumPy backend (cosine similarity)
# ---------------------------------------------------------------------------

class _NumpyBackend:
    """TF-IDF + cosine similarity using numpy (no ML model needed)."""

    def __init__(self) -> None:
        self._chunks: list[dict] = []
        self._texts: list[str] = []
        self._matrix = None  # np.ndarray shape (n, vocab)
        self._vocab: dict[str, int] = {}
        self._dirty = False

    def add(self, chunks: list[dict]) -> None:
        for c in chunks:
            self._chunks.append(c)
            self._texts.append(_chunk_text(c))
        self._dirty = True

    def _build_matrix(self) -> None:
        import numpy as np
        import math

        n = len(self._texts)
        if n == 0:
            self._matrix = np.zeros((0, 0))
            return

        tokenized = [_tokenize(t) for t in self._texts]

        # Build vocabulary
        vocab: dict[str, int] = {}
        for toks in tokenized:
            for tok in toks:
                if tok not in vocab:
                    vocab[tok] = len(vocab)
        self._vocab = vocab

        # Build TF matrix
        v = len(vocab)
        tf = np.zeros((n, v), dtype=np.float32)
        for i, toks in enumerate(tokenized):
            for tok in toks:
                j = vocab.get(tok)
                if j is not None:
                    tf[i, j] += 1
            dl = tf[i].sum()
            if dl > 0:
                tf[i] /= dl

        # IDF
        df = (tf > 0).sum(axis=0)
        idf = np.log((n + 1) / (df + 1)) + 1.0

        # TF-IDF
        self._matrix = tf * idf

        # Normalize rows
        norms = np.linalg.norm(self._matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self._matrix /= norms
        self._dirty = False

    def search(self, query: str, top_k: int = 8) -> list[dict]:
        import numpy as np

        if not self._chunks:
            return []
        if self._dirty:
            self._build_matrix()

        q_toks = _tokenize(query)
        if not q_toks or self._matrix is None or self._matrix.shape[0] == 0:
            return []

        # Build query vector
        v = len(self._vocab)
        q_vec = np.zeros(v, dtype=np.float32)
        for tok in q_toks:
            j = self._vocab.get(tok)
            if j is not None:
                q_vec[j] += 1.0
        norm = np.linalg.norm(q_vec)
        if norm == 0:
            return []
        q_vec /= norm

        sims = self._matrix @ q_vec
        top_idx = np.argsort(-sims)[:top_k]

        results = []
        for i in top_idx:
            s = float(sims[i])
            if s > 0.01:
                results.append({**self._chunks[i], "score": round(s, 4)})
        return results

    def count(self) -> int:
        return len(self._chunks)


# ---------------------------------------------------------------------------
# ChromaDB backend (persistent, best quality)
# ---------------------------------------------------------------------------

class _ChromaBackend:
    """Uses chromadb for persistent vector storage with auto-embeddings."""

    def __init__(self, persist_dir: str) -> None:
        import chromadb  # type: ignore
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._col = self._client.get_or_create_collection(
            name="kryth_code",
            metadata={"hnsw:space": "cosine"},
        )

    def add(self, chunks: list[dict]) -> None:
        if not chunks:
            return
        docs, ids, metas = [], [], []
        for i, c in enumerate(chunks):
            text = _chunk_text(c)
            uid = hashlib.md5(
                f"{c.get('file','')}:{c.get('line',0)}:{c.get('name','')}".encode()
            ).hexdigest()
            docs.append(text)
            ids.append(uid)
            metas.append({
                "file":  c.get("file", ""),
                "type":  c.get("type", ""),
                "name":  c.get("name", ""),
                "line":  c.get("line", 0),
                "sig":   c.get("sig", "")[:200],
            })
        # Upsert in batches to avoid OOM on large repos
        batch = 500
        for start in range(0, len(docs), batch):
            self._col.upsert(
                documents=docs[start:start+batch],
                ids=ids[start:start+batch],
                metadatas=metas[start:start+batch],
            )

    def search(self, query: str, top_k: int = 8) -> list[dict]:
        if self._col.count() == 0:
            return []
        try:
            res = self._col.query(query_texts=[query], n_results=min(top_k, self._col.count()))
            results = []
            for meta, dist in zip(res["metadatas"][0], res["distances"][0]):
                results.append({**meta, "score": round(1.0 - dist, 4)})
            return results
        except Exception:
            return []

    def count(self) -> int:
        try:
            return self._col.count()
        except Exception:
            return 0


# ---------------------------------------------------------------------------
# VectorStore — public API
# ---------------------------------------------------------------------------

class VectorStore:
    """Unified vector code memory.

    Picks the best available backend and provides a single interface.
    """

    def __init__(self, directory: str = ".") -> None:
        self._dir = os.path.abspath(directory)
        self._backend_name = backend_name()

        if self._backend_name == "chromadb":
            persist = str(_home_dir() / "vector_store" /
                          hashlib.md5(self._dir.encode()).hexdigest()[:12])
            os.makedirs(persist, exist_ok=True)
            try:
                self._backend = _ChromaBackend(persist)
            except Exception:
                self._backend_name = "numpy" if _has_numpy() else "bm25"
                self._backend = _NumpyBackend() if self._backend_name == "numpy" else _BM25Backend()
        elif self._backend_name == "numpy":
            self._backend = _NumpyBackend()
        else:
            self._backend = _BM25Backend()

    def add(self, chunks: list[dict]) -> None:
        """Add code chunks to the vector store."""
        self._backend.add(chunks)

    def search(self, query: str, top_k: int = 8) -> list[dict]:
        """Search for semantically relevant code chunks."""
        return self._backend.search(query, top_k=top_k)

    def search_files(self, query: str, top_k: int = 8) -> list[str]:
        """Return top-k file paths (deduplicated) by relevance."""
        results = self.search(query, top_k=top_k * 2)
        seen: set[str] = set()
        files: list[str] = []
        for r in results:
            f = r.get("file", "")
            if f and f not in seen:
                seen.add(f)
                files.append(f)
            if len(files) >= top_k:
                break
        return files

    @property
    def backend(self) -> str:
        return self._backend_name

    def count(self) -> int:
        return self._backend.count()

    def status(self) -> dict:
        return {
            "backend":   self._backend_name,
            "count":     self.count(),
            "directory": self._dir,
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_stores: dict[str, VectorStore] = {}


def get_store(directory: str = ".") -> VectorStore:
    key = os.path.abspath(directory)
    if key not in _stores:
        _stores[key] = VectorStore(key)
    return _stores[key]


def search(query: str, directory: str = ".", top_k: int = 8) -> list[dict]:
    """Convenience function: search the vector store for a directory."""
    return get_store(directory).search(query, top_k=top_k)


def search_files(query: str, directory: str = ".", top_k: int = 8) -> list[str]:
    """Convenience function: return top-k relevant files."""
    return get_store(directory).search_files(query, top_k=top_k)

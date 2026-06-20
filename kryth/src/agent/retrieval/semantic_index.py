"""Semantic code retrieval — BM25 scoring with AST-aware chunking.

Provides fast, dependency-free semantic search over a codebase:
- AST chunking: Python via stdlib `ast`; JS/TS/Go via regex
- BM25 term-frequency scoring (no numpy, no ML deps required)
- Optional dense embeddings via sentence-transformers (if installed)
- Disk-cached chunk index with per-file mtime invalidation

Usage:
    from agent.retrieval.semantic_index import SemanticIndex
    idx = SemanticIndex(directory=".")
    results = idx.query("login authentication jwt", top_k=8)
    # → [{"file": "auth.py", "name": "login", "type": "function", "score": 4.2}, ...]

Chunk schema:
    {
        "file":     str,       # relative path
        "type":     str,       # "function"|"class"|"module"|"import_block"
        "name":     str,       # symbol name or "module"
        "line":     int,       # start line (1-indexed)
        "end_line": int,       # end line inclusive
        "tokens":   list[str], # bag-of-words (lowercased)
        "sig":      str,       # first line / signature (for display)
    }
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_INDEX_VERSION = 2
_STOPWORDS = frozenset({
    "the", "and", "for", "not", "with", "this", "that", "have", "from",
    "are", "was", "were", "been", "will", "would", "could", "should",
    "add", "get", "set", "use", "new", "all", "any", "but", "can",
    "self", "cls", "def", "class", "return", "import", "from", "pass",
    "none", "true", "false", "else", "elif", "if", "in", "is", "or",
    "and", "not", "as", "try", "except", "finally", "with", "raise",
    "print", "str", "int", "list", "dict", "bool", "type", "var",
    "let", "const", "function", "async", "await", "export", "default",
})

_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

# Extensions to index
_SOURCE_EXTS = frozenset({
    ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs",
    ".go", ".rs", ".java", ".kt", ".rb", ".php",
    ".c", ".h", ".cpp", ".hpp", ".cs", ".swift",
})

_IGNORE_DIRS = frozenset({
    "node_modules", ".git", "__pycache__", "venv", ".venv", "env",
    "dist", "build", ".next", ".cache", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".idea", ".vscode", "coverage", ".coverage",
})


# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    """Split text into lowercase tokens, splitting CamelCase and snake_case."""
    # Split on whitespace and punctuation
    raw = re.split(r"[\s\W]+", text.lower())
    tokens: list[str] = []
    for tok in raw:
        if not tok or len(tok) < 2:
            continue
        # Split snake_case
        parts = tok.split("_")
        for part in parts:
            # Split CamelCase
            sub = _CAMEL_SPLIT_RE.sub(" ", part).lower().split()
            for s in sub:
                if s and len(s) >= 2 and s not in _STOPWORDS:
                    tokens.append(s)
    return tokens


# ---------------------------------------------------------------------------
# AST chunkers
# ---------------------------------------------------------------------------

def _chunk_python(path: str, source: str) -> list[dict]:
    """Extract chunks from a Python file using stdlib ast."""
    chunks: list[dict] = []
    lines = source.splitlines()
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        # Fallback: return the whole file as one chunk
        return [{
            "file": path, "type": "module", "name": "module",
            "line": 1, "end_line": len(lines),
            "tokens": _tokenize(source[:2000]),
            "sig": lines[0] if lines else "",
        }]

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end_line = getattr(node, "end_lineno", node.lineno)
            body_text = "\n".join(lines[node.lineno - 1:end_line])
            # Build a token set from: name, args, docstring, body (capped)
            args = [a.arg for a in node.args.args]
            doc = ast.get_docstring(node) or ""
            tokens = _tokenize(node.name + " " + " ".join(args) + " " + doc + " " + body_text[:500])
            sig = lines[node.lineno - 1].strip() if node.lineno <= len(lines) else ""
            chunks.append({
                "file": path,
                "type": "function",
                "name": node.name,
                "line": node.lineno,
                "end_line": end_line,
                "tokens": tokens,
                "sig": sig,
            })

        elif isinstance(node, ast.ClassDef):
            end_line = getattr(node, "end_lineno", node.lineno)
            body_text = "\n".join(lines[node.lineno - 1:min(node.lineno + 5, end_line)])
            doc = ast.get_docstring(node) or ""
            bases = [ast.unparse(b) if hasattr(ast, "unparse") else "" for b in node.bases]
            tokens = _tokenize(node.name + " " + " ".join(bases) + " " + doc + " " + body_text)
            sig = lines[node.lineno - 1].strip() if node.lineno <= len(lines) else ""
            chunks.append({
                "file": path,
                "type": "class",
                "name": node.name,
                "line": node.lineno,
                "end_line": end_line,
                "tokens": tokens,
                "sig": sig,
            })

    # Also add an import-block chunk if there are top-level imports
    imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    if imports:
        import_text = " ".join(
            ast.unparse(n) if hasattr(ast, "unparse") else ""
            for n in imports[:20]
        )
        chunks.append({
            "file": path,
            "type": "import_block",
            "name": "imports",
            "line": 1,
            "end_line": max(getattr(n, "lineno", 1) for n in imports),
            "tokens": _tokenize(import_text),
            "sig": import_text[:120],
        })

    # Module-level chunk (file name + first 10 lines)
    header = "\n".join(lines[:10])
    chunks.append({
        "file": path,
        "type": "module",
        "name": os.path.splitext(os.path.basename(path))[0],
        "line": 1,
        "end_line": len(lines),
        "tokens": _tokenize(os.path.basename(path) + " " + header),
        "sig": lines[0] if lines else "",
    })
    return chunks


# Regex-based chunker for JS/TS/Go/Rust/other
_JS_FUNC_RE = re.compile(
    r"^(?:export\s+)?(?:async\s+)?(?:function\s+(\w+)|"
    r"(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\()",
    re.M,
)
_JS_CLASS_RE = re.compile(r"^(?:export\s+)?class\s+(\w+)", re.M)
_GO_FUNC_RE  = re.compile(r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(", re.M)
_RUST_FN_RE  = re.compile(r"^(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*\(", re.M)

def _chunk_generic(path: str, source: str) -> list[dict]:
    """Regex-based chunking for JS/TS/Go/Rust."""
    lines = source.splitlines()
    ext = os.path.splitext(path)[1].lower()
    chunks: list[dict] = []

    if ext in (".js", ".ts", ".jsx", ".tsx", ".mjs"):
        for m in _JS_FUNC_RE.finditer(source):
            name = m.group(1) or m.group(2) or "anon"
            lineno = source[:m.start()].count("\n") + 1
            end = min(lineno + 30, len(lines))
            body = "\n".join(lines[lineno - 1:end])
            chunks.append({
                "file": path, "type": "function", "name": name,
                "line": lineno, "end_line": end,
                "tokens": _tokenize(name + " " + body[:400]),
                "sig": m.group(0).strip()[:100],
            })
        for m in _JS_CLASS_RE.finditer(source):
            name = m.group(1)
            lineno = source[:m.start()].count("\n") + 1
            end = min(lineno + 50, len(lines))
            chunks.append({
                "file": path, "type": "class", "name": name,
                "line": lineno, "end_line": end,
                "tokens": _tokenize(name + " " + "\n".join(lines[lineno:lineno+10])),
                "sig": m.group(0).strip()[:100],
            })

    elif ext == ".go":
        for m in _GO_FUNC_RE.finditer(source):
            name = m.group(1)
            lineno = source[:m.start()].count("\n") + 1
            end = min(lineno + 30, len(lines))
            body = "\n".join(lines[lineno - 1:end])
            chunks.append({
                "file": path, "type": "function", "name": name,
                "line": lineno, "end_line": end,
                "tokens": _tokenize(name + " " + body[:400]),
                "sig": m.group(0).strip()[:100],
            })

    elif ext == ".rs":
        for m in _RUST_FN_RE.finditer(source):
            name = m.group(1)
            lineno = source[:m.start()].count("\n") + 1
            end = min(lineno + 30, len(lines))
            body = "\n".join(lines[lineno - 1:end])
            chunks.append({
                "file": path, "type": "function", "name": name,
                "line": lineno, "end_line": end,
                "tokens": _tokenize(name + " " + body[:400]),
                "sig": m.group(0).strip()[:100],
            })

    # Always add a module chunk
    chunks.append({
        "file": path, "type": "module",
        "name": os.path.splitext(os.path.basename(path))[0],
        "line": 1, "end_line": len(lines),
        "tokens": _tokenize(os.path.basename(path) + " " + "\n".join(lines[:8])),
        "sig": lines[0] if lines else "",
    })
    return chunks


def _chunk_file(path: str) -> list[dict]:
    """Dispatch to the right chunker for the file's language."""
    ext = os.path.splitext(path)[1].lower()
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            source = f.read()
    except OSError:
        return []

    if ext == ".py":
        return _chunk_python(path, source)
    elif ext in _SOURCE_EXTS:
        return _chunk_generic(path, source)
    return []


# ---------------------------------------------------------------------------
# BM25 scorer
# ---------------------------------------------------------------------------

class _BM25:
    """Okapi BM25 scoring over a collection of chunks."""

    K1 = 1.5
    B  = 0.75

    def __init__(self, chunks: list[dict]) -> None:
        self._chunks = chunks
        n = len(chunks)
        if n == 0:
            self._avg_dl = 1.0
            self._idf: dict[str, float] = {}
            return

        # Build IDF
        df: dict[str, int] = {}
        lengths: list[int] = []
        for c in chunks:
            seen = set()
            for tok in c["tokens"]:
                if tok not in seen:
                    df[tok] = df.get(tok, 0) + 1
                    seen.add(tok)
            lengths.append(len(c["tokens"]))

        self._avg_dl = sum(lengths) / max(len(lengths), 1)
        self._idf = {
            tok: math.log((n - freq + 0.5) / (freq + 0.5) + 1)
            for tok, freq in df.items()
        }

    def score(self, query_tokens: list[str], chunk: dict) -> float:
        dl = len(chunk["tokens"])
        norm = 1 - self.B + self.B * (dl / max(self._avg_dl, 1))
        tf_map: dict[str, int] = {}
        for tok in chunk["tokens"]:
            tf_map[tok] = tf_map.get(tok, 0) + 1

        total = 0.0
        for q in query_tokens:
            if q not in self._idf:
                continue
            tf = tf_map.get(q, 0)
            total += self._idf[q] * (tf * (self.K1 + 1)) / (tf + self.K1 * norm)
        return total

    def search(self, query: str, top_k: int = 8) -> list[tuple[dict, float]]:
        q_tokens = _tokenize(query)
        if not q_tokens or not self._chunks:
            return []
        scored = [(c, self.score(q_tokens, c)) for c in self._chunks]
        scored.sort(key=lambda x: -x[1])
        return [(c, s) for c, s in scored[:top_k] if s > 0]


# ---------------------------------------------------------------------------
# Dense embedding search (optional, graceful fallback)
# ---------------------------------------------------------------------------

_HAS_EMBEDDINGS = False
_embed_model = None

def _try_load_embeddings():
    global _HAS_EMBEDDINGS, _embed_model
    if _HAS_EMBEDDINGS or _embed_model is not None:
        return _HAS_EMBEDDINGS
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        _embed_model = SentenceTransformer("all-MiniLM-L6-v2")
        _HAS_EMBEDDINGS = True
    except Exception:
        _HAS_EMBEDDINGS = False
    return _HAS_EMBEDDINGS


def _embed(texts: list[str]):
    """Embed texts using sentence-transformers. Returns numpy array or None."""
    if not _try_load_embeddings() or _embed_model is None:
        return None
    try:
        return _embed_model.encode(texts, show_progress_bar=False, batch_size=64)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Semantic Index
# ---------------------------------------------------------------------------

def _home_dir() -> Path:
    try:
        from agent.env import home_dir as _h
        return _h()
    except Exception:
        return Path.home() / ".kryth"


class SemanticIndex:
    """Per-project semantic code index with BM25 + optional embeddings.

    Lifecycle:
        idx = SemanticIndex(directory=".")
        idx.build()          # or auto-build on first query
        results = idx.query("login auth", top_k=8)
    """

    def __init__(self, directory: str = ".") -> None:
        self._dir = os.path.abspath(directory)
        self._cache_dir = _home_dir() / "semantic_index"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        key = hashlib.md5(self._dir.encode()).hexdigest()[:12]
        self._cache_path = self._cache_dir / f"{key}.json"
        self._chunks: list[dict] = []
        self._bm25: Optional[_BM25] = None
        self._embeddings = None  # np.ndarray or None
        self._loaded = False
        self._file_mtimes: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Build / load
    # ------------------------------------------------------------------

    def _load_cache(self) -> bool:
        if not self._cache_path.exists():
            return False
        try:
            with open(self._cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("version") != _INDEX_VERSION:
                return False
            if data.get("directory") != self._dir:
                return False
            self._chunks = data["chunks"]
            self._file_mtimes = data.get("mtimes", {})
            self._bm25 = _BM25(self._chunks)
            self._loaded = True
            return True
        except Exception:
            return False

    def _save_cache(self) -> None:
        try:
            payload = {
                "version":   _INDEX_VERSION,
                "directory": self._dir,
                "chunks":    self._chunks,
                "mtimes":    self._file_mtimes,
                "built_at":  time.time(),
            }
            with open(self._cache_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, separators=(",", ":"))
        except Exception:
            pass

    def _is_stale(self) -> bool:
        """Return True if any indexed file has changed since last build."""
        for path, saved_mtime in self._file_mtimes.items():
            try:
                if os.path.getmtime(path) != saved_mtime:
                    return True
            except OSError:
                return True  # file removed
        return False

    def build(self, force: bool = False) -> int:
        """(Re)build the index. Returns number of chunks indexed."""
        if not force and self._load_cache() and not self._is_stale():
            return len(self._chunks)

        chunks: list[dict] = []
        mtimes: dict[str, float] = {}

        for root, dirs, files in os.walk(self._dir):
            dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS and not d.startswith(".")]
            for fname in files:
                if not any(fname.endswith(ext) for ext in _SOURCE_EXTS):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    mtime = os.path.getmtime(fpath)
                except OSError:
                    continue
                file_chunks = _chunk_file(fpath)
                chunks.extend(file_chunks)
                mtimes[fpath] = mtime

        self._chunks = chunks
        self._file_mtimes = mtimes
        self._bm25 = _BM25(chunks)
        self._loaded = True
        self._save_cache()
        return len(chunks)

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.build()

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(
        self,
        query: str,
        top_k: int = 8,
        file_filter: Optional[str] = None,
    ) -> list[dict]:
        """Return top-k most relevant chunks for the query.

        Each result dict includes the original chunk fields plus "score".
        Deduplicates by file: at most 2 chunks per file in results.
        """
        self._ensure_loaded()
        if not self._chunks or not query.strip():
            return []

        bm25 = self._bm25 or _BM25(self._chunks)
        raw = bm25.search(query, top_k=top_k * 3)

        # Filter by file if requested
        if file_filter:
            raw = [(c, s) for c, s in raw if file_filter in c["file"]]

        # Deduplicate: max 2 chunks per file
        seen_files: dict[str, int] = {}
        results: list[dict] = []
        for chunk, score in raw:
            f = chunk["file"]
            if seen_files.get(f, 0) >= 2:
                continue
            seen_files[f] = seen_files.get(f, 0) + 1
            results.append({**chunk, "score": round(score, 3)})
            if len(results) >= top_k:
                break

        return results

    def query_files(self, query: str, top_k: int = 8) -> list[str]:
        """Return top-k file paths (deduplicated) for the query."""
        chunks = self.query(query, top_k=top_k * 2)
        seen: set[str] = set()
        files: list[str] = []
        for c in chunks:
            f = c["file"]
            if f not in seen:
                seen.add(f)
                files.append(f)
            if len(files) >= top_k:
                break
        return files

    def status(self) -> dict:
        """Return index status summary."""
        return {
            "directory":    self._dir,
            "chunks":       len(self._chunks),
            "files":        len(self._file_mtimes),
            "loaded":       self._loaded,
            "has_embeddings": _HAS_EMBEDDINGS,
            "cache_path":   str(self._cache_path),
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_instances: dict[str, SemanticIndex] = {}


def get_index(directory: str = ".") -> SemanticIndex:
    key = os.path.abspath(directory)
    if key not in _instances:
        _instances[key] = SemanticIndex(key)
    return _instances[key]


def query(user_input: str, directory: str = ".", top_k: int = 8) -> list[dict]:
    """Convenience function: query the semantic index for a directory."""
    return get_index(directory).query(user_input, top_k=top_k)


def query_files(user_input: str, directory: str = ".", top_k: int = 8) -> list[str]:
    """Convenience function: return top-k relevant file paths."""
    return get_index(directory).query_files(user_input, top_k=top_k)

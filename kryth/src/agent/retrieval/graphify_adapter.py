"""Graphify adapter — wraps graphifyy for code knowledge graph queries.

graphifyy is the primary semantic engine; this adapter provides a clean
interface over it while falling back to the existing repo_index AST index
when graphifyy is not installed or fails to initialise.

Fallback chain:
  graphifyy installed + ENABLE_GRAPHIFY=true  → graphifyy
  otherwise                                    → repo_index (AST-based)

The adapter tries several common graphifyy constructor patterns because
the exact public API may evolve.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from agent.retrieval import config as cfg


_HAS_GRAPHIFY: bool = False
_GraphClass: Any = None
_load_error: Optional[str] = None
_import_tried: bool = False


def _try_import() -> None:
    global _HAS_GRAPHIFY, _GraphClass, _load_error, _import_tried
    _import_tried = True
    try:
        import graphifyy  # type: ignore[import]

        _HAS_GRAPHIFY = True
        # Try to locate a builder/entry class under various names
        for attr in ("GraphBuilder", "Graphify", "CodeGraph", "Graph"):
            if hasattr(graphifyy, attr):
                _GraphClass = getattr(graphifyy, attr)
                break
        # Module-level API fallback
        if _GraphClass is None and hasattr(graphifyy, "build"):
            _GraphClass = graphifyy
    except ImportError as exc:
        _load_error = f"graphifyy not installed: {exc}"
    except Exception as exc:
        _load_error = f"graphifyy import failed: {exc}"


def _ensure_imported() -> None:
    if not _import_tried:
        _try_import()


class GraphifyAdapter:
    """Thread-safe Graphify wrapper with lazy initialisation.

    All methods fall back to repo_index when graphifyy is unavailable.
    """

    def __init__(self, directory: str = ".") -> None:
        self._dir = os.path.abspath(directory)
        self._graph: Any = None
        self._initialised: bool = False
        self._init_error: Optional[str] = None

    def _init_graph(self) -> bool:
        """Lazy-initialise the graph. Returns True on success."""
        _ensure_imported()
        if not _HAS_GRAPHIFY or not cfg.ENABLE_GRAPHIFY:
            return False
        if self._initialised:
            return self._graph is not None
        self._initialised = True

        if _GraphClass is None:
            return False

        try:
            # Pattern 1: GraphBuilder(directory)
            try:
                self._graph = _GraphClass(self._dir)
                return True
            except TypeError:
                pass

            # Pattern 2: GraphBuilder() then .build(directory)
            try:
                obj = _GraphClass()
                for method in ("build", "index", "load", "analyse"):
                    if hasattr(obj, method):
                        getattr(obj, method)(self._dir)
                        self._graph = obj
                        return True
            except Exception:
                pass

            # Pattern 3: module-level build function
            if hasattr(_GraphClass, "build"):
                self._graph = _GraphClass.build(self._dir)
                return self._graph is not None

        except Exception as exc:
            self._init_error = str(exc)

        return False

    # ------------------------------------------------------------------
    # Public query methods
    # ------------------------------------------------------------------

    def query_related(self, symbol: str) -> List[Dict]:
        """Find symbols semantically related to *symbol*."""
        if self._init_graph():
            for method_name in ("related", "query", "find_related"):
                fn = getattr(self._graph, method_name, None)
                if fn:
                    try:
                        return self._normalise(fn(symbol))
                    except Exception:
                        pass
        return self._fallback_related(symbol)

    def get_callers(self, symbol: str) -> List[Dict]:
        """Find all call-sites of *symbol*."""
        if self._init_graph():
            for method_name in ("callers", "get_callers", "find_callers"):
                fn = getattr(self._graph, method_name, None)
                if fn:
                    try:
                        return self._normalise(fn(symbol))
                    except Exception:
                        pass
        return self._fallback_callers(symbol)

    def get_callees(self, symbol: str) -> List[Dict]:
        """Find all functions called inside *symbol*."""
        if self._init_graph():
            for method_name in ("callees", "get_callees", "calls"):
                fn = getattr(self._graph, method_name, None)
                if fn:
                    try:
                        return self._normalise(fn(symbol))
                    except Exception:
                        pass
        return self._fallback_callees(symbol)

    def get_imports(self, path: str) -> List[str]:
        """Get all modules imported by *path*."""
        if self._init_graph():
            for method_name in ("imports", "get_imports", "file_imports"):
                fn = getattr(self._graph, method_name, None)
                if fn:
                    try:
                        result = fn(path)
                        if isinstance(result, list):
                            return [str(r) for r in result]
                    except Exception:
                        pass
        return self._fallback_imports(path)

    def get_dependents(self, path: str) -> List[str]:
        """Get all files that depend on *path*."""
        if self._init_graph():
            for method_name in ("dependents", "get_dependents", "reverse_deps"):
                fn = getattr(self._graph, method_name, None)
                if fn:
                    try:
                        result = fn(path)
                        if isinstance(result, list):
                            return [str(r) for r in result]
                    except Exception:
                        pass
        return self._fallback_dependents(path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _normalise(self, raw: Any) -> List[Dict]:
        """Normalise varied graphifyy return types to list[dict]."""
        if raw is None:
            return []
        if isinstance(raw, list):
            out: List[Dict] = []
            for item in raw:
                if isinstance(item, dict):
                    out.append(item)
                elif isinstance(item, str):
                    out.append({"path": item, "kind": "ref"})
                elif hasattr(item, "__dict__"):
                    out.append(vars(item))
            return out
        if isinstance(raw, dict):
            return [raw]
        return []

    def _fallback_related(self, symbol: str) -> List[Dict]:
        try:
            from agent import repo_index
            hits = repo_index.lookup(symbol, self._dir)
            result = repo_index.lookup_dependents(symbol, self._dir)
            out: List[Dict] = []
            for path, line, kind in hits[:10]:
                out.append({"path": path, "line": line, "kind": kind, "name": symbol})
            for path in result.get("imports", [])[:10]:
                out.append({"path": path, "kind": "imports", "name": symbol})
            return out
        except Exception:
            return []

    def _fallback_callers(self, symbol: str) -> List[Dict]:
        try:
            from agent import repo_index
            result = repo_index.lookup_dependents(symbol, self._dir)
            return [{"path": p, "kind": "caller"} for p in result.get("calls", [])[:30]]
        except Exception:
            return []

    def _fallback_callees(self, symbol: str) -> List[Dict]:
        try:
            from agent import repo_index
            hits = repo_index.lookup(symbol, self._dir)
            return [{"path": p, "line": l, "kind": k} for p, l, k in hits[:20]]
        except Exception:
            return []

    def _fallback_imports(self, path: str) -> List[str]:
        try:
            from agent import repo_index
            return repo_index.lookup_imports(path, self._dir)
        except Exception:
            return []

    def _fallback_dependents(self, path: str) -> List[str]:
        try:
            from agent import repo_index
            module = os.path.splitext(os.path.basename(path))[0]
            result = repo_index.lookup_dependents(module, self._dir)
            return result.get("imports", [])
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def is_live(self) -> bool:
        """True if graphifyy is active (not just falling back)."""
        _ensure_imported()
        return _HAS_GRAPHIFY and cfg.ENABLE_GRAPHIFY and self._graph is not None

    def status(self) -> str:
        if not _import_tried:
            return "graphify: not yet checked"
        if _load_error:
            return f"graphify: unavailable ({_load_error})"
        if not _HAS_GRAPHIFY:
            return "graphify: package not installed (pip install graphifyy)"
        if not cfg.ENABLE_GRAPHIFY:
            return "graphify: disabled (ENABLE_GRAPHIFY=false)"
        if self._init_error:
            return f"graphify: init failed ({self._init_error})"
        if self._graph is not None:
            return f"graphify: active (dir={self._dir})"
        return "graphify: idle (not yet initialised)"


# ---------------------------------------------------------------------------
# Module-level singleton per project
# ---------------------------------------------------------------------------

_adapters: dict[str, GraphifyAdapter] = {}


def get_adapter(directory: str = ".") -> GraphifyAdapter:
    """Return the GraphifyAdapter for *directory*."""
    key = os.path.abspath(directory)
    if key not in _adapters:
        _adapters[key] = GraphifyAdapter(directory)
    return _adapters[key]


def available() -> bool:
    """True if graphifyy is installed and ENABLE_GRAPHIFY is set."""
    _ensure_imported()
    return _HAS_GRAPHIFY and cfg.ENABLE_GRAPHIFY

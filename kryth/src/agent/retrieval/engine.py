"""Smart query router — automatically selects the cheapest retrieval strategy.

Query classification drives engine selection:

  keyword    → ripgrep  (fast, exact text match)
  symbol     → repo_index / lookup_symbol  (AST-based, Python-first)
  structural → ast-grep  (pattern-based, multi-language)
  docs       → SQLite FTS5  (indexed, ranked)
  relational → Graphify + repo_index  (dependency graph)
  semantic   → sentence-transformers retriever  (embedding-based)
  complex    → combine all engines, rank and deduplicate

The router always tries the cheapest engine first and escalates only
when needed, so a simple keyword query never touches the graph engine.
"""
from __future__ import annotations

import re
from typing import List, Optional


# ---------------------------------------------------------------------------
# Query classification
# ---------------------------------------------------------------------------


class QueryType:
    KEYWORD = "keyword"
    SYMBOL = "symbol"
    STRUCTURAL = "structural"
    DOCS = "docs"
    RELATIONAL = "relational"
    SEMANTIC = "semantic"
    COMPLEX = "complex"


_RELATIONAL_PATTERNS = [
    "what calls", "who calls", "what imports", "who imports",
    "what depends", "dependency", "dependent", "caller", "callee",
    "what uses", "where is it used", "reverse dep", "who uses",
    "imports of", "users of",
]

_STRUCTURAL_PATTERNS = [
    "find all function", "find all class", "all async", "all decorator",
    "all route", "api endpoint", "find all import", "react hook",
    "sql quer", "orm quer", "all definition", "all method", "list all",
]

_DOCS_PATTERNS = [
    "documentation", "readme", "docstring", "comment", "how to use",
    "usage", "example", "tutorial", "what does", "explain", "describe",
    "docs for",
]

_SEMANTIC_PATTERNS = [
    "how", "where", "show me", "find me", "search for", "locate",
    "similar to", "related to", "like the", "equivalent of",
    "pattern for", "implementation of", "handling", "manages",
]


def classify_query(query: str) -> str:
    """Classify a search query to determine the best engine."""
    q = query.lower().strip()
    words = q.split()

    if any(p in q for p in _RELATIONAL_PATTERNS):
        return QueryType.RELATIONAL

    if any(p in q for p in _STRUCTURAL_PATTERNS):
        return QueryType.STRUCTURAL

    if any(p in q for p in _DOCS_PATTERNS):
        return QueryType.DOCS

    # Pure identifier (no spaces, valid Python/JS name) → symbol lookup
    bare = query.strip()
    if re.match(r"^[A-Za-z_][A-Za-z0-9_.]*$", bare) and len(bare) > 2:
        return QueryType.SYMBOL

    if any(p in q for p in _SEMANTIC_PATTERNS) or len(words) > 4:
        return QueryType.SEMANTIC

    # 1-3 word queries → keyword
    if len(words) <= 3:
        return QueryType.KEYWORD

    return QueryType.COMPLEX


# ---------------------------------------------------------------------------
# Engine runners (each isolated so failures degrade gracefully)
# ---------------------------------------------------------------------------


def _run_ripgrep(query: str, path: str, max_results: int) -> str:
    try:
        from agent.tools._search import grep
        return grep(query, path=path, output_mode="content", max_results=max_results)
    except Exception as exc:
        return f"(ripgrep: {exc})"


def _run_symbol(query: str, path: str, _max: int) -> str:
    try:
        from agent.tools._search import lookup_symbol
        return lookup_symbol(query, directory=path)
    except Exception as exc:
        return f"(symbol: {exc})"


def _run_ast_search(query: str, path: str, max_results: int) -> str:
    try:
        from agent.retrieval.ast_search import search as _ast
        results = _ast(query, directory=path, max_results=max_results)
        if not results:
            return "(no structural matches)"
        lines = [
            f"{r['path']}:{r.get('line', 0)}  {r.get('text', '')}"
            for r in results
        ]
        return "\n".join(lines)
    except Exception as exc:
        return f"(ast-grep: {exc})"


def _run_fts(query: str, path: str, max_results: int) -> str:
    try:
        from agent.retrieval.fts_index import search as _fts
        results = _fts(query, directory=path, limit=max_results)
        if not results:
            return "(no FTS matches)"
        lines = [f"{r[0]}\t{r[1]:.2f}\t{r[2][:80]}" for r in results]
        return "\n".join(lines)
    except Exception as exc:
        return f"(fts: {exc})"


def _run_graphify(query: str, path: str, max_results: int) -> str:
    try:
        from agent.retrieval.graphify_adapter import get_adapter
        adapter = get_adapter(path)
        q = query.lower()
        if any(k in q for k in ("caller", "calls", "call")):
            symbol = query.split()[-1]
            results = adapter.get_callers(symbol)
        elif any(k in q for k in ("import", "depend")):
            symbol = query.split()[-1]
            results = adapter.get_imports(symbol)
        else:
            results = adapter.query_related(query)

        if not results:
            # Fall back to AST dependents
            from agent.tools._search import lookup_dependents
            return lookup_dependents(query.split()[-1], directory=path)

        lines = []
        for r in results[:max_results]:
            if isinstance(r, dict):
                parts = [r.get("path", ""), r.get("kind", ""), r.get("name", "")]
                lines.append("  ".join(p for p in parts if p))
            else:
                lines.append(str(r))
        return "\n".join(lines)
    except Exception:
        try:
            from agent.tools._search import lookup_dependents
            return lookup_dependents(query.split()[-1], directory=path)
        except Exception as exc:
            return f"(graphify: {exc})"


def _run_semantic(query: str, path: str, max_results: int) -> str:
    try:
        from agent.tools._search import semantic_search
        return semantic_search(query, top_k=max_results, directory=path)
    except Exception as exc:
        return f"(semantic: {exc})"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def search(
    query: str,
    path: str = ".",
    engines: Optional[List[str]] = None,
    max_results: int = 20,
) -> str:
    """Route a query to the best engine and return formatted results.

    Args:
        query:       Natural language or code search query.
        path:        Project root directory to search in.
        engines:     Explicit list of engines to use (overrides auto-routing).
                     Valid values: 'ripgrep', 'symbol', 'fts', 'ast',
                     'graphify', 'semantic'.
        max_results: Maximum results per engine.

    Returns:
        Formatted string of results, ready for LLM consumption.
    """
    if engines:
        return _run_named_engines(query, path, engines, max_results)

    qtype = classify_query(query)

    if qtype == QueryType.KEYWORD:
        return _run_ripgrep(query, path, max_results)

    if qtype == QueryType.SYMBOL:
        result = _run_symbol(query, path, max_results)
        if "(no" not in result and "failed" not in result and result.strip():
            return result
        # Escalate to ripgrep
        return _run_ripgrep(query, path, max_results)

    if qtype == QueryType.STRUCTURAL:
        return _run_ast_search(query, path, max_results)

    if qtype == QueryType.DOCS:
        result = _run_fts(query, path, max_results)
        if "(no" not in result and "failed" not in result and result.strip():
            return result
        return _run_ripgrep(query, path, max_results)

    if qtype == QueryType.RELATIONAL:
        return _run_graphify(query, path, max_results)

    if qtype == QueryType.SEMANTIC:
        result = _run_semantic(query, path, max_results)
        if "(no" not in result and "failed" not in result and result.strip():
            return result
        # Escalate to FTS
        result2 = _run_fts(query, path, max_results)
        return result2 if "(no" not in result2 else result

    # Complex: combine engines
    return _run_combined(query, path, max_results)


def _run_named_engines(
    query: str, path: str, engines: List[str], max_results: int
) -> str:
    funcs = {
        "ripgrep": _run_ripgrep,
        "symbol":  _run_symbol,
        "fts":     _run_fts,
        "ast":     _run_ast_search,
        "graphify": _run_graphify,
        "semantic": _run_semantic,
    }
    results = []
    for name in engines:
        fn = funcs.get(name)
        if not fn:
            continue
        out = fn(query, path, max_results)
        if out and "(no" not in out and "failed" not in out:
            results.append(f"[{name}]\n{out}")
    return "\n\n".join(results) if results else "(no matches)"


def _run_combined(query: str, path: str, max_results: int) -> str:
    """Run multiple engines and combine results."""
    per = max(5, max_results // 3)
    sections: list[str] = []

    for name, fn in [
        ("FTS",      _run_fts),
        ("Semantic", _run_semantic),
        ("Ripgrep",  _run_ripgrep),
    ]:
        try:
            out = fn(query, path, per)
            if out and "(no" not in out and "failed" not in out:
                sections.append(f"[{name}]\n{out}")
        except Exception:
            pass

    if not sections:
        return "(no matches across all engines)"
    return "\n\n".join(sections)

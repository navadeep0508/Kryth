"""Search-the-codebase tools: search_repo (unified), glob, and legacy engines.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
from pathlib import Path

from agent.context import IGNORE_DIRS
from agent.tools._common import trim_head_tail
from agent.tools._results import err


def _clamp_to_cwd(path_str: str) -> str:
    """Resolve path. Absolute paths are used as-is; relative paths are
    clamped to cwd to prevent ../.. escapes."""
    p = Path(path_str).resolve()
    if Path(path_str).is_absolute():
        return str(p)
    _cwd = Path(os.getcwd()).resolve()
    try:
        p.relative_to(_cwd)
        return str(p)
    except ValueError:
        return str(_cwd)


def search_code(keyword, directory="."):
    directory = _clamp_to_cwd(directory)
    matches = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for file in files:
            if not file.endswith((".py", ".js", ".ts", ".jsx", ".tsx")):
                continue
            path = os.path.join(root, file)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue
            if keyword.lower() in content.lower():
                matches.append(path)
    return "\n".join(matches)


def search_repo(
    query: str,
    path: str = ".",
    mode: str = "auto",
    max_results: int = 50,
) -> str:
    """
    Unified repository search. Auto-selects the best engine based on query type.

    Args:
        query: Search query (keyword, symbol name, regex pattern, or natural language)
        path: Directory to search (default: current directory)
        mode: "auto" | "keyword" | "symbol" | "regex" | "semantic" | "structural"
        max_results: Maximum results to return (default: 50)

    Modes:
        auto       - Classify query and pick best engine (default)
        keyword    - Fast text search via ripgrep/grep (keyword present in file)
        symbol     - AST-based symbol lookup (function/class names in Python)
        regex      - Full regex search via ripgrep with line numbers
        semantic   - Embedding-based semantic similarity (requires retriever)
        structural - AST-grep structural patterns (function, class, import, etc.)

    Returns:
        Formatted results with file paths and context.
    """
    if not isinstance(query, str) or not query.strip():
        return err("BAD_ARGS", "query must be a non-empty string")

    try:
        max_results = max(1, min(int(max_results), 200))
    except (TypeError, ValueError):
        max_results = 50

    valid_modes = ("auto", "keyword", "symbol", "regex", "semantic", "structural")
    if mode not in valid_modes:
        return err("BAD_ARGS", f"mode must be one of: {', '.join(valid_modes)}")

    query = query.strip()
    path = _clamp_to_cwd(path)

    # Auto-classify query
    if mode == "auto":
        # Symbol-like: CamelCase, snake_case, no spaces, short
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]{1,50}$", query) and " " not in query:
            mode = "symbol"
        # Regex-like: contains regex metacharacters
        elif re.search(r"[\[\]\(\)\.\*\+\?\^\$\{\}\|\\]", query):
            mode = "regex"
        # Semantic-like: natural language with spaces, >2 words
        elif len(query.split()) >= 3:
            mode = "semantic"
        # Default to keyword
        else:
            mode = "keyword"

    # Execute based on mode
    if mode == "keyword":
        return _search_repo_keyword(query, path, max_results)

    if mode == "symbol":
        return _search_repo_symbol(query, path, max_results)

    if mode == "regex":
        return _search_repo_regex(query, path, max_results)

    if mode == "semantic":
        return _search_repo_semantic(query, path, max_results)

    if mode == "structural":
        return _search_repo_structural(query, path, max_results)

    return err("BAD_ARGS", f"unknown mode: {mode}")


def _search_repo_keyword(query: str, path: str, max_results: int) -> str:
    """Fast keyword search via ripgrep or grep fallback."""
    rg = shutil.which("rg")
    if rg:
        cmd = [rg, "--no-heading", "-l", "--max-count", str(max_results), "-e", query, path]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15
            )
            if result.returncode in (0, 1):
                out = (result.stdout or "").rstrip()
                return out if out else "(no matches)"
        except Exception:
            pass

    # Fallback to Python grep
    return _grep_python_fallback(query, path, None, "files_with_matches", True, max_results)


def _search_repo_symbol(name: str, path: str, max_results: int) -> str:
    """AST-based symbol lookup (Python) with grep fallback for other languages."""
    from agent import repo_index
    hits = repo_index.lookup(name, path)
    if hits:
        lines = [f"{p}:{ln}  {kind}  {name}" for p, ln, kind in hits[:max_results]]
        if len(hits) > max_results:
            lines.append(f"...({len(hits) - max_results} more)")
        return "\n".join(lines)

    # Fallback: grep for symbol in all languages
    pattern = rf"\b{re.escape(name)}\b"
    return grep(pattern, path=path, output_mode="files_with_matches", max_results=max_results)


def _search_repo_regex(pattern: str, path: str, max_results: int) -> str:
    """Full regex search with line numbers."""
    return grep(pattern, path=path, output_mode="content", max_results=max_results)


def _search_repo_semantic(query: str, path: str, max_results: int) -> str:
    """Embedding-based semantic search."""
    from agent import retriever
    if not retriever.available():
        return err(
            "UNSUPPORTED",
            "semantic retriever unavailable",
            f"{retriever.status()}. Use mode='keyword' or 'regex' instead.",
        )

    results = retriever.retrieve_files(query, top_k=max_results, directory=path)
    if not results:
        return "(no semantic matches)"

    out = "\n".join(f"{p}\t{score:.3f}" for p, score in results)

    if retriever.truncated():
        out += (
            f"\n[note] semantic index capped at {retriever._MAX_FILES} files; "
            f"try mode='keyword' for full coverage."
        )
    return out


def _search_repo_structural(pattern: str, path: str, max_results: int) -> str:
    """AST-grep structural search."""
    try:
        from agent.retrieval.ast_search import search as _ast
        results = _ast(pattern, directory=path)
    except Exception as exc:
        return err("EXEC_FAILED", "AST search failed", str(exc))

    if not results:
        return f"(no structural matches for '{pattern}')"

    lines = [
        f"{r['path']}:{r.get('line', 0)}  {r.get('kind', '')}  {r.get('text', '')[:80]}"
        for r in results[:max_results]
    ]
    if len(results) > max_results:
        lines.append(f"...({len(results) - max_results} more)")
    return "\n".join(lines)


def _grep_python_fallback(
    pattern, path, glob_filter, output_mode, case_insensitive, max_results
):
    path = _clamp_to_cwd(path)
    try:
        regex = re.compile(pattern, re.IGNORECASE if case_insensitive else 0)
    except re.error as e:
        return err("BAD_ARGS", f"invalid regex: {e}")

    matches_by_file: dict = {}

    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for name in files:
            if glob_filter and not fnmatch.fnmatch(name, glob_filter):
                continue
            fp = os.path.join(root, name)
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except Exception:
                continue
            for i, line in enumerate(lines, start=1):
                if regex.search(line):
                    matches_by_file.setdefault(fp, []).append((i, line.rstrip("\n")))
                    if sum(len(v) for v in matches_by_file.values()) >= max_results:
                        break
            if sum(len(v) for v in matches_by_file.values()) >= max_results:
                break

    if not matches_by_file:
        return "(no matches)"

    if output_mode == "files_with_matches":
        return "\n".join(matches_by_file.keys())

    if output_mode == "count":
        return "\n".join(f"{fp}:{len(hits)}" for fp, hits in matches_by_file.items())

    lines = []
    for fp, hits in matches_by_file.items():
        for ln, text in hits:
            lines.append(f"{fp}:{ln}:{text}")
    return "\n".join(lines)


def grep(
    pattern,
    path=".",
    glob=None,
    output_mode="files_with_matches",
    case_insensitive=False,
    max_results=200,
):
    path = _clamp_to_cwd(path)
    if output_mode not in ("files_with_matches", "content", "count"):
        return err(
            "BAD_ARGS",
            "output_mode must be one of files_with_matches|content|count",
        )

    rg = shutil.which("rg")
    if rg:
        cmd = [rg, "--no-heading"]
        if case_insensitive:
            cmd.append("-i")
        if output_mode == "files_with_matches":
            cmd.append("-l")
        elif output_mode == "count":
            cmd.append("-c")
        else:
            cmd.append("-n")
        if glob:
            cmd += ["-g", glob]
        cmd += ["--max-count", str(max_results), "-e", pattern, path]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )
        except subprocess.TimeoutExpired:
            return err("TIMEOUT", "ripgrep exceeded 20s timeout")
        except Exception as e:
            return err("EXEC_FAILED", "ripgrep invocation failed", str(e))

        if result.returncode in (0, 1):
            out = (result.stdout or "").rstrip()
            return trim_head_tail(out) if out else "(no matches)"
        return err(
            "EXEC_FAILED",
            "ripgrep returned a non-zero error",
            (result.stderr or "").strip(),
        )

    return trim_head_tail(_grep_python_fallback(
        pattern, path, glob, output_mode, case_insensitive, max_results
    ))


def glob_files(pattern, path=".", max_results=500):
    import os as _os
    _cwd = Path(_os.getcwd()).resolve()
    root = Path(path).resolve()
    # Never search outside the current working directory
    try:
        root.relative_to(_cwd)
    except ValueError:
        root = _cwd
    if not root.exists():
        return err("NOT_FOUND", f"path does not exist: {root}")

    try:
        matches = []
        for p in root.glob(pattern):
            if any(part in IGNORE_DIRS for part in p.parts):
                continue
            if p.is_file():
                matches.append(p)
                if len(matches) >= max_results:
                    break
    except Exception as e:
        return err("EXEC_FAILED", f"glob({pattern!r}) failed", str(e))

    if not matches:
        return "(no matches)"

    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return "\n".join(str(p) for p in matches)


def semantic_search(query, top_k=5, directory="."):
    """Embedding-based file ranker.

    Returns "path\\tscore" lines, one per result. If the retriever is
    unavailable (e.g. sentence-transformers not installed, model
    download failed), returns a fallback hint pointing at grep.
    """
    if not isinstance(query, str) or not query.strip():
        return err("BAD_ARGS", "query must be a non-empty string")

    try:
        top_k = max(1, min(int(top_k), 25))
    except (TypeError, ValueError):
        top_k = 5

    from agent import retriever
    if not retriever.available():
        return err(
            "UNSUPPORTED",
            "semantic retriever unavailable",
            f"{retriever.status()}. Fall back to `grep` with keyword patterns.",
        )

    results = retriever.retrieve_files(query, top_k=top_k, directory=directory)
    if not results:
        return "(no semantic matches; project may be empty)"

    out = "\n".join(f"{path}\t{score:.3f}" for path, score in results)

    # Surface the silent-cap problem so the model can fall back to grep
    # for paths that may have been dropped from the index.
    if retriever.truncated():
        out += (
            f"\n[note] semantic index is capped at "
            f"{retriever._MAX_FILES} files; "
            f"{retriever.total_eligible() - retriever._MAX_FILES} additional "
            f"eligible source files were not indexed. Use `grep` with explicit "
            f"patterns to search the rest of the repo."
        )
    return out


def lookup_symbol(name, directory="."):
    """AST-based Python symbol lookup. Returns matches as
    ``path:line  kind  name`` lines. Faster, cleaner, and more accurate
    than grep for "where is FOO defined" in Python projects.

    The index is built lazily on first call and cached for the session.
    """
    if not isinstance(name, str) or not name.strip():
        return err("BAD_ARGS", "name must be a non-empty string")

    from agent import repo_index
    hits = repo_index.lookup(name.strip(), directory)
    if not hits:
        return (
            f"(no Python symbol named '{name}' found; try grep for "
            f"non-Python languages or partial matches)"
        )
    lines = [f"{path}:{line}  {kind}  {name}" for path, line, kind in hits[:20]]
    if len(hits) > 20:
        lines.append(f"...({len(hits) - 20} more matches)")
    return "\n".join(lines)


def lookup_imports(path, directory="."):
    """Return the list of modules that ``path`` imports.

    Path may be absolute or repo-relative. Output is one module per
    line, sorted. Empty result = file doesn't import anything (or isn't
    indexed).
    """
    if not isinstance(path, str) or not path.strip():
        return err("BAD_ARGS", "path must be a non-empty string")

    from agent import repo_index
    imports = repo_index.lookup_imports(path.strip(), directory)
    if not imports:
        return f"(no imports recorded for {path})"
    return "\n".join(imports)


def lookup_dependents(name, directory="."):
    """Find files that depend on a symbol (import its module or call its name).

    Useful for "what breaks if I rename X" — surfaces both the import
    side (definers' modules pulled in) and the use site (call expressions
    matching ``name`` or ``mod.name``). The two lists may overlap.
    """
    if not isinstance(name, str) or not name.strip():
        return err("BAD_ARGS", "name must be a non-empty string")

    from agent import repo_index
    result = repo_index.lookup_dependents(name.strip(), directory)
    imps = result.get("imports", [])
    calls = result.get("calls", [])
    if not imps and not calls:
        return f"(no files appear to depend on '{name}')"

    out: list[str] = []
    if imps:
        out.append(f"import-edges ({len(imps)}):")
        out.extend(f"  {p}" for p in imps[:30])
        if len(imps) > 30:
            out.append(f"  ...({len(imps) - 30} more)")
    if calls:
        out.append(f"call-edges ({len(calls)}):")
        out.extend(f"  {p}" for p in calls[:30])
        if len(calls) > 30:
            out.append(f"  ...({len(calls) - 30} more)")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# New retrieval-engine tools
# ---------------------------------------------------------------------------


def fts_search(query, path=".", limit=20):
    """Full-text search via SQLite FTS5 index.

    Faster than grep for repeated searches on the same repo because
    results come from a pre-built on-disk index with BM25 ranking.
    The index is built lazily on first call and updated incrementally.

    Returns ranked ``path\\tscore\\tsnippet`` lines.
    """
    if not isinstance(query, str) or not query.strip():
        return err("BAD_ARGS", "query must be a non-empty string")

    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 20

    try:
        from agent.retrieval.fts_index import search as _fts
        results = _fts(query.strip(), directory=path, limit=limit)
    except Exception as exc:
        return err("EXEC_FAILED", "FTS search failed", str(exc))

    if not results:
        return "(no FTS matches — index may still be building; try grep as fallback)"

    lines = []
    for file_path, score, snippet in results:
        snippet_clean = snippet.replace("\n", " ")[:120]
        lines.append(f"{file_path}\t{score:.2f}\t{snippet_clean}")
    return "\n".join(lines)


def ast_search(pattern, language=None, path="."):
    """Structural code search using AST patterns.

    Finds code by *structure* rather than text, so it works regardless
    of formatting. Supports named patterns and raw ast-grep patterns.

    Named patterns:
      python:     function, async_function, class, decorator, import,
                  from_import, lambda, with_statement, try_except
      javascript: function, arrow_function, class, async_function,
                  import, export, react_component, react_hook, api_route

    Falls back to repo_index AST index for Python when ast-grep is absent.
    """
    if not isinstance(pattern, str) or not pattern.strip():
        return err("BAD_ARGS", "pattern must be a non-empty string")

    try:
        from agent.retrieval.ast_search import search as _ast
        results = _ast(pattern.strip(), language=language, directory=path)
    except Exception as exc:
        return err("EXEC_FAILED", "AST search failed", str(exc))

    if not results:
        return (
            f"(no structural matches for '{pattern}'; "
            f"try grep for text-based search)"
        )

    lines = [
        f"{r['path']}:{r.get('line', 0)}  {r.get('kind', '')}  {r.get('text', '')[:80]}"
        for r in results[:50]
    ]
    if len(results) > 50:
        lines.append(f"...({len(results) - 50} more matches)")
    return "\n".join(lines)


def graphify_query(query, query_type="semantic", path="."):
    """Query the code knowledge graph via Graphify.

    Uses the graphifyy knowledge graph when available, falling back to
    the AST-based repo_index for relational queries.

    query_type options:
      semantic   — find semantically related symbols (default)
      callers    — find all call-sites of a symbol
      callees    — find all functions called inside a symbol
      imports    — find modules a file imports
      dependents — find files that depend on a symbol/module
    """
    if not isinstance(query, str) or not query.strip():
        return err("BAD_ARGS", "query must be a non-empty string")

    valid_types = ("semantic", "callers", "callees", "imports", "dependents")
    if query_type not in valid_types:
        return err(
            "BAD_ARGS",
            f"query_type must be one of: {', '.join(valid_types)}",
        )

    try:
        from agent.retrieval.graphify_adapter import get_adapter
        adapter = get_adapter(path)
        q = query.strip()

        if query_type == "callers":
            results = adapter.get_callers(q)
        elif query_type == "callees":
            results = adapter.get_callees(q)
        elif query_type == "imports":
            results = adapter.get_imports(q)
        elif query_type == "dependents":
            results = adapter.get_dependents(q)
        else:
            results = adapter.query_related(q)
    except Exception as exc:
        return err("EXEC_FAILED", "Graphify query failed", str(exc))

    if not results:
        # Fall back to AST-based lookup
        if query_type in ("callers", "dependents"):
            return lookup_dependents(query.strip(), directory=path)
        if query_type == "imports":
            return lookup_imports(query.strip(), directory=path)
        return f"(no graph results for '{query}'; try lookup_symbol or lookup_dependents)"

    lines = []
    for r in results[:40]:
        if isinstance(r, dict):
            parts = [
                str(r.get("path", "")),
                str(r.get("kind", "")),
                str(r.get("name", "")),
            ]
            lines.append("  ".join(p for p in parts if p))
        else:
            lines.append(str(r))
    return "\n".join(lines)


def search_smart(query, path=".", engines=None):
    """Intelligent multi-engine search that automatically picks the best strategy.

    Classifies the query and routes to the cheapest engine first:
      keyword    → ripgrep
      symbol     → AST index (lookup_symbol)
      structural → ast-grep
      docs       → SQLite FTS5
      relational → Graphify + repo_index
      semantic   → sentence-transformers
      complex    → combine all engines

    Pass ``engines`` as a comma-separated list to force specific engines:
      e.g. ``engines="ripgrep,fts"``
    """
    if not isinstance(query, str) or not query.strip():
        return err("BAD_ARGS", "query must be a non-empty string")

    engine_list = None
    if isinstance(engines, str) and engines.strip():
        engine_list = [e.strip() for e in engines.split(",") if e.strip()]

    try:
        from agent.retrieval.engine import search as _smart
        result = _smart(query.strip(), path=path, engines=engine_list, max_results=20)
    except Exception as exc:
        return err("EXEC_FAILED", "smart search failed", str(exc))

    return result or "(no matches found)"

"""Search-the-codebase tools: search_code, grep (ripgrep + fallback),
glob, and semantic_search.
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


def search_code(keyword, directory="."):
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


def _grep_python_fallback(
    pattern, path, glob_filter, output_mode, case_insensitive, max_results
):
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
    root = Path(path)
    if not root.exists():
        return err("NOT_FOUND", f"path does not exist: {path}")

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

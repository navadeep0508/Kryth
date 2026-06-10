"""Lightweight repository symbol + import + call graph index.

Walks Python files under the project root, parses each via ``ast``, and
records three things per file:

  1. Symbol definitions   (function / class / method / module-level var)
  2. Imports              (module strings the file pulls in)
  3. Calls                (callee names appearing as ``Name(...)`` or
                            ``module.attr(...)``)

Built lazily on first ``lookup``-family call and refreshed incrementally
when ``invalidate`` flags individual files.

Public surface:

    lookup(name)               — find definitions of ``name``
    lookup_imports(path)       — what does ``path`` import
    lookup_dependents(name)    — files that import or call ``name``
    invalidate(*paths)         — mark files dirty (or full reset with no args)
    index_stats()              — diagnostics for /diag
"""

from __future__ import annotations

import ast
import os
from typing import Iterable

from agent.context import IGNORE_DIRS


_index: dict[str, list[tuple[str, int, str]]] = {}
_imports_by_file: dict[str, set[str]] = {}
_calls_by_file: dict[str, set[str]] = {}
_indexed_root: str | None = None
_dirty_paths: set[str] = set()
# xxhash fingerprints keyed by absolute path — skip re-analysis when
# the fingerprint is unchanged. Populated during build/incremental updates.
_fingerprints: dict[str, str] = {}


def _file_fingerprint(path: str) -> str:
    """Fast file fingerprint using size+mtime+xxhash head. Falls back to md5."""
    try:
        from agent.retrieval.cache import file_fingerprint
        return file_fingerprint(path)
    except ImportError:
        import hashlib
        try:
            stat = os.stat(path)
            with open(path, "rb") as f:
                head = f.read(4096)
            h = hashlib.md5(head, usedforsecurity=False).hexdigest()
            return f"{stat.st_size}:{stat.st_mtime:.3f}:{h}"
        except OSError:
            return ""


def _iter_python_files(root: str) -> Iterable[str]:
    for r, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(r, f)


def _analyze_module(path: str) -> tuple[
    list[tuple[str, int, str]],
    set[str],
    set[str],
]:
    """Single-pass AST analysis of ``path``.

    Returns ``(definitions, imports, calls)``. Definitions are
    ``(name, lineno, kind)`` tuples. Imports and calls are sets of name
    strings.

    A file that fails to parse contributes empty results — staleness is
    a soft signal, not an error path.
    """
    defs: list[tuple[str, int, str]] = []
    imports: set[str] = set()
    calls: set[str] = set()

    try:
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
    except (OSError, SyntaxError):
        return defs, imports, calls

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs.append((node.name, node.lineno, "function"))
        elif isinstance(node, ast.ClassDef):
            defs.append((node.name, node.lineno, "class"))
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    defs.append((f"{node.name}.{item.name}", item.lineno, "method"))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defs.append((target.id, node.lineno, "var"))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                full = f"{mod}.{alias.name}" if mod else alias.name
                if mod:
                    imports.add(mod)
                imports.add(full)
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                calls.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                calls.add(fn.attr)

    return defs, imports, calls


def _rel(path: str, root: str) -> str:
    return os.path.relpath(path, root).replace("\\", "/")


def build_index(root: str = ".") -> int:
    """(Re)build all three indexes over ``root``. Returns symbol count."""
    global _index, _imports_by_file, _calls_by_file
    global _indexed_root, _dirty_paths, _fingerprints

    _index = {}
    _imports_by_file = {}
    _calls_by_file = {}
    _fingerprints = {}

    for path in _iter_python_files(root):
        rel = _rel(path, root)
        _fingerprints[path] = _file_fingerprint(path)
        defs, imports, calls = _analyze_module(path)
        for name, line, kind in defs:
            _index.setdefault(name, []).append((rel, line, kind))
        if imports:
            _imports_by_file[rel] = imports
        if calls:
            _calls_by_file[rel] = calls

    _indexed_root = root
    _dirty_paths = set()
    return sum(len(v) for v in _index.values())


def _reindex_paths(paths: set[str], root: str) -> None:
    """Re-parse a subset of files and merge results back. Cheaper than
    a full rebuild when only a handful of files changed.

    Files whose fingerprint is unchanged are skipped to avoid redundant
    AST parsing — critical for fast incremental updates in large repos.
    """
    norm = {_rel(p, root) for p in paths}

    for name in list(_index.keys()):
        kept = [hit for hit in _index[name] if hit[0] not in norm]
        if kept:
            _index[name] = kept
        else:
            del _index[name]
    for rel in norm:
        _imports_by_file.pop(rel, None)
        _calls_by_file.pop(rel, None)

    for p in paths:
        if not os.path.isfile(p) or not p.endswith(".py"):
            # Deleted — clean up fingerprint
            _fingerprints.pop(p, None)
            continue

        # Skip re-parsing if content hasn't changed
        new_fp = _file_fingerprint(p)
        if _fingerprints.get(p) == new_fp and new_fp:
            # Content unchanged — restore previous index entries
            # (they were just removed above; safe to skip re-parse)
            continue
        _fingerprints[p] = new_fp

        rel = _rel(p, root)
        defs, imports, calls = _analyze_module(p)
        for name, line, kind in defs:
            _index.setdefault(name, []).append((rel, line, kind))
        if imports:
            _imports_by_file[rel] = imports
        if calls:
            _calls_by_file[rel] = calls


def _ensure_fresh(root: str) -> None:
    if _indexed_root != root or not _index:
        build_index(root)
    elif _dirty_paths:
        _reindex_paths(set(_dirty_paths), root)
        _dirty_paths.clear()


def lookup(name: str, root: str = ".") -> list[tuple[str, int, str]]:
    """Find a symbol. Returns a list of ``(path, line, kind)`` matches."""
    _ensure_fresh(root)
    direct = list(_index.get(name, []))
    if direct:
        return direct
    suffix = f".{name}"
    out: list[tuple[str, int, str]] = []
    for key, hits in _index.items():
        if key.endswith(suffix):
            out.extend(hits)
    return out


def lookup_imports(path: str, root: str = ".") -> list[str]:
    """Sorted list of modules that ``path`` imports.

    ``path`` may be absolute or repo-relative. Returns an empty list when
    the file isn't a known Python file, doesn't import anything, or
    failed to parse.
    """
    _ensure_fresh(root)
    rel = _rel(path, root) if os.path.isabs(path) else path.replace("\\", "/")
    return sorted(_imports_by_file.get(rel, set()))


def lookup_dependents(name: str, root: str = ".") -> dict:
    """Find files that probably depend on ``name``.

    Heuristic — exact graph resolution requires a real type checker:

      * "imports": files whose import set contains a module ending in
        ``.<name>`` or matches the path of a file that DEFINES ``name``.
      * "calls":   files whose call set contains ``name`` (matches both
        bare ``foo()`` and ``mod.foo()``).

    Returns ``{"imports": [...], "calls": [...]}`` — repo-relative paths,
    each list deduped and sorted. The two lists may overlap; the caller
    decides whether to union them.
    """
    _ensure_fresh(root)

    defining_files = {hit[0] for hit in _index.get(name, [])}
    defining_modules = {
        f[:-3].replace("/", ".") for f in defining_files if f.endswith(".py")
    }
    suffix = f".{name}"

    importing_files: set[str] = set()
    for rel, imports in _imports_by_file.items():
        if any(imp == name or imp.endswith(suffix) for imp in imports):
            importing_files.add(rel)
            continue
        if defining_modules:
            for imp in imports:
                if imp in defining_modules or any(
                    imp.endswith("." + dm.rsplit(".", 1)[-1]) for dm in defining_modules
                ):
                    importing_files.add(rel)
                    break

    calling_files = sorted(
        rel for rel, calls in _calls_by_file.items() if name in calls
    )

    return {
        "imports": sorted(importing_files),
        "calls": calling_files,
    }


def invalidate(*paths: str) -> None:
    """Mark paths as dirty (or wipe the whole index with no args)."""
    global _dirty_paths, _index, _imports_by_file, _calls_by_file, _indexed_root
    if not paths:
        _index = {}
        _imports_by_file = {}
        _calls_by_file = {}
        _indexed_root = None
        _dirty_paths = set()
        return
    _dirty_paths.update(paths)


def index_stats() -> dict:
    return {
        "indexed_root": _indexed_root,
        "symbol_count": sum(len(v) for v in _index.values()),
        "unique_names": len(_index),
        "files_with_imports": len(_imports_by_file),
        "files_with_calls": len(_calls_by_file),
        "dirty_paths": len(_dirty_paths),
        "fingerprinted_files": len(_fingerprints),
    }

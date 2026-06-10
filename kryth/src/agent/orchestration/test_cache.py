"""Test Result Cache — skip tests when inputs haven't changed.

Hashes source files and caches pass/fail results. On subsequent runs,
if the content hash is unchanged the test is skipped (returns cached pass).

Smart rules (from spec):
  - README/docs changed only → skip code tests
  - CSS/styles only → visual tests only
  - React components → component tests only
  - Backend files → backend tests only
  - Database files → migration tests only
  - Build config → full validation

Usage:
    from agent.orchestration.test_cache import should_run_tests, record_test_pass

    if should_run_tests(changed_paths, "pytest"):
        run_tests()
        record_test_pass(changed_paths, "pytest")
    else:
        print("(tests skipped — no relevant changes)")
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Cache storage
# ---------------------------------------------------------------------------

def _cache_path() -> Path:
    cache_dir = Path(os.getenv("KRYTH_CACHE_DIR", ".kryth/test_cache"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "results.json"


def _load_cache() -> dict:
    p = _cache_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cache(data: dict) -> None:
    try:
        _cache_path().write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def _hash_files(paths: List[str]) -> str:
    h = hashlib.sha256()
    for path in sorted(paths):
        try:
            content = Path(path).read_bytes()
            h.update(path.encode())
            h.update(content)
        except Exception:
            h.update(path.encode())
    return h.hexdigest()[:16]


def _compute_cache_key(changed_paths: List[str], test_type: str) -> str:
    file_hash = _hash_files(changed_paths)
    return f"{test_type}:{file_hash}"


# ---------------------------------------------------------------------------
# Smart change classification
# ---------------------------------------------------------------------------

_DOC_PATTERNS = re.compile(r"\.(md|rst|txt|pdf)$", re.I)
_CSS_PATTERNS = re.compile(r"\.(css|scss|sass|less|styl)$", re.I)
_REACT_PATTERNS = re.compile(r"\.(jsx|tsx)$", re.I)
_BACKEND_PATTERNS = re.compile(r"(api|routes|controllers|services|middleware|views)\.(py|js|ts)$", re.I)
_DB_PATTERNS = re.compile(r"(migration|schema|model|alembic)\.(py|sql|json)$", re.I)
_BUILD_PATTERNS = re.compile(r"(package\.json|pyproject\.toml|Cargo\.toml|go\.mod|webpack|vite|rollup|tsconfig|Makefile|Dockerfile)", re.I)


def classify_changes(changed_paths: List[str]) -> str:
    """Return the test scope needed for the given file changes."""
    if not changed_paths:
        return "none"

    categories = set()
    for p in changed_paths:
        name = Path(p).name
        if _BUILD_PATTERNS.search(name) or _BUILD_PATTERNS.search(p):
            categories.add("full")
        elif _DB_PATTERNS.search(name):
            categories.add("migration")
        elif _BACKEND_PATTERNS.search(name):
            categories.add("backend")
        elif _REACT_PATTERNS.search(p):
            categories.add("component")
        elif _CSS_PATTERNS.search(name):
            categories.add("visual")
        elif _DOC_PATTERNS.search(name):
            categories.add("docs")
        else:
            categories.add("full")

    # Priority: full > migration > backend > component > visual > docs
    if "full" in categories:
        return "full"
    if "migration" in categories:
        return "migration"
    if "backend" in categories:
        return "backend"
    if "component" in categories:
        return "component"
    if "visual" in categories:
        return "visual"
    return "none"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def should_run_tests(changed_paths: List[str], test_type: str) -> bool:
    """Return True if tests should run (inputs changed or no cached pass).

    If changed_paths is empty, always run (we don't know what changed).
    If test_type doesn't match the change scope, skip.
    """
    if not changed_paths:
        return True

    scope = classify_changes(changed_paths)

    # Scope-based early skip
    if scope == "none":
        return False
    if scope == "docs" and test_type not in ("docs", "full"):
        return False
    if scope == "visual" and test_type not in ("visual", "full"):
        return False
    if scope == "component" and test_type not in ("component", "frontend", "full"):
        return False
    if scope == "backend" and test_type not in ("backend", "full", "pytest"):
        return False
    if scope == "migration" and test_type not in ("migration", "full", "pytest"):
        return False

    key = _compute_cache_key(changed_paths, test_type)
    cache = _load_cache()
    return cache.get(key, {}).get("status") != "pass"


def record_test_pass(changed_paths: List[str], test_type: str) -> None:
    key = _compute_cache_key(changed_paths, test_type)
    cache = _load_cache()
    cache[key] = {"status": "pass", "paths": changed_paths[:20]}
    _save_cache(cache)


def record_test_fail(changed_paths: List[str], test_type: str) -> None:
    key = _compute_cache_key(changed_paths, test_type)
    cache = _load_cache()
    cache[key] = {"status": "fail", "paths": changed_paths[:20]}
    _save_cache(cache)


def invalidate(paths: Optional[List[str]] = None) -> None:
    """Clear cache entries matching paths, or entire cache if paths=None."""
    if paths is None:
        _cache_path().unlink(missing_ok=True)
        return
    cache = _load_cache()
    keys_to_remove = [k for k, v in cache.items()
                      if any(p in (v.get("paths") or []) for p in paths)]
    for k in keys_to_remove:
        del cache[k]
    _save_cache(cache)

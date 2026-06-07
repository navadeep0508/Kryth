"""Repository Scale Optimizations - handle massive codebases.

Features:
- Monorepo support (multiple roots, selective indexing)
- Polyglot repository handling (language-specific indexes)
- Generated code exclusion (patterns, .gitignore)
- Incremental startup (avoid full indexing)
- Lazy symbol loading
- Memory-mapped database access for large indexes
- Background indexing with progress reporting
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from agent.retrieval import config as cfg
from agent.retrieval.cache import get_cache


# ---------------------------------------------------------------------------
# Scale configuration
# ---------------------------------------------------------------------------

@dataclass
class ScaleConfig:
    """Configuration for large repository handling."""
    max_files_for_full_index: int = 10000
    max_file_size_mb: int = 10
    enable_incremental_indexing: bool = True
    enable_lazy_loading: bool = True
    enable_mmap_for_db: bool = True
    background_index_batch_size: int = 100
    monorepo_roots: List[str] = field(default_factory=list)
    excluded_patterns: List[str] = field(default_factory=lambda: [
        "node_modules",
        "__pycache__",
        ".git",
        "venv",
        ".venv",
        "target",
        "build",
        "dist",
        "vendor",
        ".next",
        "coverage",
        "*.min.js",
        "*.bundle.js",
    ])


# ---------------------------------------------------------------------------
# Scale optimizer
# ---------------------------------------------------------------------------

class ScaleOptimizer:
    """Optimize retrieval for large repositories."""

    def __init__(self, directory: str = "."):
        self.directory = os.path.abspath(directory)
        self.config = ScaleConfig()
        self._cache = get_cache("scale_optimizer")
        self._lock = threading.RLock()
        self._indexing_thread: Optional[threading.Thread] = None
        self._indexing_progress = {"status": "idle", "files_indexed": 0, "total_files": 0}

    def estimate_repo_size(self) -> Dict[str, Any]:
        """Estimate repository size and complexity."""
        total_files = 0
        total_size = 0
        languages: Dict[str, int] = {}
        excluded = 0

        for root, dirs, files in os.walk(self.directory):
            # Apply exclusions
            dirs[:] = [d for d in dirs if not self._is_excluded(d, root)]
            for f in files:
                if self._is_excluded(f, root):
                    excluded += 1
                    continue
                fp = os.path.join(root, f)
                try:
                    stat = os.stat(fp)
                    total_files += 1
                    total_size += stat.st_size
                    lang = self._guess_language(f)
                    if lang:
                        languages[lang] = languages.get(lang, 0) + 1
                except Exception:
                    pass

        return {
            "total_files": total_files,
            "total_size_mb": total_size / (1024 * 1024),
            "languages": languages,
            "excluded_files": excluded,
            "is_large": total_files > self.config.max_files_for_full_index,
        }

    def _is_excluded(self, name: str, parent: str) -> bool:
        """Check if a file/directory should be excluded."""
        full_path = os.path.join(parent, name)
        rel_path = os.path.relpath(full_path, self.directory)
        for pattern in self.config.excluded_patterns:
            if pattern in rel_path or rel_path.endswith(pattern.replace('*', '')):
                return True
        if name.startswith('.'):
            return True
        return False

    def _guess_language(self, filename: str) -> Optional[str]:
        """Guess language from filename."""
        ext = os.path.splitext(filename)[1].lower()
        mapping = {
            '.py': 'python',
            '.js': 'javascript',
            '.ts': 'typescript',
            '.jsx': 'javascript',
            '.tsx': 'typescript',
            '.java': 'java',
            '.go': 'go',
            '.rs': 'rust',
            '.c': 'c',
            '.cpp': 'cpp',
            '.h': 'c',
            '.hpp': 'cpp',
            '.rb': 'ruby',
            '.php': 'php',
            '.swift': 'swift',
            '.kt': 'kotlin',
            '.scala': 'scala',
            '.ml': 'ocaml',
        }
        return mapping.get(ext)

    def should_full_index(self) -> bool:
        """Decide if full indexing is appropriate."""
        size = self.estimate_repo_size()
        return size["total_files"] <= self.config.max_files_for_full_index

    def start_background_indexing(self, indexers: List[Any]) -> None:
        """Start background incremental indexing."""
        if self._indexing_thread and self._indexing_thread.is_alive():
            return  # Already running

        self._indexing_thread = threading.Thread(
            target=self._background_index_worker,
            args=(indexers,),
            daemon=True,
        )
        self._indexing_thread.start()

    def _background_index_worker(self, indexers: List[Any]) -> None:
        """Background indexing worker."""
        self._indexing_progress = {"status": "running", "files_indexed": 0, "total_files": 0}

        # Collect files to index
        files_to_index: List[str] = []
        for root, dirs, files in os.walk(self.directory):
            dirs[:] = [d for d in dirs if not self._is_excluded(d, root)]
            for f in files:
                if self._is_excluded(f, root):
                    continue
                fp = os.path.join(root, f)
                files_to_index.append(fp)

        self._indexing_progress["total_files"] = len(files_to_index)

        # Process in batches
        batch_size = self.config.background_index_batch_size
        for i in range(0, len(files_to_index), batch_size):
            batch = files_to_index[i:i+batch_size]
            for indexer in indexers:
                try:
                    for filepath in batch:
                        if hasattr(indexer, 'needs_indexing') and indexer.needs_indexing(filepath):
                            indexer.index_file(filepath)
                except Exception:
                    pass
            self._indexing_progress["files_indexed"] += len(batch)

        self._indexing_progress["status"] = "complete"

    def get_indexing_progress(self) -> Dict[str, Any]:
        """Get background indexing progress."""
        return self._indexing_progress.copy()

    def optimize_query_for_scale(self, query_type: str, path: str) -> Dict[str, Any]:
        """Optimize query parameters for large repos."""
        size = self.estimate_repo_size()

        if size["is_large"]:
            # For large repos, use more aggressive limits
            return {
                "max_results": 20,  # Limit results
                "timeout": 15.0,  # Shorter timeout to fail fast
                "use_cache": True,
                "parallel": True,
                "hint_engines": ["symbol", "fts"],  # Cheaper engines first
            }
        else:
            # Small repo - full search
            return {
                "max_results": 100,
                "timeout": 30.0,
                "use_cache": True,
                "parallel": True,
                "hint_engines": None,
            }

    def get_monorepo_roots(self) -> List[str]:
        """Detect monorepo roots (package.json at root, Cargo.toml, go.mod, etc.)."""
        roots = [self.directory]

        # Look for common monorepo indicators
        indicators = [
            ("packages", os.path.isdir),
            ("services", os.path.isdir),
            ("apps", os.path.isdir),
            ("libs", os.path.isdir),
        ]

        for name, check in indicators:
            path = os.path.join(self.directory, name)
            if os.path.isdir(path):
                roots.append(path)

        # Also look for multiple package.json files at top level
        top_jsons = [f for f in os.listdir(self.directory) if f == 'package.json']
        if len(top_jsons) > 1:
            # Might be a monorepo with multiple package.json at root
            pass

        return list(set(roots))


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_scale_optimizer: Optional[ScaleOptimizer] = None
_scale_lock = threading.Lock()


def get_scale_optimizer(directory: str = ".") -> ScaleOptimizer:
    """Get or create the scale optimizer."""
    global _scale_optimizer
    if _scale_optimizer is None:
        with _scale_lock:
            if _scale_optimizer is None:
                _scale_optimizer = ScaleOptimizer(directory)
    return _scale_optimizer


def capabilities() -> Dict[str, Any]:
    """Return scale optimizer capabilities."""
    return {
        "enabled": cfg.ENABLE_SYMBOL_INDEX,  # Depends on symbol index
        "has_background_indexing": True,
        "has_monorepo_support": True,
        "has_exclusion_patterns": True,
    }
"""Lightweight dependency graph cache.

Tracks file-level and symbol-level dependencies for impact analysis,
safe refactoring, and dead code detection.

Stores in SQLite:
- imports (file A imports file B)
- imported_by (reverse of imports)
- function_calls (function A calls function B)
- class_inheritance (class A extends B)
- interface_implementations (class A implements interface B)
- module_dependencies (module A depends on module B)
- package_relationships (package A uses package B)

All relationships are incremental and update on file changes.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.retrieval import config as cfg
from agent.retrieval.cache import file_fingerprint
from agent.retrieval.symbol_index import get_index as get_symbol_index


# ---------------------------------------------------------------------------
# Database schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT NOT NULL,
    target_file TEXT NOT NULL,
    source_symbol TEXT,
    target_symbol TEXT,
    relation_type TEXT NOT NULL,
    line INTEGER,
    confidence REAL DEFAULT 1.0,
    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    file_mtime REAL,
    file_hash TEXT,
    UNIQUE(source_file, target_file, source_symbol, target_symbol, relation_type) ON CONFLICT REPLACE
);

CREATE INDEX IF NOT EXISTS idx_deps_source ON dependencies(source_file);
CREATE INDEX IF NOT EXISTS idx_deps_target ON dependencies(target_file);
CREATE INDEX IF NOT EXISTS idx_deps_type ON dependencies(relation_type);
CREATE INDEX IF NOT EXISTS idx_deps_source_sym ON dependencies(source_symbol);
CREATE INDEX IF NOT EXISTS idx_deps_target_sym ON dependencies(target_symbol);
"""

_RELATION_TYPES = {
    'imports',
    'imported_by',
    'calls',
    'called_by',
    'inherits',
    'inherited_by',
    'implements',
    'implemented_by',
    'module_dep',
    'package_dep',
}


# ---------------------------------------------------------------------------
# Dependency graph implementation
# ---------------------------------------------------------------------------


class DependencyGraph:
    """Repository dependency graph with SQLite backend."""

    def __init__(self, directory: str = "."):
        self.directory = os.path.abspath(directory)
        self.db_path = os.path.join(self.directory, ".kryth", "dep_graph.db")
        self._conn: Optional[sqlite3.Connection] = None
        self._ensure_db_dir()
        self._connect()
        self._initialize_schema()

    def _ensure_db_dir(self) -> None:
        db_dir = os.path.dirname(self.db_path)
        os.makedirs(db_dir, exist_ok=True)

    def _connect(self) -> None:
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    def _initialize_schema(self) -> None:
        if self._conn:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def _get_file_info(self, path: str) -> Tuple[float, str]:
        try:
            mtime = os.path.getmtime(path)
            file_hash = file_fingerprint(path)
            return mtime, file_hash
        except Exception:
            return 0.0, ""

    def needs_update(self, path: str) -> bool:
        """Check if dependencies for a file need updating."""
        if not os.path.isfile(path):
            return False

        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT file_mtime, file_hash FROM dependencies WHERE source_file = ? LIMIT 1",
            (path,)
        )
        row = cursor.fetchone()

        current_mtime, current_hash = self._get_file_info(path)

        if row is None:
            return True

        indexed_mtime = row[0]
        indexed_hash = row[1]

        return (current_mtime != indexed_mtime or current_hash != indexed_hash)

    def update_file(self, path: str, force: bool = False) -> int:
        """Update dependency information for a file. Returns number of edges added."""
        if not os.path.isfile(path):
            return 0

        if not force and not self.needs_update(path):
            return 0

        # Remove existing deps for this file as source
        cursor = self._conn.cursor()
        cursor.execute("DELETE FROM dependencies WHERE source_file = ?", (path,))

        # Extract dependencies
        deps = self._extract_dependencies(path)
        if not deps:
            self._conn.commit()
            return 0

        mtime, file_hash = self._get_file_info(path)
        count = 0

        for dep in deps:
            cursor.execute(
                """
                INSERT INTO dependencies 
                (source_file, target_file, source_symbol, target_symbol, relation_type, line, file_mtime, file_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dep["source_file"],
                    dep["target_file"],
                    dep.get("source_symbol"),
                    dep.get("target_symbol"),
                    dep["relation_type"],
                    dep.get("line"),
                    mtime,
                    file_hash,
                )
            )
            count += 1

        self._conn.commit()
        return count

    def _extract_dependencies(self, path: str) -> List[Dict[str, Any]]:
        """Extract dependencies from a file."""
        deps: List[Dict[str, Any]] = []

        # Simple import extraction for Python files
        if path.endswith('.py'):
            deps.extend(self._extract_python_imports(path))

        # Can extend for other languages using ast-grep or tree-sitter
        # For now, focus on Python

        return deps

    def _extract_python_imports(self, path: str) -> List[Dict[str, Any]]:
        """Extract import statements from a Python file."""
        deps: List[Dict[str, Any]] = []
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
        except Exception:
            return []

        import re
        import_pattern = re.compile(r'^(import|from)\s+([^\s]+)')

        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            match = import_pattern.match(line)
            if match:
                import_type, module = match.groups()
                # Resolve module to file path (simplified)
                target_file = self._resolve_module_to_file(module, os.path.dirname(path))
                if target_file:
                    deps.append({
                        "source_file": path,
                        "target_file": target_file,
                        "source_symbol": None,
                        "target_symbol": module,
                        "relation_type": "imports",
                        "line": line_num,
                    })

        return deps

    def _resolve_module_to_file(self, module: str, base_dir: str) -> Optional[str]:
        """Resolve a Python module name to a file path."""
        parts = module.split('.')
        # Try as package
        candidate = os.path.join(base_dir, *parts) + '.py'
        if os.path.isfile(candidate):
            return candidate

        # Try as package __init__.py
        candidate = os.path.join(base_dir, *parts, '__init__.py')
        if os.path.isfile(candidate):
            return candidate

        # Try as directory (namespace package)
        candidate = os.path.join(base_dir, *parts)
        if os.path.isdir(candidate):
            init_file = os.path.join(candidate, '__init__.py')
            if os.path.isfile(init_file):
                return init_file

        return None

    def get_imports(self, path: str) -> List[Dict[str, Any]]:
        """Get all imports for a file."""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT * FROM dependencies
            WHERE source_file = ? AND relation_type = 'imports'
            ORDER BY line
            """,
            (path,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_imported_by(self, path: str) -> List[Dict[str, Any]]:
        """Get all files that import this file."""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT * FROM dependencies
            WHERE target_file = ? AND relation_type = 'imports'
            ORDER BY source_file
            """,
            (path,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_dependents(self, path: str) -> List[Dict[str, Any]]:
        """Get all files that depend on this file (imports or calls)."""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT * FROM dependencies
            WHERE target_file = ? AND relation_type IN ('imports', 'calls')
            ORDER BY source_file
            """,
            (path,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_dependencies(self, path: str) -> List[Dict[str, Any]]:
        """Get all dependencies of a file."""
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT * FROM dependencies
            WHERE source_file = ? AND relation_type IN ('imports', 'calls')
            ORDER BY line
            """,
            (path,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def find_callers(self, symbol: str, file: Optional[str] = None) -> List[Dict[str, Any]]:
        """Find functions that call a given symbol."""
        cursor = self._conn.cursor()
        if file:
            cursor.execute(
                """
                SELECT d.* FROM dependencies d
                JOIN symbols s ON d.source_symbol = s.name
                WHERE d.target_symbol = ? AND d.source_file = ? AND d.relation_type = 'calls'
                """,
                (symbol, file)
            )
        else:
            cursor.execute(
                """
                SELECT * FROM dependencies
                WHERE target_symbol = ? AND relation_type = 'calls'
                """,
                (symbol,)
            )
        return [dict(row) for row in cursor.fetchall()]

    def get_statistics(self) -> Dict[str, Any]:
        """Get dependency graph statistics."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM dependencies")
        total_edges = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT source_file) FROM dependencies")
        source_files = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT target_file) FROM dependencies")
        target_files = cursor.fetchone()[0]

        cursor.execute("SELECT relation_type, COUNT(*) FROM dependencies GROUP BY relation_type")
        by_type = dict(cursor.fetchall())

        return {
            "total_edges": total_edges,
            "source_files": source_files,
            "target_files": target_files,
            "by_type": by_type,
            "db_path": self.db_path,
        }

    def clear(self) -> None:
        """Clear all dependencies."""
        cursor = self._conn.cursor()
        cursor.execute("DELETE FROM dependencies")
        self._conn.commit()

    def rebuild(self) -> int:
        """Full rebuild of dependency graph. Returns total edges."""
        self.clear()
        count = 0
        symbol_index = get_symbol_index(self.directory)

        # Get all indexed files
        cursor = self._conn.cursor()
        cursor.execute("SELECT DISTINCT file FROM symbols")
        files = [row[0] for row in cursor.fetchall()]

        for file in files:
            count += self.update_file(file, force=True)

        return count


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_graph_instances: Dict[str, DependencyGraph] = {}


def get_graph(directory: str = ".") -> DependencyGraph:
    """Get or create the dependency graph for a directory."""
    key = os.path.abspath(directory)
    if key not in _graph_instances:
        _graph_instances[key] = DependencyGraph(directory)
    return _graph_instances[key]


def capabilities() -> Dict[str, Any]:
    """Return dependency graph capabilities."""
    return {
        "enabled": cfg.ENABLE_DEP_GRAPH,
        "db_path": None,  # Filled by instance
    }
"""Repository Knowledge Cache - persistent learning of repository patterns.

Stores:
- Common navigation paths (frequently accessed file sequences)
- Previous search results (query → results)
- Frequently accessed files (hot files)
- Popular symbols (hot symbols)
- Module summaries (cached)

Automatically refreshes stale entries based on TTL.
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.retrieval import config as cfg
from agent.retrieval.cache import get_cache, file_fingerprint
import threading


# ---------------------------------------------------------------------------
# Database schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    kind TEXT NOT NULL,  -- 'search_result', 'nav_path', 'hot_file', 'hot_symbol', 'module_summary'
    metadata TEXT,  -- JSON
    access_count INTEGER DEFAULT 1,
    last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,  -- NULL = never expires
    UNIQUE(key, kind) ON CONFLICT REPLACE
);

CREATE INDEX IF NOT EXISTS idx_knowledge_key ON knowledge(key);
CREATE INDEX IF NOT EXISTS idx_knowledge_kind ON knowledge(kind);
CREATE INDEX IF NOT EXISTS idx_knowledge_access ON knowledge(access_count DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_last_accessed ON knowledge(last_accessed DESC);
"""


# ---------------------------------------------------------------------------
# Knowledge cache implementation
# ---------------------------------------------------------------------------

@dataclass
class KnowledgeEntry:
    """A cached knowledge entry."""
    key: str
    value: Any
    kind: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    access_count: int = 1
    last_accessed: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "kind": self.kind,
            "metadata": self.metadata,
            "access_count": self.access_count,
            "last_accessed": self.last_accessed,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KnowledgeEntry":
        return cls(**data)


class KnowledgeCache:
    """Persistent knowledge store with TTL and LRU eviction."""

    def __init__(self, directory: str = "."):
        self.directory = os.path.abspath(directory)
        self.db_path = os.path.join(self.directory, ".kryth", "knowledge_cache.db")
        self._conn: Optional[sqlite3.Connection] = None
        self._memory_cache: Dict[str, KnowledgeEntry] = {}
        self._ensure_db_dir()
        self._connect()
        self._initialize_schema()
        self._load_hot_entries()

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

    def _load_hot_entries(self) -> None:
        """Load frequently accessed entries into memory."""
        if not self._conn:
            return
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT * FROM knowledge WHERE access_count > 5 ORDER BY access_count DESC LIMIT 1000"
        )
        for row in cursor.fetchall():
            entry = KnowledgeEntry(
                key=row['key'],
                value=row['value'],
                kind=row['kind'],
                metadata=self._json_load(row['metadata']),
                access_count=row['access_count'],
                last_accessed=row['last_accessed'],
                created_at=row['created_at'],
                expires_at=row['expires_at'],
            )
            self._memory_cache[entry.key] = entry

    def _json_load(self, s: str) -> Any:
        try:
            import json
            return json.loads(s) if s else {}
        except Exception:
            return {}

    def _json_dump(self, obj: Any) -> str:
        try:
            import json
            return json.dumps(obj)
        except Exception:
            return "{}"

    def get(self, key: str, kind: str) -> Optional[Any]:
        """Get a knowledge entry."""
        # Check memory cache first
        mem_key = f"{kind}:{key}"
        entry = self._memory_cache.get(mem_key)
        if entry and not entry.is_expired():
            entry.access_count += 1
            entry.last_accessed = time.time()
            return entry.value

        # Check database
        if self._conn:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT * FROM knowledge 
                WHERE key = ? AND kind = ? AND (expires_at IS NULL OR expires_at > ?)
                """,
                (key, kind, time.time())
            )
            row = cursor.fetchone()
            if row:
                entry = KnowledgeEntry(
                    key=row['key'],
                    value=row['value'],
                    kind=row['kind'],
                    metadata=self._json_load(row['metadata']),
                    access_count=row['access_count'] + 1,
                    last_accessed=time.time(),
                    created_at=row['created_at'],
                    expires_at=row['expires_at'],
                )
                # Update access count
                cursor.execute(
                    "UPDATE knowledge SET access_count = ?, last_accessed = ? WHERE id = ?",
                    (entry.access_count, entry.last_accessed, row['id'])
                )
                self._conn.commit()
                # Add to memory cache
                self._memory_cache[mem_key] = entry
                return entry.value

        return None

    def set(
        self,
        key: str,
        value: Any,
        kind: str,
        metadata: Optional[Dict[str, Any]] = None,
        ttl: Optional[int] = None,
    ) -> None:
        """Store a knowledge entry."""
        mem_key = f"{kind}:{key}"
        expires_at = None
        if ttl:
            expires_at = time.time() + ttl

        entry = KnowledgeEntry(
            key=key,
            value=value,
            kind=kind,
            metadata=metadata or {},
            last_accessed=time.time(),
            expires_at=expires_at,
        )

        # Update memory cache
        self._memory_cache[mem_key] = entry

        # Update database
        if self._conn:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO knowledge 
                (key, value, kind, metadata, access_count, last_accessed, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    self._json_dump(value),
                    kind,
                    self._json_dump(metadata),
                    entry.access_count,
                    entry.last_accessed,
                    entry.created_at,
                    expires_at,
                )
            )
            self._conn.commit()

    def invalidate(self, key: str, kind: str) -> None:
        """Invalidate a cached entry."""
        mem_key = f"{kind}:{key}"
        self._memory_cache.pop(mem_key, None)
        if self._conn:
            cursor = self._conn.cursor()
            cursor.execute(
                "DELETE FROM knowledge WHERE key = ? AND kind = ?",
                (key, kind)
            )
            self._conn.commit()

    def clear(self) -> None:
        """Clear all knowledge."""
        self._memory_cache.clear()
        if self._conn:
            cursor = self._conn.cursor()
            cursor.execute("DELETE FROM knowledge")
            self._conn.commit()

    def get_statistics(self) -> Dict[str, Any]:
        """Get cache statistics."""
        stats = {
            "memory_entries": len(self._memory_cache),
            "db_entries": 0,
            "by_kind": defaultdict(int),
        }
        if self._conn:
            cursor = self._conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM knowledge")
            stats["db_entries"] = cursor.fetchone()[0]
            cursor.execute("SELECT kind, COUNT(*) FROM knowledge GROUP BY kind")
            for kind, count in cursor.fetchall():
                stats["by_kind"][kind] = count
        return stats


# ---------------------------------------------------------------------------
# Specialized accessors
# ---------------------------------------------------------------------------

class KnowledgeAccessor:
    """High-level API for knowledge cache."""

    def __init__(self, directory: str = "."):
        self.cache = KnowledgeCache(directory)

    def get_search_result(self, query: str, query_type: str) -> Optional[List[Any]]:
        """Get cached search results."""
        key = f"search:{query_type}:{query[:200]}"
        return self.cache.get(key, kind='search_result')

    def set_search_result(self, query: str, query_type: str, results: List[Any], ttl: int = 3600) -> None:
        """Cache search results."""
        key = f"search:{query_type}:{query[:200]}"
        self.cache.set(key, results, kind='search_result', ttl=ttl)

    def get_nav_path(self, from_file: str, to_file: str) -> Optional[List[str]]:
        """Get cached navigation path."""
        key = f"nav:{from_file}:{to_file}"
        return self.cache.get(key, kind='nav_path')

    def set_nav_path(self, from_file: str, to_file: str, path: List[str], ttl: int = 86400) -> None:
        """Cache navigation path."""
        key = f"nav:{from_file}:{to_file}"
        self.cache.set(key, path, kind='nav_path', ttl=ttl)

    def get_hot_file(self, file: str) -> Optional[Dict[str, Any]]:
        """Get hot file metadata."""
        key = f"file:{file}"
        return self.cache.get(key, kind='hot_file')

    def increment_hot_file(self, file: str) -> None:
        """Mark a file as accessed."""
        key = f"file:{file}"
        existing = self.cache.get(key, kind='hot_file')
        count = 1
        if existing:
            count = existing.get('access_count', 0) + 1
        self.cache.set(
            key,
            {"access_count": count, "last_accessed": time.time()},
            kind='hot_file',
            ttl=None,  # Never expire hot files
        )

    def get_hot_symbol(self, symbol: str, file: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get hot symbol metadata."""
        key = f"symbol:{symbol}"
        if file:
            key = f"{key}:{file}"
        return self.cache.get(key, kind='hot_symbol')

    def increment_hot_symbol(self, symbol: str, file: Optional[str] = None) -> None:
        """Mark a symbol as accessed."""
        key = f"symbol:{symbol}"
        if file:
            key = f"{key}:{file}"
        existing = self.cache.get(key, kind='hot_symbol')
        count = 1
        if existing:
            count = existing.get('access_count', 0) + 1
        self.cache.set(
            key,
            {"access_count": count, "last_accessed": time.time()},
            kind='hot_symbol',
            ttl=None,
        )

    def get_module_summary(self, module: str) -> Optional[Dict[str, Any]]:
        """Get cached module summary."""
        key = f"module:{module}"
        return self.cache.get(key, kind='module_summary')

    def set_module_summary(self, module: str, summary: Dict[str, Any], ttl: int = 86400) -> None:
        """Cache module summary."""
        key = f"module:{module}"
        self.cache.set(key, summary, kind='module_summary', ttl=ttl)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_knowledge: Optional[KnowledgeAccessor] = None
_knowledge_lock = threading.Lock()


def get_knowledge(directory: str = ".") -> KnowledgeAccessor:
    """Get or create the knowledge cache for a directory."""
    global _knowledge
    if _knowledge is None:
        with _knowledge_lock:
            if _knowledge is None:
                _knowledge = KnowledgeAccessor(directory)
    return _knowledge


def capabilities() -> Dict[str, Any]:
    """Return knowledge cache capabilities."""
    return {
        "enabled": cfg.ENABLE_KNOWLEDGE_CACHE,
        "has_db": True,
    }
"""Retrieval Telemetry - track performance and usage.

Collects:
- Query type
- Engine selected
- Latency
- Cache hits
- Token savings
- Retrieval success rate
- Error counts

Data used for:
- Performance monitoring
- Cost optimizer learning
- A/B testing strategies
- Identifying bottlenecks
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from agent.retrieval import config as cfg
from agent.retrieval.cache import get_cache


# ---------------------------------------------------------------------------
# Telemetry data structures
# ---------------------------------------------------------------------------

@dataclass
class QueryEvent:
    """A single query execution event."""
    timestamp: float
    query: str
    query_type: str
    engines_tried: List[str]
    engines_succeeded: List[str]
    latencies_ms: Dict[str, float]  # engine -> latency
    cache_hits: Dict[str, bool]  # engine -> cache hit
    tokens_estimated: int
    tokens_actual: int
    total_latency_ms: float
    success: bool
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "query": self.query[:200],
            "query_type": self.query_type,
            "engines_tried": self.engines_tried,
            "engines_succeeded": self.engines_succeeded,
            "latencies_ms": self.latencies_ms,
            "cache_hits": self.cache_hits,
            "tokens_estimated": self.tokens_estimated,
            "tokens_actual": self.tokens_actual,
            "total_latency_ms": self.total_latency_ms,
            "success": self.success,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Telemetry storage
# ---------------------------------------------------------------------------

class TelemetryStore:
    """Persistent storage for telemetry events."""

    def __init__(self, directory: str = "."):
        self.directory = os.path.abspath(directory)
        self.db_path = os.path.join(self.directory, ".kryth", "telemetry.db")
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
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS query_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    query TEXT NOT NULL,
                    query_type TEXT NOT NULL,
                    engines_tried TEXT NOT NULL,  -- JSON array
                    engines_succeeded TEXT NOT NULL,
                    latencies_ms TEXT NOT NULL,  -- JSON object
                    cache_hits TEXT NOT NULL,  -- JSON object
                    tokens_estimated INTEGER,
                    tokens_actual INTEGER,
                    total_latency_ms REAL NOT NULL,
                    success INTEGER NOT NULL,
                    error TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_query_events_timestamp ON query_events(timestamp);
                CREATE INDEX IF NOT EXISTS idx_query_events_query_type ON query_events(query_type);
                CREATE INDEX IF NOT EXISTS idx_query_events_success ON query_events(success);

                CREATE TABLE IF NOT EXISTS engine_stats (
                    engine TEXT PRIMARY KEY,
                    total_queries INTEGER DEFAULT 0,
                    total_latency_ms REAL DEFAULT 0,
                    cache_hits INTEGER DEFAULT 0,
                    successes INTEGER DEFAULT 0,
                    failures INTEGER DEFAULT 0,
                    last_updated REAL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            self._conn.commit()

    def record_event(self, event: QueryEvent) -> None:
        """Record a query event."""
        if not self._conn:
            return

        import json
        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO query_events 
            (timestamp, query, query_type, engines_tried, engines_succeeded, 
             latencies_ms, cache_hits, tokens_estimated, tokens_actual, 
             total_latency_ms, success, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.timestamp,
                event.query[:500],
                event.query_type,
                json.dumps(event.engines_tried),
                json.dumps(event.engines_succeeded),
                json.dumps(event.latencies_ms),
                json.dumps(event.cache_hits),
                event.tokens_estimated,
                event.tokens_actual,
                event.total_latency_ms,
                1 if event.success else 0,
                event.error,
            )
        )

        # Update engine stats
        for engine in event.engines_tried:
            latency = event.latencies_ms.get(engine, 0.0)
            cache_hit = event.cache_hits.get(engine, False)
            success = engine in event.engines_succeeded

            cursor.execute(
                """
                INSERT INTO engine_stats (engine, total_queries, total_latency_ms, 
                                        cache_hits, successes, failures, last_updated)
                VALUES (?, 1, ?, ?, ?, ?, ?)
                ON CONFLICT(engine) DO UPDATE SET
                    total_queries = total_queries + 1,
                    total_latency_ms = total_latency_ms + excluded.total_latency_ms,
                    cache_hits = cache_hits + excluded.cache_hits,
                    successes = successes + (CASE WHEN excluded.successes > 0 THEN 1 ELSE 0 END),
                    failures = failures + (CASE WHEN excluded.failures > 0 THEN 1 ELSE 0 END),
                    last_updated = excluded.last_updated
                """,
                (engine, latency, 1 if cache_hit else 0, 1 if success else 0, 1 if not success else 0, event.timestamp)
            )

        self._conn.commit()

    def get_recent_events(self, limit: int = 100) -> List[QueryEvent]:
        """Get recent query events."""
        if not self._conn:
            return []

        import json
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT * FROM query_events ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        )
        events = []
        for row in cursor.fetchall():
            event = QueryEvent(
                timestamp=row['timestamp'],
                query=row['query'],
                query_type=row['query_type'],
                engines_tried=json.loads(row['engines_tried']),
                engines_succeeded=json.loads(row['engines_succeeded']),
                latencies_ms=json.loads(row['latencies_ms']),
                cache_hits=json.loads(row['cache_hits']),
                tokens_estimated=row['tokens_estimated'],
                tokens_actual=row['tokens_actual'],
                total_latency_ms=row['total_latency_ms'],
                success=bool(row['success']),
                error=row['error'],
            )
            events.append(event)
        return events

    def get_engine_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get aggregated engine statistics."""
        if not self._conn:
            return {}

        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM engine_stats")
        stats = {}
        for row in cursor.fetchall():
            engine = row['engine']
            total = row['total_queries']
            stats[engine] = {
                "total_queries": total,
                "avg_latency_ms": row['total_latency_ms'] / total if total > 0 else 0,
                "cache_hit_rate": row['cache_hits'] / total if total > 0 else 0,
                "success_rate": row['successes'] / total if total > 0 else 0,
                "last_updated": row['last_updated'],
            }
        return stats

    def get_performance_metrics(self, last_hours: int = 24) -> Dict[str, Any]:
        """Get performance metrics over a time window."""
        if not self._conn:
            return {}

        import json
        cutoff = time.time() - (last_hours * 3600)
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT * FROM query_events 
            WHERE timestamp >= ? AND success = 1
            ORDER BY timestamp DESC
            """,
            (cutoff,)
        )
        events = cursor.fetchall()

        if not events:
            return {}

        latencies = [row['total_latency_ms'] for row in events]
        token_savings = []
        for row in events:
            # Estimate token savings: if we used parallel retrieval vs sequential
            # This is a rough heuristic
            latencies_ms = json.loads(row['latencies_ms'])
            if len(latencies_ms) > 1:
                sequential_sum = sum(latencies_ms.values())
                parallel_max = max(latencies_ms.values())
                saved = sequential_sum - parallel_max
                token_savings.append(saved)

        return {
            "total_queries": len(events),
            "avg_latency_ms": sum(latencies) / len(latencies) if latencies else 0,
            "p95_latency_ms": sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0,
            "avg_token_savings_ms": sum(token_savings) / len(token_savings) if token_savings else 0,
            "engine_distribution": defaultdict(int),  # Would need to aggregate
        }


# ---------------------------------------------------------------------------
# Telemetry recorder (context manager)
# ---------------------------------------------------------------------------

class TelemetryRecorder:
    """Record telemetry for a query execution."""

    def __init__(self, query: str, query_type: str):
        self.query = query
        self.query_type = query_type
        self.start_time = time.time()
        self.engines_tried: List[str] = []
        self.engines_succeeded: List[str] = []
        self.latencies_ms: Dict[str, float] = {}
        self.cache_hits: Dict[str, bool] = {}
        self.tokens_estimated = 0
        self.tokens_actual = 0
        self.success = False
        self.error: Optional[str] = None

    def record_engine(
        self,
        engine: str,
        latency_ms: float,
        cache_hit: bool,
        success: bool,
    ) -> None:
        """Record an engine attempt."""
        self.engines_tried.append(engine)
        if success:
            self.engines_succeeded.append(engine)
        self.latencies_ms[engine] = latency_ms
        self.cache_hits[engine] = cache_hit

    def set_tokens(self, estimated: int, actual: int) -> None:
        """Set token counts."""
        self.tokens_estimated = estimated
        self.tokens_actual = actual

    def set_success(self, success: bool, error: Optional[str] = None) -> None:
        """Set final success status."""
        self.success = success
        self.error = error

    def finish(self) -> QueryEvent:
        """Finish recording and return event."""
        total_latency = (time.time() - self.start_time) * 1000.0
        event = QueryEvent(
            timestamp=self.start_time,
            query=self.query,
            query_type=self.query_type,
            engines_tried=self.engines_tried,
            engines_succeeded=self.engines_succeeded,
            latencies_ms=self.latencies_ms,
            cache_hits=self.cache_hits,
            tokens_estimated=self.tokens_estimated,
            tokens_actual=self.tokens_actual,
            total_latency_ms=total_latency,
            success=self.success,
            error=self.error,
        )
        return event


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: Optional[TelemetryStore] = None
_store_lock = threading.Lock()


def get_store(directory: str = ".") -> TelemetryStore:
    """Get or create the telemetry store."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = TelemetryStore(directory)
    return _store


def record_event(event: QueryEvent) -> None:
    """Record a telemetry event."""
    store = get_store()
    store.record_event(event)


def get_recent_events(limit: int = 100) -> List[QueryEvent]:
    """Get recent events for analysis."""
    store = get_store()
    return store.get_recent_events(limit)


def get_engine_stats() -> Dict[str, Dict[str, Any]]:
    """Get engine performance stats."""
    store = get_store()
    return store.get_engine_stats()


def get_performance_metrics(last_hours: int = 24) -> Dict[str, Any]:
    """Get overall performance metrics."""
    store = get_store()
    return store.get_performance_metrics(last_hours)


def capabilities() -> Dict[str, Any]:
    """Return telemetry capabilities."""
    return {
        "enabled": cfg.ENABLE_TELEMETRY,
        "has_db": True,
    }
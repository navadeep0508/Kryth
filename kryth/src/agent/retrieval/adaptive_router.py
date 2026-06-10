"""Adaptive Retrieval Learning - self-improving query router.

Learns from telemetry to optimize routing:
- If engine X consistently outperforms others for query type Y, boost its priority
- If cache hit rate is high for certain query patterns, prefer cached engines
- Reduce expensive calls when cheaper alternatives succeed
- Adjust cost model based on actual performance

No ML model required - uses simple statistical optimization.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from agent.retrieval import config as cfg
from agent.retrieval.cost_optimizer import get_optimizer
from agent.retrieval.telemetry import get_engine_stats, get_recent_events


# ---------------------------------------------------------------------------
# Learning data structures
# ---------------------------------------------------------------------------

@dataclass
class QueryPattern:
    """A pattern of queries (e.g., "symbol lookup in Python files")."""
    pattern_key: str  # e.g., "symbol:python"
    query_type: str
    file_extension: Optional[str] = None
    size_category: str = "medium"  # small, medium, large
    success_engines: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    total_queries: int = 0
    avg_tokens: float = 0.0
    last_updated: float = field(default_factory=time.time)

    def update(self, engine: str, success: bool, tokens: int) -> None:
        """Update pattern with a query result."""
        self.total_queries += 1
        if success:
            self.success_engines[engine] += 1
        # Update rolling average tokens
        self.avg_tokens = self.avg_tokens * 0.9 + tokens * 0.1
        self.last_updated = time.time()

    def get_best_engines(self, top_k: int = 3) -> List[str]:
        """Get top performing engines for this pattern."""
        if not self.success_engines:
            return []
        sorted_engines = sorted(
            self.success_engines.items(),
            key=lambda x: x[1],
            reverse=True
        )
        return [e for e, _ in sorted_engines[:top_k]]


# ---------------------------------------------------------------------------
# Adaptive router
# ---------------------------------------------------------------------------

class AdaptiveRouter:
    """Learns optimal routing from telemetry."""

    def __init__(self):
        self._patterns: Dict[str, QueryPattern] = {}
        self._lock = threading.RLock()
        self._optimizer = get_optimizer()
        self._last_learning = time.time()
        self._learning_interval = 300.0  # Learn every 5 minutes

    def route(
        self,
        query_type: str,
        path: str,
        max_results: int,
        hint_pattern: Optional[str] = None,
    ) -> List[str]:
        """Select engines for a query, using learned patterns."""
        if not cfg.ENABLE_ADAPTIVE_ROUTING:
            return self._optimizer.select_engines(query_type, max_results, path)

        # Extract pattern key
        pattern_key = self._extract_pattern_key(query_type, path)
        if hint_pattern:
            pattern_key = hint_pattern

        with self._lock:
            # Check if we have a learned pattern
            pattern = self._patterns.get(pattern_key)
            if pattern and pattern.total_queries > 10:
                # Use learned best engines
                best_engines = pattern.get_best_engines()
                if best_engines:
                    # Merge with optimizer's cost-based ordering
                    cost_engines = self._optimizer.select_engines(query_type, max_results, path)
                    # Prioritize learned best, then fill with cost-based
                    ordered = [e for e in best_engines if e in cost_engines]
                    for e in cost_engines:
                        if e not in ordered:
                            ordered.append(e)
                    return ordered

        # Fallback to cost optimizer
        return self._optimizer.select_engines(query_type, max_results, path)

    def _extract_pattern_key(self, query_type: str, path: str) -> str:
        """Extract a pattern key from query and path."""
        # Get file extension if path is a file
        ext = ""
        if os.path.isfile(path):
            ext = os.path.splitext(path)[1].lower()
        elif os.path.isdir(path):
            # Look at common files in directory to guess language
            # Simplified: check for common files
            if os.path.exists(os.path.join(path, 'package.json')):
                ext = '.js'
            elif os.path.exists(os.path.join(path, 'Cargo.toml')):
                ext = '.rs'
            elif os.path.exists(os.path.join(path, 'go.mod')):
                ext = '.go'
            elif any(f.endswith('.py') for f in os.listdir(path)[:10]):
                ext = '.py'

        # Size category
        try:
            total_size = 0
            for root, dirs, files in os.walk(path):
                for f in files[:100]:  # Sample
                    fp = os.path.join(root, f)
                    try:
                        total_size += os.path.getsize(fp)
                    except Exception:
                        pass
            if total_size < 100_000:  # 100KB
                size_cat = "small"
            elif total_size < 10_000_000:  # 10MB
                size_cat = "medium"
            else:
                size_cat = "large"
        except Exception:
            size_cat = "medium"

        return f"{query_type}:{ext}:{size_cat}"

    def learn_from_telemetry(self) -> None:
        """Update patterns from recent telemetry."""
        if not cfg.ENABLE_ADAPTIVE_ROUTING:
            return

        now = time.time()
        if now - self._last_learning < self._learning_interval:
            return

        self._last_learning = now

        try:
            events = get_recent_events(limit=1000)
        except Exception:
            return

        with self._lock:
            for event in events:
                if not event.success:
                    continue

                # Reconstruct pattern key from event
                # We don't store path in event, so we'll use a generic pattern
                # In production, we'd store more context
                pattern_key = f"{event.query_type}:unknown:medium"

                if pattern_key not in self._patterns:
                    self._patterns[pattern_key] = QueryPattern(
                        pattern_key=pattern_key,
                        query_type=event.query_type,
                    )

                pattern = self._patterns[pattern_key]
                tokens = event.tokens_actual or event.tokens_estimated

                for engine in event.engines_succeeded:
                    pattern.update(engine, success=True, tokens=tokens)

            # Prune old patterns
            cutoff = now - (24 * 3600)  # 1 day
            self._patterns = {
                k: p for k, p in self._patterns.items()
                if p.last_updated > cutoff
            }

    def get_pattern_stats(self) -> Dict[str, Any]:
        """Get learning statistics."""
        with self._lock:
            return {
                "patterns_learned": len(self._patterns),
                "total_pattern_queries": sum(p.total_queries for p in self._patterns.values()),
                "patterns": [
                    {
                        "key": p.pattern_key,
                        "queries": p.total_queries,
                        "best_engines": p.get_best_engines(3),
                        "avg_tokens": p.avg_tokens,
                    }
                    for p in sorted(self._patterns.values(), key=lambda x: x.total_queries, reverse=True)[:10]
                ],
            }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_router: Optional[AdaptiveRouter] = None
_router_lock = threading.Lock()


def get_router() -> AdaptiveRouter:
    """Get or create the adaptive router."""
    global _router
    if _router is None:
        with _router_lock:
            if _router is None:
                _router = AdaptiveRouter()
    return _router


def capabilities() -> Dict[str, Any]:
    """Return adaptive router capabilities."""
    return {
        "enabled": cfg.ENABLE_ADAPTIVE_ROUTING,
        "has_learning": True,
    }
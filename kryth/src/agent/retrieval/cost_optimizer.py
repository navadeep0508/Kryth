"""Query Cost Optimizer - dynamic routing based on cost estimation.

Instead of fixed query classification, estimates:
- Latency (historical or default)
- Cache hit probability
- Index availability
- Token cost (result size)
- Retrieval cost (compute)

Chooses the cheapest engine that can satisfy the query.

Strategy:
  1. Classify query type (still needed for engine eligibility)
  2. For eligible engines, estimate total cost = latency + token_cost
  3. Sort by cost ascending
  4. Try engines in order until we get sufficient results
  5. Cache the routing decision for similar queries

Learning:
  - Track actual latency per engine
  - Track success rate per engine
  - Adjust cost estimates over time
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from agent.retrieval import config as cfg
from agent.retrieval.cache import get_cache


# ---------------------------------------------------------------------------
# Cost model
# ---------------------------------------------------------------------------

@dataclass
class EngineCost:
    """Cost estimate for a retrieval engine."""
    name: str
    base_latency_ms: float  # Expected base latency
    token_cost_per_k: float  # Cost per kilotoken of result
    cache_hit_penalty: float = 0.1  # Multiplier when cache hit
    cache_miss_penalty: float = 1.0
    availability: float = 1.0  # 0-1, how often engine is available
    success_rate: float = 0.95  # Historical success rate

    def estimate_total_cost(self, estimated_tokens: int, cache_hit: bool = False) -> float:
        """Estimate total cost for a query."""
        token_cost = (estimated_tokens / 1000.0) * self.token_cost_per_k
        latency = self.base_latency_ms
        if cache_hit:
            latency *= self.cache_hit_penalty
        else:
            latency *= self.cache_miss_penalty
        # Factor in availability and success rate
        availability_factor = 1.0 / max(0.01, self.availability)
        success_factor = 1.0 / max(0.01, self.success_rate)
        return (latency + token_cost) * availability_factor * success_factor


# Default cost estimates (in milliseconds)
_DEFAULT_COSTS = {
    'ripgrep': EngineCost('ripgrep', base_latency_ms=2.0, token_cost_per_k=0.1),
    'fts': EngineCost('fts', base_latency_ms=1.0, token_cost_per_k=0.05),
    'symbol': EngineCost('symbol', base_latency_ms=1.0, token_cost_per_k=0.05),
    'ast': EngineCost('ast', base_latency_ms=5.0, token_cost_per_k=0.2),
    'graphify': EngineCost('graphify', base_latency_ms=20.0, token_cost_per_k=0.5),
    'semantic': EngineCost('semantic', base_latency_ms=80.0, token_cost_per_k=1.0),
    'lsp': EngineCost('lsp', base_latency_ms=3.0, token_cost_per_k=0.1),
}


# ---------------------------------------------------------------------------
# Telemetry and learning
# ---------------------------------------------------------------------------

@dataclass
class EngineMetrics:
    """Performance metrics for an engine."""
    total_queries: int = 0
    total_latency_ms: float = 0.0
    cache_hits: int = 0
    successes: int = 0
    failures: int = 0
    recent_latencies: deque[float] = field(default_factory=lambda: deque(maxlen=100))

    @property
    def avg_latency(self) -> float:
        if self.total_queries == 0:
            return 0.0
        return self.total_latency_ms / self.total_queries

    @property
    def success_rate(self) -> float:
        if self.total_queries == 0:
            return 0.0
        return self.successes / self.total_queries

    @property
    def cache_hit_rate(self) -> float:
        if self.total_queries == 0:
            return 0.0
        return self.cache_hits / self.total_queries

    def record(self, latency_ms: float, cache_hit: bool, success: bool) -> None:
        """Record a query execution."""
        self.total_queries += 1
        self.total_latency_ms += latency_ms
        self.recent_latencies.append(latency_ms)
        if cache_hit:
            self.cache_hits += 1
        if success:
            self.successes += 1
        else:
            self.failures += 1


class CostOptimizer:
    """Dynamic cost-based query router with learning."""

    def __init__(self):
        self._costs: Dict[str, EngineCost] = _DEFAULT_COSTS.copy()
        self._metrics: Dict[str, EngineMetrics] = defaultdict(EngineMetrics)
        self._cache = get_cache("cost_optimizer")
        self._lock = threading.RLock()

    def select_engines(self, query_type: str, max_results: int, path: str) -> List[str]:
        """Select engines to try in order of estimated cost."""
        if not cfg.ENABLE_COST_OPTIMIZER:
            # Fallback to static ordering from engine.py
            return self._static_order(query_type)

        with self._lock:
            # Determine eligible engines for this query type
            eligible = self._eligible_engines(query_type)

            if not eligible:
                return []

            # Estimate token cost (rough: max_results * 100 tokens per result)
            estimated_tokens = max_results * 100

            # Score each engine
            scores: List[Tuple[float, str]] = []
            for engine in eligible:
                cost = self._costs.get(engine)
                if cost is None:
                    continue
                # Check cache for this query pattern to estimate cache hit
                cache_key = f"query_cache:{query_type}:{path}:{max_results}"
                cache_hint = self._cache.get(cache_key)  # Could store hit rate
                cache_hit = cache_hint is not None
                total_cost = cost.estimate_total_cost(estimated_tokens, cache_hit)
                scores.append((total_cost, engine))

            # Sort by cost ascending
            scores.sort(key=lambda x: x[0])
            return [engine for _, engine in scores]

    def _eligible_engines(self, query_type: str) -> List[str]:
        """Map query types to eligible engines."""
        mapping = {
            'keyword': ['ripgrep', 'fts', 'symbol'],
            'symbol': ['symbol', 'lsp', 'fts'],
            'structural': ['ast', 'symbol'],
            'docs': ['fts', 'semantic', 'symbol'],
            'relational': ['graphify', 'symbol', 'dep_graph'],
            'semantic': ['semantic', 'graphify', 'fts'],
            'complex': ['symbol', 'fts', 'graphify', 'semantic', 'lsp'],
        }
        return mapping.get(query_type, ['ripgrep', 'fts', 'symbol'])

    def _static_order(self, query_type: str) -> List[str]:
        """Fallback static ordering (cheapest first)."""
        return self._eligible_engines(query_type)

    def record_attempt(self, engine: str, latency_ms: float, cache_hit: bool, success: bool) -> None:
        """Record performance of an engine attempt."""
        with self._lock:
            metrics = self._metrics[engine]
            metrics.record(latency_ms, cache_hit, success)

            # Adapt costs based on recent performance
            self._adapt_costs()

    def _adapt_costs(self) -> None:
        """Adjust engine costs based on recent metrics."""
        # Simple adaptation: if an engine's success rate is low, increase its cost
        # If latency is high, increase cost
        for engine, metrics in self._metrics.items():
            if metrics.total_queries < 10:
                continue  # Not enough data

            cost = self._costs.get(engine)
            if not cost:
                continue

            # Increase cost if success rate < 80%
            if metrics.success_rate < 0.8:
                cost.base_latency_ms *= 1.2
            # Decrease cost if success rate > 95% and latency is low
            elif metrics.success_rate > 0.95 and metrics.avg_latency < 10.0:
                cost.base_latency_ms *= 0.9

            # Clamp to reasonable bounds
            cost.base_latency_ms = max(0.5, min(1000.0, cost.base_latency_ms))

    def get_metrics_report(self) -> Dict[str, Any]:
        """Get performance metrics for all engines."""
        with self._lock:
            report = {}
            for engine, metrics in self._metrics.items():
                report[engine] = {
                    "total_queries": metrics.total_queries,
                    "avg_latency_ms": metrics.avg_latency,
                    "success_rate": metrics.success_rate,
                    "cache_hit_rate": metrics.cache_hit_rate,
                    "current_cost_ms": self._costs[engine].base_latency_ms if engine in self._costs else None,
                }
            return report


# Global optimizer instance
_optimizer: Optional[CostOptimizer] = None
_lock = threading.RLock()


def get_optimizer() -> CostOptimizer:
    """Get the global cost optimizer instance."""
    global _optimizer
    if _optimizer is None:
        _optimizer = CostOptimizer()
    return _optimizer


def capabilities() -> Dict[str, Any]:
    """Return cost optimizer capabilities."""
    return {
        "enabled": cfg.ENABLE_COST_OPTIMIZER,
        "has_learning": True,
    }
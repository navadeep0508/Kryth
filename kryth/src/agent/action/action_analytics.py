"""Action Analytics — benchmark metrics for the Universal Action Layer.

Measures and reports:
  action_latency_ms       per-action wall time
  executor_latency_ms     executor-level timing
  verification_latency_ms time spent in verifier
  rollback_latency_ms     time spent rolling back
  hybrid_efficiency       % of missions using >1 executor
  executor_success_rate   per-executor success %
  memory_hit_rate         % of selections informed by history

Integrates with improvement_lab and benchmark_runner via JSON export.
"""

from __future__ import annotations

import json
import logging

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ActionMetric:
    action_id: str
    goal: str
    executor: str
    action_latency_ms: float
    executor_latency_ms: float
    verification_latency_ms: float
    rollback_latency_ms: float
    success: bool
    verified: bool
    rolled_back: bool
    memory_informed: bool      # True if history data influenced selection
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ActionAnalytics:
    """Collect, aggregate, and export UAL performance metrics."""

    def __init__(self, project_root: str | Path = ".") -> None:
        self._root = Path(project_root).resolve()
        self._metrics: list[ActionMetric] = []
        self._lock = threading.Lock()
        self._session_start = time.time()

    # ── Recording ─────────────────────────────────────────────────────

    def record(self, metric: ActionMetric) -> None:
        with self._lock:
            self._metrics.append(metric)

    def action_started(self) -> None:
        pass

    def action_finished(self) -> None:
        pass

    # ── Aggregation ───────────────────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        with self._lock:
            metrics = list(self._metrics)

        if not metrics:
            return {"total_actions": 0}

        total = len(metrics)
        succeeded = sum(1 for m in metrics if m.success)
        verified  = sum(1 for m in metrics if m.verified)
        rolled_back = sum(1 for m in metrics if m.rolled_back)
        memory_hits = sum(1 for m in metrics if m.memory_informed)

        # Per-executor success rates
        executor_stats: dict[str, dict] = {}
        for m in metrics:
            s = executor_stats.setdefault(m.executor, {"total": 0, "success": 0})
            s["total"] += 1
            s["success"] += int(m.success)
        for ex, s in executor_stats.items():
            s["success_rate"] = s["success"] / s["total"] if s["total"] else 0.0

        # Hybrid execution: missions using >1 executor
        executors_per_action = [m.executor for m in metrics]
        unique_executors = len(set(executors_per_action))
        hybrid_efficiency = (
            (unique_executors / total * 100) if total > 0 else 0.0
        )

        avg = lambda vals: sum(vals) / len(vals) if vals else 0.0

        return {
            "total_actions": total,
            "success_count": succeeded,
            "success_rate_pct": succeeded / total * 100,
            "verified_count": verified,
            "verification_rate_pct": verified / total * 100,
            "rolled_back_count": rolled_back,
            "memory_hit_rate_pct": memory_hits / total * 100,
            "sequential_action_count": total,
            "hybrid_efficiency_pct": hybrid_efficiency,
            "avg_action_latency_ms": avg([m.action_latency_ms for m in metrics]),
            "avg_executor_latency_ms": avg([m.executor_latency_ms for m in metrics]),
            "avg_verification_latency_ms": avg([m.verification_latency_ms for m in metrics]),
            "avg_rollback_latency_ms": avg([m.rollback_latency_ms for m in metrics]),
            "executor_stats": executor_stats,
            "session_elapsed_s": time.time() - self._session_start,
        }

    # ── Export ────────────────────────────────────────────────────────

    def export_json(self, path: Optional[str | Path] = None) -> str:
        """Write analytics summary to JSON. Returns the path written."""
        out = Path(path) if path else (
            self._root / ".kryth" / "action_analytics.json"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        data = self.summary()
        data["exported_at"] = datetime.now(timezone.utc).isoformat()
        out.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.info("Action analytics exported to %s", out)
        return str(out)

    def benchmark_metrics(self) -> dict[str, float]:
        """Return flat dict suitable for benchmark_runner integration."""
        s = self.summary()
        return {
            "ual_action_latency_ms":       s.get("avg_action_latency_ms", 0.0),
            "ual_executor_latency_ms":     s.get("avg_executor_latency_ms", 0.0),
            "ual_verification_latency_ms": s.get("avg_verification_latency_ms", 0.0),
            "ual_rollback_latency_ms":     s.get("avg_rollback_latency_ms", 0.0),
            "ual_success_rate_pct":        s.get("success_rate_pct", 0.0),
            "ual_memory_hit_rate_pct":     s.get("memory_hit_rate_pct", 0.0),
            "ual_sequential_action_count": float(s.get("sequential_action_count", 0)),
            "ual_hybrid_efficiency_pct":   s.get("hybrid_efficiency_pct", 0.0),
        }

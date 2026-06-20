"""Planner Analytics — benchmark metrics for the Universal Planner.

Measures and reports:
  planning_latency_ms      wall time to produce the ActionGraph
  action_count             number of actions in the plan
  dependency_count         total dependency edges
  parallel_layers          topological layer count
  worker_estimate_accuracy % difference: estimated vs actual workers used
  execution_estimate_acc   % difference: estimated vs actual duration
  replan_count             number of dynamic replans during execution
  mission_success          1 or 0

Integrates with benchmark_runner and improvement_lab via JSON export.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class PlannerMetric:
    session_id: str
    objective: str
    complexity: str
    planning_latency_ms: float
    action_count: int
    dependency_count: int
    parallel_layers: int
    estimated_workers: int
    actual_workers: int
    estimated_duration_s: float
    actual_duration_s: float
    replan_count: int
    mission_success: bool
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class PlannerAnalytics:

    def __init__(self, project_root: str | Path = ".") -> None:
        self._root    = Path(project_root).resolve()
        self._metrics: list[PlannerMetric] = []
        self._lock    = threading.Lock()

    def record(self, m: PlannerMetric) -> None:
        with self._lock:
            self._metrics.append(m)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            metrics = list(self._metrics)
        if not metrics:
            return {"total_plans": 0}

        total   = len(metrics)
        success = sum(m.mission_success for m in metrics)

        def avg(vals):
            return sum(vals) / len(vals) if vals else 0.0

        worker_acc = []
        duration_acc = []
        for m in metrics:
            if m.estimated_workers > 0:
                worker_acc.append(abs(m.actual_workers - m.estimated_workers) / m.estimated_workers)
            if m.estimated_duration_s > 0:
                duration_acc.append(abs(m.actual_duration_s - m.estimated_duration_s) / m.estimated_duration_s)

        return {
            "total_plans":               total,
            "success_count":             success,
            "success_rate_pct":          success / total * 100,
            "avg_planning_latency_ms":   avg([m.planning_latency_ms for m in metrics]),
            "avg_action_count":          avg([m.action_count for m in metrics]),
            "avg_dependency_count":      avg([m.dependency_count for m in metrics]),
            "avg_parallel_layers":       avg([m.parallel_layers for m in metrics]),
            "avg_replan_count":          avg([m.replan_count for m in metrics]),
            "worker_estimate_error_pct": avg(worker_acc) * 100,
            "duration_estimate_error_pct": avg(duration_acc) * 100,
        }

    def benchmark_metrics(self) -> dict[str, float]:
        s = self.summary()
        return {
            "planner_latency_ms":           s.get("avg_planning_latency_ms", 0.0),
            "planner_action_count":         s.get("avg_action_count", 0.0),
            "planner_parallel_layers":      s.get("avg_parallel_layers", 0.0),
            "planner_replan_count":         s.get("avg_replan_count", 0.0),
            "planner_success_rate_pct":     s.get("success_rate_pct", 0.0),
            "planner_worker_error_pct":     s.get("worker_estimate_error_pct", 0.0),
        }

    def export_json(self, path: Optional[str | Path] = None) -> str:
        out = Path(path) if path else (self._root / ".kryth" / "planner_analytics.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        data = self.summary()
        data["exported_at"] = datetime.now(timezone.utc).isoformat()
        out.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return str(out)

"""Mission Analytics — benchmark metrics for the Mission OS portfolio."""

from __future__ import annotations

import json
import logging

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class PortfolioMetric:
    timestamp:       str
    total_missions:  int
    running:         int
    queued:          int
    completed:       int
    failed:          int
    success_rate:    float
    avg_elapsed_s:   float
    avg_token_pct:   float
    avg_confidence:  float
    worker_util_pct: float
    token_util_pct:  float


class MissionAnalytics:
    """Collects portfolio-level metrics over time."""

    def __init__(self, project_root: str | Path = ".") -> None:
        self._root    = Path(project_root).resolve()
        self._metrics: list[PortfolioMetric] = []
        self._lock    = threading.Lock()

    def record_snapshot(
        self,
        portfolio: dict,       # id → Mission (typed as dict to avoid circular)
        utilization: dict,
    ) -> None:
        from agent.mos.mission_state import MissionStatus
        missions = list(portfolio.values())
        total    = len(missions)
        running  = sum(1 for m in missions if m.status == MissionStatus.RUNNING)
        queued   = sum(1 for m in missions if m.status == MissionStatus.QUEUED)
        completed = sum(1 for m in missions if m.status == MissionStatus.COMPLETED)
        failed    = sum(1 for m in missions if m.status == MissionStatus.FAILED)

        done = completed + failed
        success_rate = completed / done if done else 0.0

        terminal = [m for m in missions if m.status.value in ("completed", "failed")]
        def avg(vals): return sum(vals) / len(vals) if vals else 0.0

        m = PortfolioMetric(
            timestamp       = datetime.now(timezone.utc).isoformat(),
            total_missions  = total,
            running         = running,
            queued          = queued,
            completed       = completed,
            failed          = failed,
            success_rate    = success_rate,
            avg_elapsed_s   = avg([t.elapsed_s() for t in terminal]),
            avg_token_pct   = avg([t.budget.token_pct for t in terminal]),
            avg_confidence  = avg([t.metrics.confidence for t in missions if t.metrics.confidence > 0]),
            worker_util_pct = utilization.get("workers_pct", 0.0),
            token_util_pct  = utilization.get("tokens_pct", 0.0),
        )
        with self._lock:
            self._metrics.append(m)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            metrics = list(self._metrics)
        if not metrics:
            return {"snapshots": 0}
        latest = metrics[-1]
        def avg(vals): return sum(vals) / len(vals) if vals else 0.0
        return {
            "snapshots":          len(metrics),
            "latest_running":     latest.running,
            "latest_queued":      latest.queued,
            "total_completed":    latest.completed,
            "total_failed":       latest.failed,
            "success_rate":       latest.success_rate,
            "avg_elapsed_s":      avg([m.avg_elapsed_s for m in metrics]),
            "avg_worker_util_pct": avg([m.worker_util_pct for m in metrics]),
            "avg_token_util_pct": avg([m.token_util_pct for m in metrics]),
        }

    def benchmark_metrics(self) -> dict[str, float]:
        s = self.summary()
        return {
            "mos_success_rate":       s.get("success_rate", 0.0),
            "mos_avg_elapsed_s":      s.get("avg_elapsed_s", 0.0),
            "mos_worker_util_pct":    s.get("avg_worker_util_pct", 0.0),
            "mos_token_util_pct":     s.get("avg_token_util_pct", 0.0),
        }

    def export_json(self, path: Optional[str | Path] = None) -> str:
        out = Path(path) if path else (self._root / ".kryth" / "mos" / "analytics.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        data = self.summary()
        data["exported_at"] = datetime.now(timezone.utc).isoformat()
        out.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return str(out)

"""Executive Analytics — benchmark metrics for the EIL."""

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
class ExecutiveMetric:
    session_id: str
    mission: str
    decision: str
    confidence_overall: float
    risk_level: str
    quality_overall: float
    budget_pct: float
    replan_count: int
    escalation_count: int
    mission_success: bool
    elapsed_s: float
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ExecutiveAnalytics:

    def __init__(self, project_root: str | Path = ".") -> None:
        self._root    = Path(project_root).resolve()
        self._metrics: list[ExecutiveMetric] = []
        self._lock    = threading.Lock()

    def record(self, m: ExecutiveMetric) -> None:
        with self._lock:
            self._metrics.append(m)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            metrics = list(self._metrics)
        if not metrics:
            return {"total_sessions": 0}

        total   = len(metrics)
        success = sum(m.mission_success for m in metrics)

        def avg(vals):
            return sum(vals) / len(vals) if vals else 0.0

        decision_counts: dict[str, int] = {}
        for m in metrics:
            decision_counts[m.decision] = decision_counts.get(m.decision, 0) + 1

        return {
            "total_sessions":         total,
            "success_count":          success,
            "success_rate_pct":       success / total * 100,
            "avg_confidence":         avg([m.confidence_overall for m in metrics]),
            "avg_quality":            avg([m.quality_overall for m in metrics]),
            "avg_budget_pct":         avg([m.budget_pct for m in metrics]),
            "avg_replan_count":       avg([m.replan_count for m in metrics]),
            "avg_escalation_count":   avg([m.escalation_count for m in metrics]),
            "avg_elapsed_s":          avg([m.elapsed_s for m in metrics]),
            "decision_distribution":  decision_counts,
        }

    def benchmark_metrics(self) -> dict[str, float]:
        s = self.summary()
        return {
            "eil_success_rate_pct":     s.get("success_rate_pct", 0.0),
            "eil_avg_confidence":       s.get("avg_confidence", 0.0),
            "eil_avg_quality":          s.get("avg_quality", 0.0),
            "eil_avg_replan_count":     s.get("avg_replan_count", 0.0),
            "eil_avg_escalation_count": s.get("avg_escalation_count", 0.0),
        }

    def export_json(self, path: Optional[str | Path] = None) -> str:
        out = Path(path) if path else (self._root / ".kryth" / "executive_analytics.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        data = self.summary()
        data["exported_at"] = datetime.now(timezone.utc).isoformat()
        out.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return str(out)

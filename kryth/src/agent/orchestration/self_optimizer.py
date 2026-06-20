"""SelfOptimizer — V8 Phase 5.

KRYTH learns from missions. Records mission history and generates
recommendations — NEVER auto-modifies core architecture.

Stores (in memory + optional JSON file):
  task_type, models_used, success, cost_usd, duration_s, tier,
  failures, recovery_actions, token_count

Detects patterns:
  * Best models per task type (by success rate × speed)
  * Bad model/task combos (failure rate > threshold)
  * Slow workflows (P95 duration)
  * Failure clusters (recurring error patterns)

Generates text recommendations only. The operator applies them manually
or via the /exec and /mode commands.
"""
from __future__ import annotations

import json
import statistics
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class MissionRecord:
    """One completed mission's performance record."""
    mission_id: str
    task_type: str
    tier: int
    success: bool
    duration_s: float
    cost_usd: float
    token_count: int
    models_used: List[str]
    recovery_attempts: int
    failure_classes: List[str]
    ts: float = field(default_factory=time.monotonic)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Recommendation:
    priority: str          # "high" | "medium" | "low"
    category: str          # "routing" | "tier" | "model" | "workflow"
    title: str
    detail: str
    action: str            # suggested /exec, /mode, config change, or env var


class SelfOptimizerEngine:
    """Records mission history and emits optimization recommendations.

    IMPORTANT: This engine ONLY produces recommendations — it never calls
    setenv(), never touches scheduling logic, never modifies the DAG, and
    never patches any other system. It is purely advisory.
    """

    _BAD_FAILURE_RATE   = 0.25   # ≥25% failure rate for a model/task combo
    _SLOW_DURATION_MULT = 3.0    # P95 duration > 3× median = slow
    _MIN_SAMPLES        = 3      # minimum missions before drawing conclusions

    def __init__(self, history_path: Optional[Path] = None) -> None:
        self._records: List[MissionRecord] = []
        self._history_path = history_path
        if history_path and history_path.exists():
            self._load(history_path)

    def record(
        self,
        mission_id: str,
        task_type: str,
        tier: int,
        success: bool,
        duration_s: float,
        cost_usd: float = 0.0,
        token_count: int = 0,
        models_used: Optional[List[str]] = None,
        recovery_attempts: int = 0,
        failure_classes: Optional[List[str]] = None,
    ) -> MissionRecord:
        rec = MissionRecord(
            mission_id=mission_id,
            task_type=task_type,
            tier=tier,
            success=success,
            duration_s=duration_s,
            cost_usd=cost_usd,
            token_count=token_count,
            models_used=models_used or [],
            recovery_attempts=recovery_attempts,
            failure_classes=failure_classes or [],
        )
        self._records.append(rec)
        if self._history_path:
            self._append(self._history_path, rec)
        return rec

    def record_from_v5_report(self, v5_report, mission_id: str = "") -> Optional[MissionRecord]:
        """Convenience: record from a V5MissionReport."""
        mr = getattr(v5_report, "mission_result", None)
        if mr is None:
            return None
        ts = getattr(v5_report, "token_summary", None)
        bs = getattr(v5_report, "budget_status", None)
        qr = getattr(v5_report, "quality_report", None)
        fail_classes: List[str] = []
        for ms in getattr(mr, "milestones", []):
            if not ms.success:
                fail_classes.append(getattr(ms, "rework_notes", "unknown")[:50])
        return self.record(
            mission_id=mission_id or getattr(mr, "project_name", ""),
            task_type=getattr(mr, "project_name", "unknown"),
            tier=getattr(v5_report, "tier", 4),
            success=mr.success,
            duration_s=getattr(v5_report, "elapsed_s", 0.0),
            cost_usd=getattr(bs, "spent_usd", 0.0) if bs else 0.0,
            token_count=getattr(ts, "total_tokens", 0) if ts else 0,
            recovery_attempts=getattr(v5_report, "recovery_attempts", 0),
            failure_classes=fail_classes,
        )

    def recommendations(self) -> List[Recommendation]:
        recs: List[Recommendation] = []
        if len(self._records) < self._MIN_SAMPLES:
            return [Recommendation("low", "workflow",
                "Not enough data for recommendations",
                f"Run at least {self._MIN_SAMPLES} missions to generate evidence-based suggestions.",
                "")]

        # 1. Tier efficiency: is the chosen tier costing too much?
        by_tier: Dict[int, List[MissionRecord]] = defaultdict(list)
        for r in self._records:
            by_tier[r.tier].append(r)
        for tier, records in by_tier.items():
            failures = [r for r in records if not r.success]
            if len(records) >= self._MIN_SAMPLES and len(failures) / len(records) > self._BAD_FAILURE_RATE:
                recs.append(Recommendation(
                    "high", "tier",
                    f"Tier {tier} has high failure rate",
                    f"{len(failures)}/{len(records)} missions failed at tier {tier}.",
                    f"Consider routing complex tasks to a higher tier or checking recovery config.",
                ))

        # 2. Cost outliers: missions spending > 3× median
        costs = [r.cost_usd for r in self._records if r.cost_usd > 0]
        if len(costs) >= self._MIN_SAMPLES:
            med = statistics.median(costs)
            outliers = [r for r in self._records if r.cost_usd > med * 3]
            if outliers:
                recs.append(Recommendation(
                    "medium", "routing",
                    f"{len(outliers)} missions spent > 3× median cost",
                    f"Median cost ${med:.4f}. High-cost missions: {[r.mission_id for r in outliers[:3]]}",
                    f"Use PONYTAIL profile for these task types: /mode ponytail",
                ))

        # 3. Slow workflows: P95 duration > 3× median
        durations = [r.duration_s for r in self._records]
        if len(durations) >= self._MIN_SAMPLES:
            med_dur = statistics.median(durations)
            try:
                p95 = sorted(durations)[int(len(durations) * 0.95)]
            except IndexError:
                p95 = max(durations)
            if p95 > med_dur * self._SLOW_DURATION_MULT:
                slow = [r for r in self._records if r.duration_s >= p95]
                recs.append(Recommendation(
                    "medium", "workflow",
                    "P95 mission duration is unusually high",
                    f"P95={p95:.0f}s vs median {med_dur:.0f}s. Slow missions: {[r.task_type for r in slow[:3]]}",
                    "Review hot_path_cache hit rates; enable KRYTH_EXECUTION_TIER for these tasks.",
                ))

        # 4. Recovery storms: repeated recovery on same task type
        recovery_by_type: Dict[str, int] = defaultdict(int)
        for r in self._records:
            recovery_by_type[r.task_type] += r.recovery_attempts
        for task_type, total in recovery_by_type.items():
            count = sum(1 for r in self._records if r.task_type == task_type)
            if count >= self._MIN_SAMPLES and total / count > 1.5:
                recs.append(Recommendation(
                    "high", "workflow",
                    f"'{task_type}' triggers frequent recovery",
                    f"Average {total/count:.1f} recovery attempts per mission.",
                    "Investigate failure_intelligence.py root cause; check provider reliability.",
                ))

        # 5. Positive signal: surface best-performing tier
        successes_by_tier = {
            t: sum(1 for r in recs_t if r.success) / len(recs_t)
            for t, recs_t in by_tier.items()
            if len(recs_t) >= self._MIN_SAMPLES
        }
        if successes_by_tier:
            best_tier = max(successes_by_tier, key=successes_by_tier.get)
            recs.append(Recommendation(
                "low", "tier",
                f"Tier {best_tier} has highest success rate",
                f"Tier {best_tier}: {successes_by_tier[best_tier]:.0%} success rate across {len(by_tier[best_tier])} missions.",
                f"Set KRYTH_EXECUTION_TIER={best_tier} as default for similar workloads.",
            ))

        return recs

    def summary(self) -> str:
        n = len(self._records)
        if n == 0:
            return "SelfOptimizer: no mission history yet."
        ok = sum(1 for r in self._records if r.success)
        avg_cost = statistics.mean(r.cost_usd for r in self._records) if self._records else 0
        avg_dur  = statistics.mean(r.duration_s for r in self._records) if self._records else 0
        lines = [
            f"SelfOptimizer: {n} missions  {ok/n:.0%} success  avg ${avg_cost:.4f} avg {avg_dur:.1f}s",
        ]
        for rec in self.recommendations()[:3]:
            lines.append(f"  [{rec.priority.upper()}] {rec.title}: {rec.action}")
        return "\n".join(lines)

    def _load(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text())
            for d in data:
                self._records.append(MissionRecord(**d))
        except Exception:
            pass

    def _append(self, path: Path, rec: MissionRecord) -> None:
        try:
            existing = json.loads(path.read_text()) if path.exists() else []
            existing.append(rec.to_dict())
            path.write_text(json.dumps(existing, indent=2))
        except Exception:
            pass

    @property
    def record_count(self) -> int:
        return len(self._records)

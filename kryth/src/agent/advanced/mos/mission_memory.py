"""Mission Memory — JSONL persistence for the Mission OS portfolio."""

from __future__ import annotations

import json
import logging

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from agent.mos.mission_state import Mission

logger = logging.getLogger(__name__)


@dataclass
class MissionRecord:
    mission_id:     str
    name:           str
    goal:           str
    status:         str
    priority:       str
    elapsed_s:      float
    actions_total:  int
    actions_done:   int
    actions_failed: int
    replan_count:   int
    token_pct:      float
    confidence:     float
    risk_level:     str
    result_summary: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class MissionMemory:
    """Appends mission outcome records to .kryth/mos/mission_memory.jsonl."""

    def __init__(self, project_root: str | Path = ".") -> None:
        self._root = Path(project_root).resolve()
        self._path = self._root / ".kryth" / "mos" / "mission_memory.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._records: list[MissionRecord] = []

    def record(self, r: MissionRecord) -> None:
        with self._lock:
            self._records.append(r)
            try:
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(r)) + "\n")
            except Exception as exc:
                logger.warning("MissionMemory write failed: %s", exc)

    def record_mission(self, mission: Mission) -> None:
        self.record(MissionRecord(
            mission_id=mission.id,
            name=mission.name,
            goal=mission.goal,
            status=mission.status.value,
            priority=mission.priority.value,
            elapsed_s=mission.elapsed_s(),
            actions_total=mission.metrics.actions_total,
            actions_done=mission.metrics.actions_done,
            actions_failed=mission.metrics.actions_failed,
            replan_count=mission.metrics.replan_count,
            token_pct=mission.budget.token_pct,
            confidence=mission.metrics.confidence,
            risk_level=mission.metrics.risk_level,
            result_summary=mission.result_summary,
        ))

    def load_all(self) -> list[MissionRecord]:
        records: list[MissionRecord] = []
        if not self._path.exists():
            return records
        with self._lock:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                try:
                    d = json.loads(line)
                    records.append(MissionRecord(**{
                        k: v for k, v in d.items()
                        if k in MissionRecord.__dataclass_fields__
                    }))
                except Exception:
                    pass
        return records

    def stats(self) -> dict[str, Any]:
        with self._lock:
            records = list(self._records)
        if not records:
            return {"total": 0}
        total    = len(records)
        success  = sum(1 for r in records if r.status == "completed")
        failed   = sum(1 for r in records if r.status == "failed")
        def avg(vals): return sum(vals) / len(vals) if vals else 0.0
        return {
            "total":          total,
            "completed":      success,
            "failed":         failed,
            "success_rate":   success / total if total else 0.0,
            "avg_elapsed_s":  avg([r.elapsed_s for r in records]),
            "avg_token_pct":  avg([r.token_pct for r in records]),
            "avg_confidence": avg([r.confidence for r in records]),
        }

"""Release management (Phase D).

A release advances through a fixed pipeline — development → verification →
staging → canary → production — and can roll back. Each release records its stage
history, durations, a deployment-confidence score, and rollback outcome. The
pipeline is a pure state machine; it does not deploy anything (Mission OS /
executors do the real work) — it tracks and reports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class Stage(Enum):
    DEVELOPMENT = "development"
    VERIFICATION = "verification"
    STAGING = "staging"
    CANARY = "canary"
    PRODUCTION = "production"
    ROLLED_BACK = "rolled_back"


# Forward order. ROLLED_BACK is reachable from any deployed stage.
_FORWARD = [Stage.DEVELOPMENT, Stage.VERIFICATION, Stage.STAGING, Stage.CANARY, Stage.PRODUCTION]


@dataclass
class StageEvent:
    stage: str
    at: float
    duration_s: float = 0.0
    ok: bool = True
    note: str = ""


@dataclass
class Release:
    id: str
    title: str = ""
    stage: Stage = Stage.DEVELOPMENT
    history: List[StageEvent] = field(default_factory=list)
    confidence: float = 0.0          # 0–1 deployment confidence
    rolled_back: bool = False
    _stage_started: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "stage": self.stage.value,
            "confidence": round(self.confidence, 2), "rolled_back": self.rolled_back,
            "duration_s": round(self.total_duration(), 1),
            "history": [{"stage": e.stage, "duration_s": round(e.duration_s, 1),
                         "ok": e.ok, "note": e.note} for e in self.history],
        }

    def total_duration(self) -> float:
        return sum(e.duration_s for e in self.history)


class ReleasePipeline:
    """Tracks releases through stages with confidence + rollback metrics."""

    def __init__(self) -> None:
        self._releases: Dict[str, Release] = {}

    def create(self, release_id: str, *, title: str = "", now: float = 0.0) -> Release:
        r = Release(id=release_id, title=title or release_id)
        r._stage_started = now
        self._releases[release_id] = r
        return r

    def get(self, release_id: str) -> Optional[Release]:
        return self._releases.get(release_id)

    def _close_stage(self, r: Release, now: float, ok: bool, note: str) -> None:
        dur = max(0.0, now - r._stage_started)
        r.history.append(StageEvent(stage=r.stage.value, at=now, duration_s=dur, ok=ok, note=note))
        r._stage_started = now

    def advance(self, release_id: str, *, now: float = 0.0, confidence: float = 1.0,
                note: str = "") -> Stage:
        """Promote a release to the next forward stage. Records the closing event
        for the current stage and updates deployment confidence."""
        r = self._releases.get(release_id)
        if not r or r.rolled_back or r.stage is Stage.PRODUCTION:
            return r.stage if r else Stage.DEVELOPMENT
        self._close_stage(r, now, ok=True, note=note)
        idx = _FORWARD.index(r.stage)
        r.stage = _FORWARD[min(idx + 1, len(_FORWARD) - 1)]
        # confidence is the running min — one shaky stage lowers overall confidence.
        r.confidence = confidence if not r.history else min(r.confidence or 1.0, confidence)
        return r.stage

    def rollback(self, release_id: str, *, now: float = 0.0, note: str = "") -> bool:
        r = self._releases.get(release_id)
        if not r or r.rolled_back:
            return False
        self._close_stage(r, now, ok=False, note=note or "rollback")
        r.stage = Stage.ROLLED_BACK
        r.rolled_back = True
        r.confidence = 0.0
        return True

    # -- metrics ------------------------------------------------------------

    def metrics(self) -> dict:
        rs = list(self._releases.values())
        if not rs:
            return {"total": 0, "in_production": 0, "rolled_back": 0,
                    "rollback_rate": 0.0, "avg_deploy_duration_s": 0.0,
                    "avg_confidence": 0.0}
        prod = sum(1 for r in rs if r.stage is Stage.PRODUCTION)
        rb = sum(1 for r in rs if r.rolled_back)
        durations = [r.total_duration() for r in rs if r.history]
        confs = [r.confidence for r in rs if r.stage is Stage.PRODUCTION]
        return {
            "total": len(rs), "in_production": prod, "rolled_back": rb,
            "rollback_rate": round(rb / len(rs), 3),
            "deployment_success_rate": round(prod / len(rs), 3),
            "avg_deploy_duration_s": round(sum(durations) / len(durations), 1) if durations else 0.0,
            "avg_confidence": round(sum(confs) / len(confs), 2) if confs else 0.0,
        }

    def history(self) -> List[dict]:
        return [r.to_dict() for r in self._releases.values()]

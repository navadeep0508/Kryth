"""BlockerIntelligence — V5 Phase 6.

Workers no longer just wait — the system understands, tracks, and displays
every blocker with full impact analysis.

Tracks per blocker:
  * blocked_by   — what is causing the block
  * blocking     — what downstream work is held up
  * impact       — severity (LOW / MEDIUM / HIGH / CRITICAL)
  * affected_teams  — which teams are waiting
  * affected_milestones — which milestones are delayed
  * estimated_delay_s   — conservative delay estimate

Display surfaces:
  * top_blockers()      — ranked by impact
  * critical_blockers() — CRITICAL-only, for escalation
  * blocked_deliverables()
  * blocked_workers()
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set


class BlockerImpact(Enum):
    LOW      = 1
    MEDIUM   = 2
    HIGH     = 3
    CRITICAL = 4


@dataclass
class Blocker:
    blocker_id: str
    description: str
    blocked_workers: List[str] = field(default_factory=list)
    blocked_deliverables: List[str] = field(default_factory=list)
    affected_teams: List[str] = field(default_factory=list)
    affected_milestones: List[str] = field(default_factory=list)
    blocked_by: Optional[str] = None    # what is causing this blocker
    blocking: List[str] = field(default_factory=list)   # what this blocks downstream
    impact: BlockerImpact = BlockerImpact.MEDIUM
    estimated_delay_s: float = 0.0
    created_at: float = field(default_factory=time.monotonic)
    resolved_at: Optional[float] = None

    @property
    def is_resolved(self) -> bool:
        return self.resolved_at is not None

    @property
    def age_s(self) -> float:
        return (self.resolved_at or time.monotonic()) - self.created_at

    @property
    def impact_score(self) -> int:
        return self.impact.value * (len(self.blocked_workers) + len(self.affected_milestones) + 1)


@dataclass
class BlockerReport:
    total_blockers: int = 0
    active_blockers: int = 0
    resolved_blockers: int = 0
    critical_count: int = 0
    high_count: int = 0
    blocked_worker_count: int = 0
    blocked_deliverable_count: int = 0
    estimated_total_delay_s: float = 0.0
    top_blockers: List[Blocker] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Blockers: {self.active_blockers} active / {self.total_blockers} total",
            f"  Critical: {self.critical_count}  High: {self.high_count}",
            f"  Blocked workers: {self.blocked_worker_count}",
            f"  Blocked deliverables: {self.blocked_deliverable_count}",
        ]
        if self.estimated_total_delay_s:
            lines.append(f"  Estimated delay: {self.estimated_total_delay_s:.0f}s")
        if self.top_blockers:
            lines.append("  Top blockers:")
            for b in self.top_blockers[:3]:
                lines.append(f"    [{b.impact.name}] {b.description[:60]}")
        return "\n".join(lines)


class BlockerIntelligence:
    """Tracks and analyses blockers across a mission."""

    def __init__(self) -> None:
        self._blockers: Dict[str, Blocker] = {}
        self._counter = 0

    def register(
        self,
        description: str,
        blocked_workers: Optional[List[str]] = None,
        blocked_deliverables: Optional[List[str]] = None,
        affected_teams: Optional[List[str]] = None,
        affected_milestones: Optional[List[str]] = None,
        blocked_by: Optional[str] = None,
        blocking: Optional[List[str]] = None,
        impact: BlockerImpact = BlockerImpact.MEDIUM,
        estimated_delay_s: float = 300.0,
    ) -> Blocker:
        """Register a new blocker and return it."""
        self._counter += 1
        bid = f"blk_{self._counter:04d}"
        b = Blocker(
            blocker_id=bid,
            description=description,
            blocked_workers=list(blocked_workers or []),
            blocked_deliverables=list(blocked_deliverables or []),
            affected_teams=list(affected_teams or []),
            affected_milestones=list(affected_milestones or []),
            blocked_by=blocked_by,
            blocking=list(blocking or []),
            impact=impact,
            estimated_delay_s=estimated_delay_s,
        )
        self._blockers[bid] = b
        return b

    def resolve(self, blocker_id: str) -> bool:
        b = self._blockers.get(blocker_id)
        if b and not b.is_resolved:
            b.resolved_at = time.monotonic()
            return True
        return False

    def infer_from_output(
        self,
        worker_id: str,
        module_name: str,
        output: str,
        milestone_name: str = "",
    ) -> Optional[Blocker]:
        """Automatically infer a blocker from worker output text."""
        low = (output or "").lower()
        markers = [
            ("blocked by", BlockerImpact.HIGH),
            ("waiting for", BlockerImpact.MEDIUM),
            ("depends on", BlockerImpact.MEDIUM),
            ("cannot proceed", BlockerImpact.HIGH),
            ("unable to complete", BlockerImpact.HIGH),
            ("missing dependency", BlockerImpact.CRITICAL),
            ("api key", BlockerImpact.HIGH),
            ("permission denied", BlockerImpact.HIGH),
        ]
        for marker, impact in markers:
            if marker in low:
                idx   = low.find(marker)
                snip  = output[idx: idx + 80].strip()
                return self.register(
                    description=f"{worker_id}: {snip}",
                    blocked_workers=[worker_id],
                    affected_teams=[f"{module_name} Team"],
                    affected_milestones=[milestone_name] if milestone_name else [],
                    impact=impact,
                    estimated_delay_s=600.0,
                )
        return None

    def top_blockers(self, n: int = 5) -> List[Blocker]:
        active = [b for b in self._blockers.values() if not b.is_resolved]
        return sorted(active, key=lambda b: b.impact_score, reverse=True)[:n]

    def critical_blockers(self) -> List[Blocker]:
        return [
            b for b in self._blockers.values()
            if not b.is_resolved and b.impact == BlockerImpact.CRITICAL
        ]

    def blocked_deliverables(self) -> List[str]:
        result: List[str] = []
        for b in self._blockers.values():
            if not b.is_resolved:
                result.extend(b.blocked_deliverables)
        return list(set(result))

    def blocked_workers(self) -> List[str]:
        result: List[str] = []
        for b in self._blockers.values():
            if not b.is_resolved:
                result.extend(b.blocked_workers)
        return list(set(result))

    def generate_report(self) -> BlockerReport:
        all_b   = list(self._blockers.values())
        active  = [b for b in all_b if not b.is_resolved]
        report  = BlockerReport(
            total_blockers=len(all_b),
            active_blockers=len(active),
            resolved_blockers=len(all_b) - len(active),
            critical_count=sum(1 for b in active if b.impact == BlockerImpact.CRITICAL),
            high_count=sum(1 for b in active if b.impact == BlockerImpact.HIGH),
            blocked_worker_count=len(self.blocked_workers()),
            blocked_deliverable_count=len(self.blocked_deliverables()),
            estimated_total_delay_s=sum(b.estimated_delay_s for b in active),
            top_blockers=self.top_blockers(5),
        )
        return report

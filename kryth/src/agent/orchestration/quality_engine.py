"""ExecutionQualityEngine — V5 Phase 11.

Measures mission quality across multiple dimensions and produces a single
Mission Quality Score (0-100) plus a breakdown.

Dimensions:
  Contract Quality      — proxy: fraction of contract validations that pass
  Deliverable Quality   — fraction of planned deliverables actually completed
  Validation Pass Rate  — contract validations passed / total run
  Recovery Success      — fraction of AutonomousRecovery attempts that succeeded
  Rework Rate           — milestones requiring rework / total milestones (inverted)
  Approval Rate         — team-lead approvals / total reviews
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class QualityBreakdown:
    contract_quality: float = 1.0
    deliverable_quality: float = 1.0
    validation_pass_rate: float = 1.0
    recovery_success_rate: float = 1.0
    rework_rate: float = 0.0
    approval_rate: float = 1.0

    def weighted_score(self) -> float:
        components = [
            self.contract_quality,
            self.deliverable_quality,
            self.validation_pass_rate,
            self.recovery_success_rate,
            1.0 - self.rework_rate,
            self.approval_rate,
        ]
        return sum(components) / len(components) * 100


@dataclass
class QualityReport:
    mission_name: str
    breakdown: QualityBreakdown
    mission_quality_score: float
    notes: List[str] = field(default_factory=list)

    def grade(self) -> str:
        s = self.mission_quality_score
        if s >= 85:
            return "excellent"
        if s >= 70:
            return "good"
        if s >= 50:
            return "fair"
        return "poor"

    def summary(self) -> str:
        b = self.breakdown
        lines = [
            f"Quality Report: {self.mission_name}",
            f"  Mission Quality Score: {self.mission_quality_score:.0f}/100 ({self.grade()})",
            f"  Contract quality:      {b.contract_quality:.0%}",
            f"  Deliverable quality:   {b.deliverable_quality:.0%}",
            f"  Validation pass rate:  {b.validation_pass_rate:.0%}",
            f"  Recovery success:      {b.recovery_success_rate:.0%}",
            f"  Rework rate:           {b.rework_rate:.0%}",
            f"  Approval rate:         {b.approval_rate:.0%}",
        ]
        return "\n".join(lines)


def compute_quality_report(
    mission_name: str,
    mission_result,                    # MissionDeliveryResult
    recovery_success_rate: float = 1.0,
) -> QualityReport:
    """Compute a QualityReport from a MilestoneEngine MissionDeliveryResult."""
    milestones = getattr(mission_result, "milestones", [])
    if not milestones:
        return QualityReport(
            mission_name=mission_name,
            breakdown=QualityBreakdown(),
            mission_quality_score=0.0,
            notes=["no milestones executed"],
        )

    total_contracts = sum(len(ms.contract_validations) for ms in milestones)
    passed_contracts = sum(
        sum(1 for v in ms.contract_validations.values() if getattr(v, "passed", False))
        for ms in milestones
    )
    validation_pass_rate = passed_contracts / total_contracts if total_contracts else 1.0

    total_delivs = getattr(mission_result, "total_deliverables_planned", 0)
    done_delivs  = getattr(mission_result, "total_deliverables_completed", 0)
    deliverable_quality = done_delivs / total_delivs if total_delivs else 1.0

    rework_count = sum(1 for ms in milestones if getattr(ms, "rework_required", False))
    rework_rate = rework_count / len(milestones)

    total_reviews = sum(len(ms.team_lead_reviews) for ms in milestones)
    approved_reviews = sum(
        sum(1 for r in ms.team_lead_reviews.values() if getattr(r, "approved", False))
        for ms in milestones
    )
    approval_rate = approved_reviews / total_reviews if total_reviews else 1.0

    # Contract quality proxy: a contract that validates cleanly was well-specified.
    contract_quality = validation_pass_rate

    breakdown = QualityBreakdown(
        contract_quality=contract_quality,
        deliverable_quality=deliverable_quality,
        validation_pass_rate=validation_pass_rate,
        recovery_success_rate=recovery_success_rate,
        rework_rate=rework_rate,
        approval_rate=approval_rate,
    )
    score = breakdown.weighted_score()
    return QualityReport(mission_name=mission_name, breakdown=breakdown, mission_quality_score=score)

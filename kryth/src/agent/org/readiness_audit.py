"""Final readiness audit (Phase O).

Scores organizational maturity across dimensions — architecture, operational,
organizational, release, portfolio, department, budget — and rolls them into an
overall enterprise-readiness score with remaining gaps, scaling risks, and a
future roadmap. Pure: callers pass measured maturity inputs (0–100 each).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


_DIMENSIONS = (
    "architecture", "operational", "organizational",
    "release", "portfolio", "department", "budget",
)


@dataclass
class MaturityInputs:
    architecture: float = 0.0
    operational: float = 0.0
    organizational: float = 0.0
    release: float = 0.0
    portfolio: float = 0.0
    department: float = 0.0
    budget: float = 0.0

    def as_dict(self) -> Dict[str, float]:
        return {d: float(getattr(self, d)) for d in _DIMENSIONS}


@dataclass
class AuditResult:
    overall: float
    grade: str
    dimensions: Dict[str, float] = field(default_factory=dict)
    gaps: List[str] = field(default_factory=list)
    scaling_risks: List[str] = field(default_factory=list)
    roadmap: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"overall": round(self.overall, 1), "grade": self.grade,
                "dimensions": self.dimensions, "gaps": self.gaps,
                "scaling_risks": self.scaling_risks, "roadmap": self.roadmap}


def _grade(score: float) -> str:
    if score >= 90:
        return "Enterprise ready"
    if score >= 80:
        return "Enterprise ready with monitoring"
    if score >= 70:
        return "Scale-up ready"
    if score >= 55:
        return "Team ready"
    return "Foundational"


def audit(inp: MaturityInputs) -> AuditResult:
    dims = {k: round(v, 1) for k, v in inp.as_dict().items()}
    overall = sum(dims.values()) / len(dims)

    gaps, risks, roadmap = [], [], []
    for dim, score in dims.items():
        if score < 70:
            gaps.append(f"{dim} maturity {score:.0f}/100 — below scale-up threshold")
    if dims["operational"] < 80:
        risks.append("Operational maturity unproven under sustained load (no 24h/100-mission soak).")
    if dims["budget"] < 70:
        risks.append("Budget enforcement not exercised at scale — cost overrun risk.")
    if dims["department"] < 70:
        risks.append("Department reputation is shallow — routing decisions have weak priors.")

    # Future roadmap = the lowest dimensions, in priority order.
    for dim, score in sorted(dims.items(), key=lambda kv: kv[1])[:3]:
        roadmap.append(f"Raise {dim} maturity (currently {score:.0f}) toward 90+.")

    return AuditResult(overall=overall, grade=_grade(overall), dimensions=dims,
                       gaps=gaps, scaling_risks=risks, roadmap=roadmap)


def audit_report(inp: MaturityInputs, *, title: str = "KRYTH Enterprise Readiness Audit") -> str:
    r = audit(inp)
    lines = [f"# {title}", "",
             f"**Overall enterprise readiness: {r.overall:.1f}/100 — {r.grade}**", "",
             "## Maturity dimensions"]
    for dim, score in r.dimensions.items():
        lines.append(f"- {dim.title()}: {score:.0f}/100")
    if r.gaps:
        lines += ["", "## Remaining gaps"] + [f"- {g}" for g in r.gaps]
    if r.scaling_risks:
        lines += ["", "## Scaling risks"] + [f"- {x}" for x in r.scaling_risks]
    if r.roadmap:
        lines += ["", "## Future roadmap"] + [f"- {x}" for x in r.roadmap]
    return "\n".join(lines)

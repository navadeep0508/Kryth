"""FailureIntelligence — V8 Phase 6.

Classifies, clusters, and explains why missions fail. Built on top of the
existing AutonomousRecoveryEngine's trigger classification and MilestoneResult
data, plus real provider error patterns observed in production.

Failure taxonomy (7 classes):
  PROVIDER     — API rate limits, 503s, stream interruptions, auth failures
  NETWORK      — connection drops, timeouts, DNS failures
  TOOL         — tool schema errors, tool call malformed, write permission denied
  PLANNER      — bad plan (cycles, zero agents, contradictory constraints)
  WORKER       — worker hit max_turns, AGENT_COMPLETE missing, bad output
  VALIDATION   — contract check failed, artifacts missing, tests failed
  RECOVERY     — recovery itself failed or escalated to limit

FailureCluster groups similar failures across multiple missions so recurring
patterns are visible. RootCauseReport explains the top 5 failure modes with
frequency, impact (blocked deliverables), and mitigation guidance.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class FailureClass(Enum):
    PROVIDER   = "provider"
    NETWORK    = "network"
    TOOL       = "tool"
    PLANNER    = "planner"
    WORKER     = "worker"
    VALIDATION = "validation"
    RECOVERY   = "recovery"
    UNKNOWN    = "unknown"


# ── Classification patterns ────────────────────────────────────────────────────

_PATTERNS: List[tuple] = [
    (FailureClass.PROVIDER, [
        r"rate.?limit", r"429", r"503", r"quota", r"overloaded",
        r"api.?key", r"auth.?fail", r"stream.?interrupt", r"provider.?fail",
        r"model.?unavail", r"token.?limit", r"context.?length",
    ]),
    (FailureClass.NETWORK, [
        r"connect(ion)?.?(drop|refused|timed?.{0,2}out|reset)", r"dns.?fail",
        r"timed?.{0,2}out", r"socket", r"ssl.?error", r"eof",
    ]),
    (FailureClass.TOOL, [
        r"tool.?(call|fail|error|malform)", r"permission.?denied",
        r"json.?(decode|parse)", r"schema.?invalid", r"unknown.?tool",
        r"file.?not.?found", r"no.?such.?file",
    ]),
    (FailureClass.PLANNER, [
        r"cycle.?detect", r"no.?module", r"zero.?agent",
        r"contra(dict|ry)", r"plan.?fail", r"planner.?error",
        r"decompos", r"bad.?plan",
    ]),
    (FailureClass.WORKER, [
        r"max.?turn", r"AGENT_COMPLETE.?missing", r"worker.?stall",
        r"blocked.?by", r"waiting.?dependency", r"worker.?fail",
        r"incomplete.?work", r"output.?too.?short",
    ]),
    (FailureClass.VALIDATION, [
        r"contract.?fail", r"deliverable.?miss", r"test.?fail",
        r"artifact.?not.?found", r"check.?fail", r"valida",
        r"criterion.?not.?met",
    ]),
    (FailureClass.RECOVERY, [
        r"escalat", r"max.?attempt", r"recovery.?fail",
        r"replan.?fail", r"rework.?reject",
    ]),
]

_COMPILED = [(cls, [re.compile(p, re.IGNORECASE) for p in pats]) for cls, pats in _PATTERNS]


def classify_failure(error_text: str, context: str = "") -> FailureClass:
    """Classify a failure from its error text. Returns the first matching class."""
    combined = (error_text or "") + " " + (context or "")
    for cls, patterns in _COMPILED:
        if any(pat.search(combined) for pat in patterns):
            return cls
    return FailureClass.UNKNOWN


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class FailureEvent:
    ts: float
    mission_id: str
    failure_class: FailureClass
    error_text: str
    milestone_name: str = ""
    module_name: str = ""
    provider: str = ""
    blocked_deliverables: int = 0
    recovered: bool = False

    @property
    def age_s(self) -> float:
        return time.monotonic() - self.ts


@dataclass
class FailureCluster:
    failure_class: FailureClass
    events: List[FailureEvent] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.events)

    @property
    def recovery_rate(self) -> float:
        if not self.events:
            return 0.0
        return sum(1 for e in self.events if e.recovered) / len(self.events)

    @property
    def total_deliverables_blocked(self) -> int:
        return sum(e.blocked_deliverables for e in self.events)

    @property
    def most_recent_ts(self) -> float:
        return max(e.ts for e in self.events) if self.events else 0.0

    def impact_score(self) -> float:
        """Higher = more critical. Weights: count × blocked_deliverables × (1 - recovery_rate)."""
        return self.count * (self.total_deliverables_blocked + 1) * (1 - self.recovery_rate)


@dataclass
class MitigationSuggestion:
    failure_class: FailureClass
    suggestion: str
    action: str = ""


_MITIGATIONS: Dict[FailureClass, MitigationSuggestion] = {
    FailureClass.PROVIDER: MitigationSuggestion(
        FailureClass.PROVIDER,
        "Enable provider rotation (KRYTH_PROVIDER_ROTATION=1) and add retry backoff.",
        "Add a fallback provider in model config; set KRYTH_MAX_RETRIES=3.",
    ),
    FailureClass.NETWORK: MitigationSuggestion(
        FailureClass.NETWORK,
        "Increase timeout budget and enable exponential backoff.",
        "Set KRYTH_TIMEOUT_S=120; check network stability.",
    ),
    FailureClass.TOOL: MitigationSuggestion(
        FailureClass.TOOL,
        "Validate tool schemas before injection; add permission pre-checks.",
        "Run tool_scheduler.validate() before each agent launch.",
    ),
    FailureClass.PLANNER: MitigationSuggestion(
        FailureClass.PLANNER,
        "Planner produced an invalid plan. Enable plan validation gate.",
        "Set KRYTH_PLAN_VALIDATION=1; add cycle detection as a pre-check.",
    ),
    FailureClass.WORKER: MitigationSuggestion(
        FailureClass.WORKER,
        "Worker stalled or hit max_turns. Increase per-worker turn budget.",
        "Set max_turns_per_agent=120; add mid-mission turn-count monitoring.",
    ),
    FailureClass.VALIDATION: MitigationSuggestion(
        FailureClass.VALIDATION,
        "Contract validation failed. Review success criteria clarity.",
        "Tighten success criteria; enable artifact_validator.py for file existence checks.",
    ),
    FailureClass.RECOVERY: MitigationSuggestion(
        FailureClass.RECOVERY,
        "Recovery itself exhausted its budget. Increase MAX_ATTEMPTS_PER_TARGET.",
        "Review autonomous_recovery.py MAX_ATTEMPTS_PER_TARGET (currently 3).",
    ),
    FailureClass.UNKNOWN: MitigationSuggestion(
        FailureClass.UNKNOWN,
        "Unknown failure — check raw error logs.",
        "Enable KRYTH_DEBUG=1 for verbose failure logging.",
    ),
}


@dataclass
class RootCauseReport:
    total_failures: int
    clusters: List[FailureCluster]
    top_modes: List[FailureClass]
    mitigations: List[MitigationSuggestion]
    generated_at: float = field(default_factory=time.monotonic)

    def summary(self) -> str:
        if not self.total_failures:
            return "Failure Intelligence: no failures recorded."
        lines = [
            f"Failure Intelligence: {self.total_failures} total failures",
            f"  Top failure modes:",
        ]
        for cls in self.clusters[:5]:
            m = _MITIGATIONS.get(cls.failure_class)
            lines.append(
                f"  [{cls.failure_class.value.upper():<12}] "
                f"{cls.count} events  "
                f"{cls.recovery_rate:.0%} recovered  "
                f"{cls.total_deliverables_blocked} deliverables blocked"
            )
            if m:
                lines.append(f"    → {m.suggestion}")
        return "\n".join(lines)


# ── Intelligence engine ────────────────────────────────────────────────────────

class FailureIntelligenceEngine:
    """Records failure events and generates root-cause reports.

    Purely additive — reads from AutonomousRecoveryEngine history and
    MilestoneResult data; never modifies any execution path.
    """

    def __init__(self) -> None:
        self._events: List[FailureEvent] = []

    def record(
        self,
        error_text: str,
        mission_id: str = "",
        milestone_name: str = "",
        module_name: str = "",
        provider: str = "",
        blocked_deliverables: int = 0,
        recovered: bool = False,
        context: str = "",
    ) -> FailureEvent:
        cls = classify_failure(error_text, context)
        event = FailureEvent(
            ts=time.monotonic(),
            mission_id=mission_id,
            failure_class=cls,
            error_text=error_text[:500],
            milestone_name=milestone_name,
            module_name=module_name,
            provider=provider,
            blocked_deliverables=blocked_deliverables,
            recovered=recovered,
        )
        self._events.append(event)
        return event

    def ingest_mission_result(self, mission_result, mission_id: str = "") -> None:
        """Ingest failures from a MissionDeliveryResult automatically."""
        mid = mission_id or getattr(mission_result, "project_name", "unknown")
        for ms in getattr(mission_result, "milestones", []):
            if not getattr(ms, "success", True):
                self.record(
                    error_text=getattr(ms, "rework_notes", "") or "milestone failed",
                    mission_id=mid,
                    milestone_name=getattr(ms, "milestone_name", ""),
                    blocked_deliverables=getattr(ms, "deliverables_planned", 0),
                    recovered=False,
                )
            for mod, tl_result in getattr(ms, "team_lead_reviews", {}).items():
                if not getattr(tl_result, "approved", True):
                    self.record(
                        error_text=getattr(tl_result, "notes", "") or "team lead rejected",
                        mission_id=mid,
                        milestone_name=getattr(ms, "milestone_name", ""),
                        module_name=mod,
                        recovered=False,
                    )

    def ingest_recovery_history(self, recovery_engine, mission_id: str = "") -> None:
        """Ingest from AutonomousRecoveryEngine.history()."""
        for outcome in getattr(recovery_engine, "_history", []):
            decision = getattr(outcome, "decision", None)
            if decision is None:
                continue
            self.record(
                error_text=getattr(decision, "notes", "") or decision.trigger.value,
                mission_id=mission_id,
                milestone_name=getattr(decision, "target", ""),
                recovered=getattr(outcome, "success", False),
            )

    def generate_report(self) -> RootCauseReport:
        if not self._events:
            return RootCauseReport(
                total_failures=0, clusters=[], top_modes=[], mitigations=[],
            )

        # Group by failure class
        by_class: Dict[FailureClass, List[FailureEvent]] = defaultdict(list)
        for e in self._events:
            by_class[e.failure_class].append(e)

        clusters = [
            FailureCluster(failure_class=cls, events=evts)
            for cls, evts in by_class.items()
        ]
        clusters.sort(key=lambda c: c.impact_score(), reverse=True)

        top_modes = [c.failure_class for c in clusters[:5]]
        mitigations = [_MITIGATIONS[cls] for cls in top_modes if cls in _MITIGATIONS]

        return RootCauseReport(
            total_failures=len(self._events),
            clusters=clusters,
            top_modes=top_modes,
            mitigations=mitigations,
        )

    def clear(self) -> None:
        self._events.clear()

    @property
    def event_count(self) -> int:
        return len(self._events)

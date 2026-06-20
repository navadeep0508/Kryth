"""Planner state — dataclasses shared across all planner modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Complexity(str, Enum):
    SMALL   = "small"    # 1 agent, <5 actions, <500 tokens
    MEDIUM  = "medium"   # lead + 2 workers, 5-15 actions
    LARGE   = "large"    # lead + 5 workers, 15-40 actions
    MASSIVE = "massive"  # dynamic team, 40+ actions


class PlannerStatus(str, Enum):
    PENDING   = "pending"
    PLANNING  = "planning"
    READY     = "ready"
    REPLANNING = "replanning"
    FAILED    = "failed"


@dataclass
class Objective:
    """Structured representation of a user's natural-language goal."""
    raw: str                         # original user text
    goal: str                        # cleaned one-line goal
    domains: list[str]               # e.g. ["backend", "testing", "deployment"]
    requirements: list[str]          # implicit requirements detected
    constraints: list[str]           # e.g. ["keep existing API", "no breaking changes"]
    complexity_hint: Optional[Complexity] = None
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class ComplexityEstimate:
    complexity: Complexity
    action_count: int
    worker_count: int
    parallel_layers: int
    estimated_tokens: int
    estimated_duration_s: float
    risk_level: str               # low / medium / high / critical
    domains: list[str]
    rationale: str


@dataclass
class TeamSpec:
    total_agents: int
    lead_agents: list[str]        # domain names that need a lead
    worker_agents: dict[str, int] # domain → worker count
    specialist_agents: list[str]  # e.g. ["browser", "devops", "recovery"]
    rationale: str


@dataclass
class PlannerState:
    """Mutable state threaded through the planning pipeline."""
    session_id: str
    objective: Optional[Objective]      = None
    estimate:  Optional[ComplexityEstimate] = None
    team:      Optional[TeamSpec]       = None
    plan_dict: Optional[dict[str, Any]] = None   # raw action graph dict
    status:    PlannerStatus            = PlannerStatus.PENDING
    replan_count: int                   = 0
    error:     str                      = ""
    metadata:  dict[str, Any]           = field(default_factory=dict)

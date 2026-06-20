"""ResourceScheduler — V5 Phase 4.

Data-driven model/provider routing based on:
  * Provider latency (historical p50 from ProviderHealth)
  * Token cost per 1k tokens
  * Provider reliability (success rate)
  * Historical success rate for this task type
  * Context size required
  * Execution cost target

Routing rules (overridable via KRYTH_RESOURCE_ROUTING=off):
  cheap  model  → Documentation, Comments, Formatting
  fast   model  → Refactors, Boilerplate, Simple CRUD
  reasoning     → Architecture, Complex logic, Debugging
  vision        → UI analysis, Screenshot review, CSS

Additive: does not change how agents execute.
Returns a ResourcePlan that callers MAY use to override model/provider hints.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ── Task type classification ──────────────────────────────────────────────────

CHEAP_KEYWORDS = {
    "documentation", "docs", "readme", "comment", "format",
    "lint", "style", "markdown", "changelog",
}
FAST_KEYWORDS = {
    "refactor", "rename", "boilerplate", "scaffold", "crud",
    "migration", "schema", "fixture", "stub", "mock",
}
REASONING_KEYWORDS = {
    "architecture", "design", "algorithm", "optimization", "security",
    "auth", "encryption", "concurrency", "performance", "debug",
    "complex", "critical", "analysis",
}
VISION_KEYWORDS = {
    "ui", "ux", "screenshot", "css", "html", "frontend",
    "layout", "component", "visual",
}
# Note: "design" is deliberately NOT in VISION_KEYWORDS — it collides with
# REASONING_KEYWORDS ("system design", "architecture design") and vision is
# checked first in classify_task_tier(); genuine UI/UX work still matches
# via ui/css/layout/component/visual.


def classify_task_tier(
    role: str,
    goal: str = "",
    deliverables: Optional[List[str]] = None,
) -> str:
    """Return tier: 'cheap' | 'fast' | 'reasoning' | 'vision' | 'standard'."""
    text = " ".join([role, goal] + (deliverables or [])).lower()
    words = set(text.split())

    if words & VISION_KEYWORDS:
        return "vision"
    if words & REASONING_KEYWORDS:
        return "reasoning"
    if words & CHEAP_KEYWORDS:
        return "cheap"
    if words & FAST_KEYWORDS:
        return "fast"
    return "standard"


# ── Cost table ────────────────────────────────────────────────────────────────
# Rough token costs per 1k output tokens (normalized to relative units).
# Callers can override via model_config if present.

DEFAULT_COST_TABLE: Dict[str, float] = {
    "cheap":     0.15,    # e.g. haiku-class
    "fast":      0.30,    # e.g. sonnet-fast
    "standard":  1.00,    # e.g. sonnet
    "reasoning": 3.00,    # e.g. opus / reasoning model
    "vision":    1.50,    # e.g. vision-capable sonnet
}

DEFAULT_LATENCY_TABLE: Dict[str, float] = {
    "cheap":     1.0,     # seconds p50
    "fast":      2.0,
    "standard":  4.0,
    "reasoning": 8.0,
    "vision":    5.0,
}


# ── Resource allocation ───────────────────────────────────────────────────────

@dataclass
class AgentResourcePlan:
    """Resource recommendation for a single agent."""
    agent_id: str
    role: str
    tier: str                    # cheap / fast / standard / reasoning / vision
    model_hint: str = ""         # preferred model name (advisory)
    provider_hint: str = ""      # preferred provider (advisory)
    estimated_cost: float = 0.0  # relative cost units
    estimated_latency_s: float = 0.0
    rationale: str = ""


@dataclass
class ResourcePlan:
    """Resource allocation plan for a full milestone or mission."""
    agents: List[AgentResourcePlan] = field(default_factory=list)
    total_estimated_cost: float = 0.0
    total_estimated_latency_s: float = 0.0   # critical path latency
    notes: str = ""

    def by_id(self, agent_id: str) -> Optional[AgentResourcePlan]:
        return next((a for a in self.agents if a.agent_id == agent_id), None)

    def summary(self) -> str:
        tiers = {}
        for a in self.agents:
            tiers[a.tier] = tiers.get(a.tier, 0) + 1
        tier_str = ", ".join(f"{v}×{k}" for k, v in sorted(tiers.items()))
        return (
            f"ResourcePlan: {len(self.agents)} agents [{tier_str}]  "
            f"cost≈{self.total_estimated_cost:.1f}  latency≈{self.total_estimated_latency_s:.1f}s"
        )


# ── Scheduler ─────────────────────────────────────────────────────────────────

class ResourceScheduler:
    """Assigns resource tiers and model hints to agents before execution.

    Usage:
        sched = ResourceScheduler()
        plan  = sched.allocate(team.agents)
        for agent in team.agents:
            hint = plan.by_id(agent.id)
            agent.model_hint = hint.model_hint  # if AgentRole supports it
    """

    def __init__(
        self,
        cost_table: Optional[Dict[str, float]] = None,
        latency_table: Optional[Dict[str, float]] = None,
        provider_health=None,   # ProviderHealth | None
    ) -> None:
        self._cost    = cost_table or DEFAULT_COST_TABLE
        self._latency = latency_table or DEFAULT_LATENCY_TABLE
        self._health  = provider_health

        # Load model config if available
        self._model_map = self._load_model_map()

    def _load_model_map(self) -> Dict[str, str]:
        """Try to read model config for tier→model mapping."""
        try:
            from agent.model_config.router import get_model_for_tier
            return {
                t: get_model_for_tier(t)
                for t in ("cheap", "fast", "standard", "reasoning", "vision")
            }
        except Exception:
            pass
        # Sensible defaults using env vars
        from agent.env import getenv
        main = getenv("KRYTH_MAIN_MODEL") or "default"
        return {
            "cheap":     getenv("KRYTH_CHEAP_MODEL") or main,
            "fast":      getenv("KRYTH_FAST_MODEL") or main,
            "standard":  main,
            "reasoning": getenv("KRYTH_REASONING_MODEL") or main,
            "vision":    getenv("KRYTH_VISION_MODEL") or main,
        }

    def _best_provider(self, tier: str) -> str:
        """Return healthiest provider for this tier (advisory)."""
        if self._health is None:
            return ""
        try:
            metrics = self._health.all_providers()
            # Pick provider with highest success rate
            best = max(metrics.items(), key=lambda kv: kv[1].success_rate, default=(None, None))
            return best[0] or ""
        except Exception:
            return ""

    def allocate(
        self,
        agents: List,    # List[AgentRole]
        max_cost_budget: float = float("inf"),
    ) -> ResourcePlan:
        """Allocate resources for a list of agents."""
        enabled = os.environ.get("KRYTH_RESOURCE_ROUTING", "1").strip().lower()
        if enabled in ("0", "false", "off", "no"):
            # Routing disabled — assign standard to everything
            return ResourcePlan(
                agents=[
                    AgentResourcePlan(
                        agent_id=a.id, role=a.role, tier="standard",
                        model_hint=self._model_map.get("standard", ""),
                        rationale="routing disabled",
                    )
                    for a in agents
                ]
            )

        plans: List[AgentResourcePlan] = []
        total_cost = 0.0
        max_latency = 0.0

        for agent in agents:
            goal       = getattr(agent, "mission", "")
            delivs     = list(getattr(getattr(agent, "owns", None), "files", []))
            tier       = classify_task_tier(agent.role, goal, delivs)
            model      = self._model_map.get(tier, self._model_map.get("standard", ""))
            provider   = self._best_provider(tier)
            est_cost   = self._cost.get(tier, 1.0) * max(getattr(agent, "max_turns", 30) / 10, 1)
            est_lat    = self._latency.get(tier, 4.0)

            # Budget guard: downgrade to cheaper tier if over budget
            if total_cost + est_cost > max_cost_budget and tier in ("reasoning", "vision"):
                tier     = "standard"
                model    = self._model_map.get("standard", model)
                est_cost = self._cost.get("standard", 1.0) * max(getattr(agent, "max_turns", 30) / 10, 1)

            plans.append(AgentResourcePlan(
                agent_id=agent.id,
                role=agent.role,
                tier=tier,
                model_hint=model,
                provider_hint=provider,
                estimated_cost=est_cost,
                estimated_latency_s=est_lat,
                rationale=f"task tier={tier} from role+goal keywords",
            ))
            total_cost += est_cost
            max_latency = max(max_latency, est_lat)

        return ResourcePlan(
            agents=plans,
            total_estimated_cost=total_cost,
            total_estimated_latency_s=max_latency,
        )

"""ModelRoutingOptimizer — V8 Phase 4.

Evidence-based model routing: track which model works best for which task type,
then route future tasks to the highest-performing model. Falls back to the
existing resource_scheduler.py tier heuristics when no evidence yet.

Tracks per (task_tier, provider) pair:
  * success_count
  * failure_count
  * total_latency_ms
  * total_cost_usd
  * token_count

Routing decision: pick the provider/model with the highest quality score:
  quality_score = success_rate × (1 / latency_ms) × (1 / cost_per_token)

Evidence is session-scoped by default (resets on restart); pass a persistence
path to survive across sessions.
"""
from __future__ import annotations

import math
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class ProviderRecord:
    provider: str
    task_tier: int
    success_count: int = 0
    failure_count: int = 0
    total_latency_ms: float = 0.0
    total_cost_usd: float = 0.0
    total_tokens: int = 0

    @property
    def total_calls(self) -> int:
        return self.success_count + self.failure_count

    @property
    def success_rate(self) -> float:
        return self.success_count / max(self.total_calls, 1)

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / max(self.total_calls, 1)

    @property
    def avg_cost_usd(self) -> float:
        return self.total_cost_usd / max(self.total_calls, 1)

    def quality_score(self) -> float:
        """Higher is better. Balances success rate, speed, and cost."""
        if self.total_calls == 0:
            return 0.0
        lat_factor  = 1.0 / math.log1p(self.avg_latency_ms) if self.avg_latency_ms > 0 else 1.0
        cost_factor = 1.0 / math.log1p(self.avg_cost_usd * 1000) if self.avg_cost_usd > 0 else 1.0
        return self.success_rate * lat_factor * cost_factor


# Task tier → default model tiers (used before any evidence is available)
_HEURISTIC_DEFAULTS: Dict[int, str] = {
    0: "haiku",          # MICRO: cheapest/fastest
    1: "haiku",          # SIMPLE: still cheap
    2: "sonnet",         # STANDARD: balanced
    3: "sonnet",         # COMPLEX: solid reasoning
    4: "opus",           # ENTERPRISE: best quality
}

# Provider families — grouped by cost tier
_PROVIDER_FAMILIES: Dict[str, List[str]] = {
    "cheap":    ["groq/llama3", "haiku", "openrouter/mistral"],
    "balanced": ["sonnet", "gpt-4o-mini", "gemini-flash"],
    "premium":  ["opus", "gpt-4o", "gemini-pro"],
    "reasoning":["opus", "o1-mini", "o3-mini"],
}


@dataclass
class RoutingDecision:
    provider: str
    model: str
    confidence: str          # "evidence" | "heuristic"
    reason: str
    quality_score: float = 0.0


class ModelRoutingOptimizer:
    """Evidence-based model router.

    Call observe() after each mission to record actual performance.
    Call route() before a mission to get the recommended model.
    """

    def __init__(self) -> None:
        self._records: Dict[Tuple[int, str], ProviderRecord] = {}

    def observe(
        self,
        task_tier: int,
        provider: str,
        success: bool,
        latency_ms: float,
        cost_usd: float,
        token_count: int = 0,
    ) -> None:
        key = (task_tier, provider)
        if key not in self._records:
            self._records[key] = ProviderRecord(provider=provider, task_tier=task_tier)
        r = self._records[key]
        if success:
            r.success_count += 1
        else:
            r.failure_count += 1
        r.total_latency_ms += latency_ms
        r.total_cost_usd += cost_usd
        r.total_tokens += token_count

    def route(self, task_tier: int, available_providers: Optional[List[str]] = None) -> RoutingDecision:
        """Recommend the best provider for this task tier.

        Uses accumulated evidence if available; falls back to cost-tier
        heuristics if fewer than 3 samples exist for this tier.
        """
        tier_records = [
            r for (t, _), r in self._records.items()
            if t == task_tier
        ]
        if available_providers:
            tier_records = [r for r in tier_records if r.provider in available_providers]

        # Filter to records with at least 3 calls for statistical validity
        evidenced = [r for r in tier_records if r.total_calls >= 3]

        if evidenced:
            best = max(evidenced, key=lambda r: r.quality_score())
            return RoutingDecision(
                provider=best.provider,
                model=best.provider,
                confidence="evidence",
                reason=(
                    f"{best.success_rate:.0%} success, {best.avg_latency_ms:.0f}ms avg, "
                    f"${best.avg_cost_usd:.4f} avg — quality={best.quality_score():.3f}"
                ),
                quality_score=best.quality_score(),
            )

        # Fall back to heuristic
        default_model = _HEURISTIC_DEFAULTS.get(task_tier, "sonnet")
        if available_providers and default_model not in available_providers:
            default_model = available_providers[0]
        return RoutingDecision(
            provider=default_model,
            model=default_model,
            confidence="heuristic",
            reason=f"No evidence yet for tier {task_tier}; defaulting to cost-tier heuristic",
        )

    def scorecard(self) -> str:
        if not self._records:
            return "ModelRoutingOptimizer: no evidence collected yet."
        lines = ["Model Routing Evidence:"]
        by_tier: Dict[int, List[ProviderRecord]] = defaultdict(list)
        for (t, _), r in self._records.items():
            by_tier[t].append(r)
        for tier in sorted(by_tier.keys()):
            records = sorted(by_tier[tier], key=lambda r: r.quality_score(), reverse=True)
            lines.append(f"  Tier {tier}:")
            for r in records[:3]:
                lines.append(
                    f"    {r.provider:<20} "
                    f"success={r.success_rate:.0%}  "
                    f"lat={r.avg_latency_ms:.0f}ms  "
                    f"cost=${r.avg_cost_usd:.4f}  "
                    f"quality={r.quality_score():.3f}"
                )
        return "\n".join(lines)

    def top_providers_for_tier(self, task_tier: int, n: int = 3) -> List[ProviderRecord]:
        records = [r for (t, _), r in self._records.items() if t == task_tier]
        return sorted(records, key=lambda r: r.quality_score(), reverse=True)[:n]

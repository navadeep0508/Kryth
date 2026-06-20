"""TokenLedger — V6 Phase 3.

Aggregates REAL token usage from WorkerStats (already captured at
scheduler.py:389-390 from actual `session.cumulative_in_tokens/out_tokens`
after each real LLM call). No proxies, no estimates — these are real tokens.

Tracks per:
  * Worker (agent_id → tokens_in, tokens_out)
  * Milestone (milestone_name → tokens)
  * Provider (provider → tokens)
  * Mission (total)

Reports:
  * Top token consumers (wasteful workers)
  * Cost by tier (using provider-specific cost table)
  * Prompt vs completion split (where most tokens go)
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# Cost table: USD per 1k tokens (prompt / completion).
# Pulled from actual provider pricing — update as pricing changes.
DEFAULT_COST_TABLE: Dict[str, Tuple[float, float]] = {
    "default":    (0.003, 0.015),   # fallback
    "haiku":      (0.00025, 0.00125),
    "sonnet":     (0.003, 0.015),
    "opus":       (0.015, 0.075),
    "groq":       (0.00027, 0.0003),
    "openrouter": (0.003, 0.015),
}


@dataclass
class TokenRecord:
    worker_id: str
    role: str
    milestone: str = ""
    provider: str = "default"
    tokens_in: int = 0
    tokens_out: int = 0

    @property
    def total(self) -> int:
        return self.tokens_in + self.tokens_out

    def cost_usd(self, cost_table: Dict[str, Tuple[float, float]] = DEFAULT_COST_TABLE) -> float:
        p_rate, c_rate = cost_table.get(self.provider, cost_table["default"])
        return (self.tokens_in / 1000 * p_rate) + (self.tokens_out / 1000 * c_rate)


@dataclass
class TokenSummary:
    total_in: int = 0
    total_out: int = 0
    total_cost_usd: float = 0.0
    by_milestone: Dict[str, int] = field(default_factory=dict)
    by_provider: Dict[str, int] = field(default_factory=dict)
    top_consumers: List[TokenRecord] = field(default_factory=list)
    waste_signals: List[str] = field(default_factory=list)   # identified inefficiencies

    @property
    def total_tokens(self) -> int:
        return self.total_in + self.total_out

    def summary(self) -> str:
        lines = [
            f"Token Ledger: {self.total_in:,} prompt + {self.total_out:,} completion = {self.total_tokens:,} total",
            f"  Estimated cost: ${self.total_cost_usd:.4f} USD",
        ]
        if self.by_milestone:
            top_ms = sorted(self.by_milestone.items(), key=lambda x: x[1], reverse=True)[:3]
            lines.append("  Top milestones: " + ", ".join(f"{n}={t:,}" for n, t in top_ms))
        if self.top_consumers:
            lines.append("  Top consumers: " + ", ".join(
                f"{r.role}={r.total:,}" for r in self.top_consumers[:3]
            ))
        if self.waste_signals:
            lines.append("  Waste: " + "; ".join(self.waste_signals[:3]))
        return "\n".join(lines)


class TokenLedger:
    """Thread-safe real-token aggregator.

    Fed from WorkerResult.stats.tokens_in/out which come directly from the
    LLM's usage object (no proxy). Call record_worker() from v5_runtime's
    _on_milestone_done callback.
    """

    # Workers that produce less than this fraction of the mission's average
    # token output but consume above-average tokens are flagged as wasteful.
    _WASTE_THRESHOLD = 0.15

    def __init__(
        self,
        cost_table: Optional[Dict[str, Tuple[float, float]]] = None,
    ) -> None:
        self._cost_table = cost_table or DEFAULT_COST_TABLE
        self._records: List[TokenRecord] = []
        self._lock = threading.Lock()

    def record_worker(
        self,
        worker_id: str,
        role: str,
        tokens_in: int,
        tokens_out: int,
        provider: str = "default",
        milestone: str = "",
    ) -> None:
        rec = TokenRecord(
            worker_id=worker_id, role=role, milestone=milestone,
            provider=provider, tokens_in=tokens_in, tokens_out=tokens_out,
        )
        with self._lock:
            self._records.append(rec)

    def record_from_milestone_result(self, ms_result, milestone_name: str = "") -> None:
        """Ingest real tokens from a MilestoneResult's scheduler outputs."""
        name = milestone_name or getattr(ms_result, "milestone_name", "")
        # MilestoneResult doesn't directly hold WorkerStats — they live in
        # SchedulerResult.worker_stats, not exposed here.
        # Fallback: if worker_outputs is present and has token hints, use them.
        # (WorkerStats capture happens in scheduler._run_single_agent before
        # returning WorkerResult; MilestoneEngine discards them today. V6
        # wires the ledger at the scheduler callback level instead.)
        pass  # See record_worker() — called from the scheduler stats hook.

    def record_from_scheduler_result(self, sched_result, milestone_name: str = "") -> None:
        """Ingest from SchedulerResult.worker_stats (the richer source)."""
        stats = getattr(sched_result, "worker_stats", {})
        for wid, ws in stats.items():
            self.record_worker(
                worker_id=wid,
                role=getattr(ws, "role", wid),
                tokens_in=getattr(ws, "tokens_in", 0),
                tokens_out=getattr(ws, "tokens_out", 0),
                milestone=milestone_name,
            )

    def summarize(self, top_n: int = 5) -> TokenSummary:
        with self._lock:
            records = list(self._records)

        total_in  = sum(r.tokens_in for r in records)
        total_out = sum(r.tokens_out for r in records)
        total_cost = sum(r.cost_usd(self._cost_table) for r in records)

        by_ms: Dict[str, int] = {}
        by_prov: Dict[str, int] = {}
        for r in records:
            by_ms[r.milestone] = by_ms.get(r.milestone, 0) + r.total
            by_prov[r.provider] = by_prov.get(r.provider, 0) + r.total

        top = sorted(records, key=lambda r: r.total, reverse=True)[:top_n]

        # Identify waste: high-token workers with unusually short outputs
        waste: List[str] = []
        if records:
            avg_out = total_out / len(records) if records else 0
            for r in records:
                if r.tokens_in > avg_out * 3 and r.tokens_out < avg_out * 0.2:
                    waste.append(
                        f"{r.role} consumed {r.tokens_in:,} prompt tokens "
                        f"but produced only {r.tokens_out:,} completion tokens"
                    )

        return TokenSummary(
            total_in=total_in,
            total_out=total_out,
            total_cost_usd=total_cost,
            by_milestone=by_ms,
            by_provider=by_prov,
            top_consumers=top,
            waste_signals=waste,
        )

    def mission_total(self) -> Tuple[int, int]:
        """(total_prompt_tokens, total_completion_tokens) from real calls."""
        with self._lock:
            return (
                sum(r.tokens_in for r in self._records),
                sum(r.tokens_out for r in self._records),
            )

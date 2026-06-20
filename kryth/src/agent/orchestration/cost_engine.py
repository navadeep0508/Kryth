"""MissionCostEngine — V6 Phase 4.

Budget-aware execution: track actual spend vs projected spend vs remaining
budget, and trigger adaptive downgrades when the budget is threatened.

Budget can be expressed as:
  * USD amount     (budget_usd)
  * Token count    (budget_tokens)
  * Currency other (budget_other, with usd_per_unit conversion)

When the budget is exceeded or close to exceeded, the engine emits a
DowngradeAction that the caller can use to reduce cost:

  REDUCE_WORKERS    — cut max_workers to reduce parallelism cost
  DOWNGRADE_MODEL   — switch to a cheaper model tier
  DISABLE_LAYERS    — disable expensive V5 layers (digital twin, quality)
  SWITCH_PROVIDER   — switch to a lower-cost provider
  REDUCE_RETRIES    — cut max_retries to 0
  ABORT             — mission is over budget, abort cleanly

Thresholds (configurable):
  warn_pct   = 0.75  (75% spent → warn)
  downgrade_pct = 0.90 (90% spent → downgrade)
  abort_pct  = 1.05   (5% over → abort)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from agent.orchestration.token_ledger import DEFAULT_COST_TABLE


class DowngradeAction(Enum):
    NONE             = "none"
    REDUCE_WORKERS   = "reduce_workers"
    DOWNGRADE_MODEL  = "downgrade_model"
    DISABLE_LAYERS   = "disable_layers"
    SWITCH_PROVIDER  = "switch_provider"
    REDUCE_RETRIES   = "reduce_retries"
    ABORT            = "abort"


@dataclass
class BudgetStatus:
    budget_usd: Optional[float]
    spent_usd: float
    projected_usd: float
    remaining_usd: float
    pct_used: float
    action: DowngradeAction
    notes: str = ""

    @property
    def over_budget(self) -> bool:
        return self.pct_used >= 1.0

    def summary(self) -> str:
        if self.budget_usd is None:
            return f"Cost: ${self.spent_usd:.4f} (no budget set)"
        return (
            f"Budget: ${self.spent_usd:.4f}/${self.budget_usd:.4f} "
            f"({self.pct_used:.0%})  "
            f"remaining=${self.remaining_usd:.4f}  "
            f"projected=${self.projected_usd:.4f}  "
            f"action={self.action.value}"
        )


class MissionCostEngine:
    """Track actual spend and trigger adaptive downgrades.

    Feed real tokens via `record_spend()` from the TokenLedger or directly
    from WorkerStats. The engine projects total mission cost from velocity
    (tokens/s so far) and remaining estimated work.
    """

    def __init__(
        self,
        budget_usd: Optional[float] = None,
        budget_tokens: Optional[int] = None,
        warn_pct: float = 0.75,
        downgrade_pct: float = 0.90,
        abort_pct: float = 1.05,
        cost_table=None,
        provider: str = "default",
    ) -> None:
        self._budget_usd    = budget_usd
        self._budget_tokens = budget_tokens
        self._warn_pct      = warn_pct
        self._downgrade_pct = downgrade_pct
        self._abort_pct     = abort_pct
        self._cost_table    = cost_table or DEFAULT_COST_TABLE
        self._provider      = provider
        self._spent_in  = 0
        self._spent_out = 0
        self._events: List[str] = []
        self._start     = time.monotonic()

    def _usd_for(self, tokens_in: int, tokens_out: int, provider: str = "") -> float:
        prov = provider or self._provider
        p_rate, c_rate = self._cost_table.get(prov, self._cost_table["default"])
        return (tokens_in / 1000 * p_rate) + (tokens_out / 1000 * c_rate)

    @property
    def _effective_budget_usd(self) -> Optional[float]:
        if self._budget_usd is not None:
            return self._budget_usd
        if self._budget_tokens is not None:
            # Convert token budget to USD using default rates
            p_rate, c_rate = self._cost_table.get(self._provider, self._cost_table["default"])
            avg_rate = (p_rate + c_rate) / 2
            return self._budget_tokens / 1000 * avg_rate
        return None

    def record_spend(
        self,
        tokens_in: int,
        tokens_out: int,
        provider: str = "",
        note: str = "",
    ) -> None:
        self._spent_in  += tokens_in
        self._spent_out += tokens_out
        if note:
            self._events.append(note)

    def status(
        self,
        milestones_done: int = 0,
        milestones_total: int = 1,
    ) -> BudgetStatus:
        """Compute current budget status and recommended action."""
        spent_usd   = self._usd_for(self._spent_in, self._spent_out)
        budget_usd  = self._effective_budget_usd

        if budget_usd is None:
            return BudgetStatus(
                budget_usd=None, spent_usd=spent_usd, projected_usd=spent_usd,
                remaining_usd=float("inf"), pct_used=0.0, action=DowngradeAction.NONE,
            )

        # Project total cost from velocity so far
        elapsed = max(time.monotonic() - self._start, 0.001)
        total_tokens = self._spent_in + self._spent_out
        if milestones_done > 0 and milestones_total > milestones_done:
            progress = milestones_done / milestones_total
            projected_usd = spent_usd / progress if progress > 0 else spent_usd * 2
        else:
            projected_usd = spent_usd

        remaining_usd = max(0.0, budget_usd - spent_usd)
        pct_used = spent_usd / budget_usd if budget_usd > 0 else 0.0

        # Decide action
        if pct_used >= self._abort_pct:
            action = DowngradeAction.ABORT
            notes  = f"Budget exceeded by {(pct_used-1)*100:.0f}%"
        elif pct_used >= self._downgrade_pct:
            # Escalating downgrades based on severity
            if pct_used >= 0.98:
                action = DowngradeAction.DISABLE_LAYERS
                notes  = "Disabling expensive intelligence layers to stay in budget"
            elif pct_used >= 0.95:
                action = DowngradeAction.DOWNGRADE_MODEL
                notes  = "Switching to cheaper model tier"
            elif pct_used >= 0.92:
                action = DowngradeAction.REDUCE_WORKERS
                notes  = "Reducing parallel workers to cut cost"
            else:
                action = DowngradeAction.REDUCE_RETRIES
                notes  = "Disabling retries to cut cost"
        elif pct_used >= self._warn_pct:
            action = DowngradeAction.NONE
            notes  = f"Budget warning: {pct_used:.0%} spent"
        else:
            action = DowngradeAction.NONE
            notes  = ""

        return BudgetStatus(
            budget_usd=budget_usd, spent_usd=spent_usd,
            projected_usd=projected_usd, remaining_usd=remaining_usd,
            pct_used=pct_used, action=action, notes=notes,
        )

    @property
    def total_spent_usd(self) -> float:
        return self._usd_for(self._spent_in, self._spent_out)

    @property
    def total_tokens(self) -> int:
        return self._spent_in + self._spent_out

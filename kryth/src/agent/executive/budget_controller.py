"""Budget Controller — tracks token / time / retry spend; halts runaway missions."""

from __future__ import annotations

import logging
from agent.executive.executive_state import BudgetState, ExecutiveDecision, ExecutiveState

logger = logging.getLogger(__name__)

_BUDGET_WARN_PCT    = 75.0   # warn at 75% token budget
_BUDGET_HALT_PCT    = 95.0   # pause at 95%
_MAX_API_RETRIES    = 20
_MAX_LLM_CALLS      = 200


class BudgetController:
    """Monitors resource spend and decides whether to continue or halt."""

    def assess(self, state: ExecutiveState) -> ExecutiveDecision:
        b = state.budget

        if b.over_budget or b.token_pct >= _BUDGET_HALT_PCT:
            logger.warning(
                "BudgetController: token budget %.0f%% — ESCALATE", b.token_pct
            )
            state.annotations["budget_halt"] = (
                f"Token budget {b.token_pct:.0f}% >= halt threshold {_BUDGET_HALT_PCT:.0f}%"
            )
            return ExecutiveDecision.ESCALATE

        if b.api_retries >= _MAX_API_RETRIES:
            logger.warning(
                "BudgetController: %d API retries — PAUSE", b.api_retries
            )
            state.annotations["budget_retry_halt"] = f"API retries {b.api_retries} >= {_MAX_API_RETRIES}"
            return ExecutiveDecision.PAUSE

        if b.llm_calls >= _MAX_LLM_CALLS:
            logger.warning(
                "BudgetController: %d LLM calls — ESCALATE", b.llm_calls
            )
            state.annotations["budget_llm_halt"] = f"LLM calls {b.llm_calls} >= {_MAX_LLM_CALLS}"
            return ExecutiveDecision.ESCALATE

        if b.token_pct >= _BUDGET_WARN_PCT:
            logger.info("BudgetController: token budget %.0f%% — approaching limit", b.token_pct)
            state.annotations["budget_warning"] = f"Budget {b.token_pct:.0f}%"

        return ExecutiveDecision.CONTINUE

    def update(self, state: ExecutiveState, **kwargs) -> None:
        """Update budget state from telemetry. Call from the execution loop."""
        b = state.budget
        b.tokens_used     += kwargs.get("tokens", 0)
        b.llm_calls       += kwargs.get("llm_calls", 0)
        b.api_retries     += kwargs.get("api_retries", 0)
        b.tool_calls      += kwargs.get("tool_calls", 0)
        b.elapsed_s        = kwargs.get("elapsed_s", b.elapsed_s)

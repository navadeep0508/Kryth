"""Budget Controller — track and enforce LLM/time/agent spend limits.

Prevents runaway execution by monitoring:
  - Tokens consumed (in + out)
  - Wall-clock elapsed time
  - Parallel agent count
  - Total tool calls

When a limit is approaching or exceeded, it signals the supervisor to
stop or reduce parallelism rather than silently burning resources.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class BudgetSnapshot:
    tokens_spent: int = 0
    token_limit: int = 0           # 0 = no limit
    tokens_remaining: int = 0
    elapsed_seconds: float = 0.0
    time_limit_seconds: float = 0.0  # 0 = no limit
    agent_spawns: int = 0
    agent_limit: int = 0             # 0 = no limit
    tool_calls: int = 0
    over_token_budget: bool = False
    over_time_budget: bool = False
    over_agent_budget: bool = False

    @property
    def any_exceeded(self) -> bool:
        return self.over_token_budget or self.over_time_budget or self.over_agent_budget

    @property
    def token_pct(self) -> float:
        if not self.token_limit:
            return 0.0
        return self.tokens_spent / self.token_limit

    @property
    def time_pct(self) -> float:
        if not self.time_limit_seconds:
            return 0.0
        return self.elapsed_seconds / self.time_limit_seconds


class BudgetController:
    """Track spend, enforce soft/hard limits, signal the supervisor."""

    def __init__(
        self,
        token_limit: int = 0,
        time_limit_seconds: float = 0.0,
        agent_limit: int = 20,
    ) -> None:
        self._lock = threading.RLock()
        self._token_limit = token_limit
        self._time_limit = time_limit_seconds
        self._agent_limit = agent_limit
        self._tokens_in: int = 0
        self._tokens_out: int = 0
        self._tool_calls: int = 0
        self._agent_spawns: int = 0
        self._start_time: float = time.monotonic()
        # Soft-stop flag — set when limits approached
        self._soft_stop: bool = False
        self._hard_stop: bool = False

    def configure(
        self,
        token_limit: int = 0,
        time_limit_seconds: float = 0.0,
        agent_limit: int = 20,
    ) -> None:
        with self._lock:
            self._token_limit = token_limit
            self._time_limit = time_limit_seconds
            self._agent_limit = agent_limit

    # ------------------------------------------------------------------
    # Recording

    def record_tokens(self, tokens_in: int, tokens_out: int) -> None:
        with self._lock:
            self._tokens_in += max(0, tokens_in)
            self._tokens_out += max(0, tokens_out)
            self._evaluate()

    def record_tool_call(self) -> None:
        with self._lock:
            self._tool_calls += 1

    def record_agent_spawn(self) -> None:
        with self._lock:
            self._agent_spawns += 1
            self._evaluate()

    def reset(self) -> None:
        with self._lock:
            self._tokens_in = 0
            self._tokens_out = 0
            self._tool_calls = 0
            self._agent_spawns = 0
            self._start_time = time.monotonic()
            self._soft_stop = False
            self._hard_stop = False

    # ------------------------------------------------------------------
    # Query

    def snapshot(self) -> BudgetSnapshot:
        with self._lock:
            total = self._tokens_in + self._tokens_out
            elapsed = time.monotonic() - self._start_time
            remaining = max(0, self._token_limit - total) if self._token_limit else total
            return BudgetSnapshot(
                tokens_spent=total,
                token_limit=self._token_limit,
                tokens_remaining=remaining,
                elapsed_seconds=elapsed,
                time_limit_seconds=self._time_limit,
                agent_spawns=self._agent_spawns,
                agent_limit=self._agent_limit,
                tool_calls=self._tool_calls,
                over_token_budget=self._hard_stop and self._token_limit > 0
                    and total >= self._token_limit,
                over_time_budget=self._time_limit > 0 and elapsed >= self._time_limit,
                over_agent_budget=self._agent_limit > 0
                    and self._agent_spawns >= self._agent_limit,
            )

    def should_stop(self) -> bool:
        snap = self.snapshot()
        return snap.any_exceeded or self._hard_stop

    def should_reduce_parallelism(self) -> bool:
        """True when token or time budget is >75% consumed."""
        snap = self.snapshot()
        return snap.token_pct > 0.75 or snap.time_pct > 0.75

    def is_goal_achievable(self) -> bool:
        """Heuristic: can we still complete the mission within budget?"""
        snap = self.snapshot()
        if snap.over_token_budget or snap.over_time_budget:
            return False
        # If more than 90% token budget used, probably not
        if snap.token_pct > 0.90:
            return False
        return True

    def format_summary(self) -> str:
        snap = self.snapshot()
        parts = [f"tokens {snap.tokens_spent:,}"]
        if snap.token_limit:
            parts[0] += f"/{snap.token_limit:,}"
        parts.append(f"elapsed {snap.elapsed_seconds:.0f}s")
        parts.append(f"agents {snap.agent_spawns}")
        if snap.any_exceeded:
            parts.append("BUDGET EXCEEDED")
        return "  ·  ".join(parts)

    # ------------------------------------------------------------------
    # Internal

    def _evaluate(self) -> None:
        total = self._tokens_in + self._tokens_out
        elapsed = time.monotonic() - self._start_time
        if self._token_limit and total >= self._token_limit:
            self._hard_stop = True
        elif self._token_limit and total >= self._token_limit * 0.85:
            self._soft_stop = True
        if self._time_limit and elapsed >= self._time_limit:
            self._hard_stop = True
        if self._agent_limit and self._agent_spawns >= self._agent_limit:
            self._soft_stop = True


# Module-level singleton
budget_controller = BudgetController(agent_limit=20)

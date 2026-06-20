"""Resource & budget management (Phase H).

Tracks multi-dimensional budgets — token, mission, department, tool, executor,
time, risk — with spend recording, linear forecasting (spend rate → projected
exhaustion), warnings, and optional enforcement. A budget is a soft advisory by
default; ``enforce=True`` makes ``can_spend`` gate at the limit. Pure accounting.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Dict, List, Optional

# The seven budget dimensions the spec lists.
DIMENSIONS = ("token", "mission", "department", "tool", "executor", "time", "risk")

_WARN_AT = 0.8       # warn when 80% consumed
_CRITICAL_AT = 0.95


@dataclass
class Budget:
    name: str
    dimension: str
    limit: float
    spent: float = 0.0
    enforce: bool = False

    @property
    def remaining(self) -> float:
        return max(0.0, self.limit - self.spent)

    @property
    def usage(self) -> float:
        return min(1.0, self.spent / self.limit) if self.limit else 0.0

    @property
    def status(self) -> str:
        u = self.usage
        if u >= 1.0:
            return "exhausted"
        if u >= _CRITICAL_AT:
            return "critical"
        if u >= _WARN_AT:
            return "warning"
        return "ok"

    def to_dict(self) -> dict:
        return {"name": self.name, "dimension": self.dimension, "limit": self.limit,
                "spent": round(self.spent, 2), "remaining": round(self.remaining, 2),
                "usage": round(self.usage, 3), "status": self.status,
                "enforce": self.enforce}


class BudgetManager:
    """Holds budgets across dimensions; records spend, forecasts, enforces."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._budgets: Dict[str, Budget] = {}

    def _key(self, dimension: str, name: str) -> str:
        return f"{dimension}:{name}"

    def set_budget(self, dimension: str, name: str, limit: float, *,
                   enforce: bool = False) -> Budget:
        with self._lock:
            b = Budget(name=name, dimension=dimension, limit=float(limit), enforce=enforce)
            self._budgets[self._key(dimension, name)] = b
            return b

    def can_spend(self, dimension: str, name: str, amount: float) -> bool:
        with self._lock:
            b = self._budgets.get(self._key(dimension, name))
            if not b or not b.enforce:
                return True
            return b.spent + amount <= b.limit

    def spend(self, dimension: str, name: str, amount: float) -> bool:
        """Record spend. Returns False (and records nothing) if enforced and the
        spend would exceed the limit."""
        with self._lock:
            b = self._budgets.get(self._key(dimension, name))
            if b is None:
                return True  # untracked dimension → no limit
            if b.enforce and b.spent + amount > b.limit:
                return False
            b.spent += amount
            return True

    def forecast(self, dimension: str, name: str, *, elapsed: float,
                 horizon: float) -> dict:
        """Linear forecast: at the current spend rate, projected spend after
        ``horizon`` more units, and whether it exhausts the budget."""
        with self._lock:
            b = self._budgets.get(self._key(dimension, name))
            if not b or elapsed <= 0:
                return {"projected": 0.0, "will_exhaust": False, "rate": 0.0}
            rate = b.spent / elapsed
            projected = b.spent + rate * horizon
            return {"rate": round(rate, 3), "projected": round(projected, 2),
                    "will_exhaust": projected > b.limit,
                    "remaining_runway": round(b.remaining / rate, 1) if rate else float("inf")}

    def warnings(self) -> List[dict]:
        with self._lock:
            return [b.to_dict() for b in self._budgets.values()
                    if b.status in ("warning", "critical", "exhausted")]

    def report(self) -> dict:
        with self._lock:
            by_dim: Dict[str, List[dict]] = {}
            for b in self._budgets.values():
                by_dim.setdefault(b.dimension, []).append(b.to_dict())
            return {"dimensions": by_dim,
                    "warnings": [b.to_dict() for b in self._budgets.values()
                                 if b.status != "ok"]}

    def snapshot(self) -> List[dict]:
        with self._lock:
            return [b.to_dict() for b in self._budgets.values()]

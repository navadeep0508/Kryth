"""Resource Manager — tracks and allocates system-wide resources across missions."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional

from agent.mos.mission_state import Mission, ResourceBudget


@dataclass
class SystemResources:
    """Global resource pool available to the MOS."""
    total_workers:        int = 16
    total_executors:      int = 8
    total_browser_tabs:   int = 4
    total_api_budget:     int = 10_000   # per-session API call cap
    total_token_budget:   int = 2_000_000
    total_memory_mb:      int = 4_096


class ResourceManager:
    """Centrally tracks resource consumption across all running missions.

    Allocation rules:
    - Each mission requests a ResourceBudget at admission.
    - ResourceManager checks capacity before granting the slot.
    - On mission completion/failure, resources are released.
    - No mission can consume more than its allocated budget (soft-enforced via metrics).
    """

    def __init__(self, system: Optional[SystemResources] = None) -> None:
        self._sys  = system or SystemResources()
        self._lock = threading.Lock()

        # id → allocated ResourceBudget
        self._allocations: dict[str, ResourceBudget] = {}

    # ── Capacity check ────────────────────────────────────────────────

    def can_admit(self, requested: ResourceBudget) -> tuple[bool, str]:
        """Return (ok, reason). reason is empty string if ok."""
        with self._lock:
            used = self._aggregate()
            if used["workers"] + requested.max_workers > self._sys.total_workers:
                return False, f"workers exhausted ({used['workers']}/{self._sys.total_workers})"
            if used["executors"] + requested.max_executors > self._sys.total_executors:
                return False, f"executors exhausted ({used['executors']}/{self._sys.total_executors})"
            if used["browser_tabs"] + requested.max_browser_tabs > self._sys.total_browser_tabs:
                return False, f"browser_tabs exhausted ({used['browser_tabs']}/{self._sys.total_browser_tabs})"
            if used["tokens"] + requested.max_tokens > self._sys.total_token_budget:
                return False, f"token_budget exhausted"
            return True, ""

    def allocate(self, mission_id: str, budget: ResourceBudget) -> None:
        with self._lock:
            self._allocations[mission_id] = budget

    def release(self, mission_id: str) -> None:
        with self._lock:
            self._allocations.pop(mission_id, None)

    # ── Consumption update ────────────────────────────────────────────

    def update_consumption(self, mission_id: str, mission: Mission) -> None:
        """Sync consumed amounts from mission metrics into allocation record."""
        with self._lock:
            budget = self._allocations.get(mission_id)
            if budget is None:
                return
            budget.tokens_used    = mission.budget.tokens_used
            budget.api_calls_used = mission.budget.api_calls_used
            budget.workers_used   = mission.budget.workers_used

    # ── Utilization ───────────────────────────────────────────────────

    def utilization(self) -> dict[str, float]:
        with self._lock:
            used = self._aggregate()
        s = self._sys
        def pct(a, b): return a / b * 100 if b else 0.0
        return {
            "workers_pct":      pct(used["workers"],      s.total_workers),
            "executors_pct":    pct(used["executors"],    s.total_executors),
            "browser_tabs_pct": pct(used["browser_tabs"], s.total_browser_tabs),
            "tokens_pct":       pct(used["tokens"],       s.total_token_budget),
            "api_pct":          pct(used["api"],          s.total_api_budget),
        }

    def available(self) -> dict[str, int]:
        with self._lock:
            used = self._aggregate()
        s = self._sys
        return {
            "workers":      max(0, s.total_workers      - used["workers"]),
            "executors":    max(0, s.total_executors    - used["executors"]),
            "browser_tabs": max(0, s.total_browser_tabs - used["browser_tabs"]),
            "tokens":       max(0, s.total_token_budget - used["tokens"]),
            "api_calls":    max(0, s.total_api_budget   - used["api"]),
        }

    def system_limits(self) -> dict[str, int]:
        return {
            "total_workers":      self._sys.total_workers,
            "total_executors":    self._sys.total_executors,
            "total_browser_tabs": self._sys.total_browser_tabs,
            "total_token_budget": self._sys.total_token_budget,
            "total_api_budget":   self._sys.total_api_budget,
        }

    # ── Internals ─────────────────────────────────────────────────────

    def _aggregate(self) -> dict[str, int]:
        totals: dict[str, int] = {
            "workers": 0, "executors": 0, "browser_tabs": 0, "tokens": 0, "api": 0,
        }
        for b in self._allocations.values():
            totals["workers"]      += b.max_workers
            totals["executors"]    += b.max_executors
            totals["browser_tabs"] += b.max_browser_tabs
            totals["tokens"]       += b.max_tokens
            totals["api"]          += b.max_api_calls
        return totals

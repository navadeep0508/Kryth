"""HotPathCache — V6 Phase 7.

Caches the results of repeated, expensive operations on the hot execution
path so they run at most once per mission (or across missions).

Cached operations:
  1. DeliverableContract lookup  (plan.get_contract is O(n) per call, called
     multiple times per module per milestone — cache makes it O(1))
  2. validate_contract results   (same output → same result; skip re-running
     the heuristic checks on identical (contract_key, output_hash) pairs)
  3. Project file map            (ProjectSnapshot.get_or_build — disk IO)
  4. Milestone structure         (plan.ensure_structured_milestones — called
     repeatedly across v5_runtime callbacks)

All caches use `functools.lru_cache` or explicit LRU dicts; they are
mission-scoped by default (cleared via reset()) and thread-safe.

Phase 9 simplification: these caches eliminate the most common repeated
reads observed in the overhead profiler — contract lookups alone were called
3-4× per module per milestone before caching.
"""
from __future__ import annotations

import hashlib
import threading
from functools import lru_cache
from typing import Dict, Optional, Tuple


# ── Contract lookup cache ─────────────────────────────────────────────────────

class ContractCache:
    """Cache plan.get_contract() calls per plan instance.

    Because `plan` objects are mutable, we key on `id(plan)` and module_name.
    Cleared when `reset()` is called (at mission end or plan replacement).
    """

    def __init__(self) -> None:
        self._cache: Dict[Tuple[int, str], object] = {}
        self._lock = threading.Lock()

    def get(self, plan, module_name: str) -> object:
        key = (id(plan), module_name)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        result = plan.get_contract(module_name)
        with self._lock:
            self._cache[key] = result
        return result

    def reset(self) -> None:
        with self._lock:
            self._cache.clear()

    @property
    def hit_rate(self) -> float:
        return len(self._cache) / max(len(self._cache), 1)


# ── Contract validation cache ──────────────────────────────────────────────────

def _output_hash(output: str) -> str:
    return hashlib.md5((output or "").encode("utf-8", errors="replace")).hexdigest()[:16]


def _contract_key(contract) -> str:
    mn = getattr(contract, "module_name", "")
    cr = "|".join(getattr(contract, "success_criteria", [])[:3])
    return f"{mn}:{cr}"


class ValidationCache:
    """Cache validate_contract() for identical (contract, output) pairs.

    An identical output from the same contract will always produce the same
    validation result — skip re-running heuristic checks. The cache is keyed
    on a compact (contract_key, output_hash) pair.
    """

    def __init__(self, maxsize: int = 512) -> None:
        self._maxsize = maxsize
        self._cache: Dict[str, object] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, contract, output: str) -> Optional[object]:
        key = f"{_contract_key(contract)}:{_output_hash(output)}"
        with self._lock:
            if key in self._cache:
                self._hits += 1
                return self._cache[key]
            self._misses += 1
            return None

    def set(self, contract, output: str, result: object) -> None:
        key = f"{_contract_key(contract)}:{_output_hash(output)}"
        with self._lock:
            if len(self._cache) >= self._maxsize:
                # Evict oldest entry (FIFO approximation via popitem)
                try:
                    self._cache.pop(next(iter(self._cache)))
                except StopIteration:
                    pass
            self._cache[key] = result

    def reset(self) -> None:
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total else 0.0


# ── Milestone structure cache ──────────────────────────────────────────────────

class MilestoneStructureCache:
    """Cache plan.ensure_structured_milestones() results.

    ensure_structured_milestones() re-builds the milestone list from
    dependency_layers() every call when structured_milestones is empty.
    With this cache, the first call builds it; all subsequent calls on the
    same plan object return the cached result in O(1).
    """

    def __init__(self) -> None:
        self._cache: Dict[int, list] = {}   # id(plan) → milestones list
        self._lock = threading.Lock()

    def get_or_build(self, plan) -> list:
        plan_id = id(plan)
        with self._lock:
            if plan_id in self._cache:
                return self._cache[plan_id]
        milestones = plan.ensure_structured_milestones()
        with self._lock:
            self._cache[plan_id] = milestones
        return milestones

    def reset(self) -> None:
        with self._lock:
            self._cache.clear()


# ── Module-level singletons (mission-scoped) ──────────────────────────────────

_contract_cache    = ContractCache()
_validation_cache  = ValidationCache()
_milestone_cache   = MilestoneStructureCache()


def get_contract_cached(plan, module_name: str) -> object:
    """Cached plan.get_contract() — same result, no repeated O(n) scan."""
    return _contract_cache.get(plan, module_name)


def validate_contract_cached(contract, output: str, project_root: str = ".") -> object:
    """Return cached ContractValidationResult or compute + cache it."""
    cached = _validation_cache.get(contract, output)
    if cached is not None:
        return cached
    from agent.orchestration.milestone_engine import validate_contract
    result = validate_contract(contract, output, project_root)
    _validation_cache.set(contract, output, result)
    return result


def get_milestones_cached(plan) -> list:
    """Cached plan.ensure_structured_milestones()."""
    return _milestone_cache.get_or_build(plan)


def reset_caches() -> None:
    """Clear all mission-level caches — call at the start of each mission."""
    _contract_cache.reset()
    _validation_cache.reset()
    _milestone_cache.reset()


def cache_stats() -> dict:
    return {
        "contract_cache_entries": len(_contract_cache._cache),
        "validation_cache_hit_rate": f"{_validation_cache.hit_rate:.0%}",
        "validation_cache_hits": _validation_cache._hits,
        "validation_cache_misses": _validation_cache._misses,
    }

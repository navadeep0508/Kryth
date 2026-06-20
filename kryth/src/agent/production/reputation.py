"""Tool & Executor reputation routing (Phases 3 & 4).

Tracks per-tool and per-executor outcomes — success, failure, runtime, recovery,
user override — and produces a confidence score + leaderboard so the runtime can
*prefer the historically-best* option when several can do the job.

This is additive observability: it records outcomes you feed it and *advises*
(`best_of`, `rank`). It does not intercept dispatch or replace the existing
`ExecutorSelector` / Executive `ConfidenceEngine` — callers consult it. Backed by
a JSON file so reputation survives across sessions; flag-gated writes keep test
and ephemeral runs clean.

A Wilson lower-bound on the success rate is used for ranking so a tool with 9/10
doesn't outrank one with 95/100 purely on raw rate — confidence grows with
evidence.
"""

from __future__ import annotations

import json
import math
import os
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, Optional


def reputation_path() -> Path:
    base = os.environ.get("KRYTH_REPUTATION_PATH")
    if base:
        return Path(base)
    return Path(os.path.expanduser("~")) / ".kryth" / "reputation.json"


def _wilson_lower_bound(successes: int, n: int, z: float = 1.96) -> float:
    """Lower bound of the Wilson score interval for a binomial proportion.
    Rewards evidence: few samples → score pulled toward 0.5-ish floor."""
    if n == 0:
        return 0.0
    phat = successes / n
    denom = 1 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


@dataclass
class Stat:
    name: str
    kind: str = "tool"            # "tool" | "executor"
    successes: int = 0
    failures: int = 0
    recovered: int = 0            # failures later recovered (retry/repair)
    overrides: int = 0            # user rejected / overrode the choice
    total_runtime_ms: float = 0.0
    runs: int = 0

    @property
    def attempts(self) -> int:
        return self.successes + self.failures

    @property
    def success_rate(self) -> float:
        return self.successes / self.attempts if self.attempts else 0.0

    @property
    def failure_rate(self) -> float:
        return self.failures / self.attempts if self.attempts else 0.0

    @property
    def recovery_rate(self) -> float:
        return self.recovered / self.failures if self.failures else 0.0

    @property
    def override_rate(self) -> float:
        return self.overrides / self.runs if self.runs else 0.0

    @property
    def avg_runtime_ms(self) -> float:
        return self.total_runtime_ms / self.runs if self.runs else 0.0

    def confidence(self) -> float:
        """0–1 reputation. Wilson-bounded success rate, lightly penalized by the
        user-override rate (a tool the user keeps rejecting loses trust)."""
        base = _wilson_lower_bound(self.successes, self.attempts)
        return round(max(0.0, base * (1 - 0.5 * self.override_rate)), 4)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(success_rate=round(self.success_rate, 4),
                 recovery_rate=round(self.recovery_rate, 4),
                 override_rate=round(self.override_rate, 4),
                 avg_runtime_ms=round(self.avg_runtime_ms, 1),
                 confidence=self.confidence())
        return d


class ReputationStore:
    """Persistent per-tool/executor reputation with a leaderboard."""

    def __init__(self, path: Optional[Path] = None, *, autoload: bool = True):
        self.path = Path(path) if path else reputation_path()
        self._lock = threading.RLock()
        self._stats: Dict[str, Stat] = {}
        if autoload:
            self.load()

    def _key(self, name: str, kind: str) -> str:
        return f"{kind}:{name}"

    def _stat(self, name: str, kind: str) -> Stat:
        k = self._key(name, kind)
        s = self._stats.get(k)
        if s is None:
            s = Stat(name=name, kind=kind)
            self._stats[k] = s
        return s

    # -- recording ----------------------------------------------------------

    def record(self, name: str, *, kind: str = "tool", success: bool = True,
               runtime_ms: float = 0.0, recovered: bool = False,
               overridden: bool = False) -> None:
        with self._lock:
            s = self._stat(name, kind)
            s.runs += 1
            s.total_runtime_ms += max(0.0, runtime_ms)
            if success:
                s.successes += 1
            else:
                s.failures += 1
                if recovered:
                    s.recovered += 1
            if overridden:
                s.overrides += 1

    def record_tool(self, name: str, **kw) -> None:
        self.record(name, kind="tool", **kw)

    def record_executor(self, name: str, **kw) -> None:
        self.record(name, kind="executor", **kw)

    # -- querying / routing -------------------------------------------------

    def confidence(self, name: str, kind: str = "tool") -> float:
        with self._lock:
            k = self._key(name, kind)
            return self._stats[k].confidence() if k in self._stats else 0.0

    def best_of(self, candidates, kind: str = "tool", *, default=None):
        """Prefer the highest-confidence candidate. Unseen candidates (no
        history) tie at 0 — ties resolve to the caller's input order, so a brand
        new tool isn't blocked, it just isn't *preferred*."""
        cand = list(candidates or [])
        if not cand:
            return default
        with self._lock:
            return max(cand, key=lambda c: self.confidence(c, kind))

    def rank(self, kind: Optional[str] = None) -> list:
        """Leaderboard: stats sorted by confidence desc, then attempts desc."""
        with self._lock:
            items = [s for s in self._stats.values() if kind is None or s.kind == kind]
            return sorted(items, key=lambda s: (s.confidence(), s.attempts), reverse=True)

    def leaderboard(self, kind: str = "tool", top: int = 10) -> list:
        return [s.to_dict() for s in self.rank(kind)[:top]]

    # -- persistence --------------------------------------------------------

    def load(self) -> None:
        with self._lock:
            try:
                if self.path.exists():
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    for k, d in raw.items():
                        self._stats[k] = Stat(
                            name=d["name"], kind=d.get("kind", "tool"),
                            successes=d.get("successes", 0), failures=d.get("failures", 0),
                            recovered=d.get("recovered", 0), overrides=d.get("overrides", 0),
                            total_runtime_ms=d.get("total_runtime_ms", 0.0), runs=d.get("runs", 0),
                        )
            except Exception:
                pass  # corrupt store → start fresh, never crash the runtime

    def save(self) -> None:
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                raw = {self._key(s.name, s.kind): {
                    "name": s.name, "kind": s.kind, "successes": s.successes,
                    "failures": s.failures, "recovered": s.recovered,
                    "overrides": s.overrides, "total_runtime_ms": s.total_runtime_ms,
                    "runs": s.runs} for s in self._stats.values()}
                self.path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
            except Exception:
                pass


# Module-level default store (lazy).
_DEFAULT: Optional[ReputationStore] = None
_DEFAULT_LOCK = threading.Lock()


def get_store() -> ReputationStore:
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = ReputationStore()
        return _DEFAULT

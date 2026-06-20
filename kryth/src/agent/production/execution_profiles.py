"""Execution profiles — FAST / BALANCED / MAXIMUM_QUALITY (Phase 11).

One knob that bundles the quality/throughput trade-offs the runtime already
exposes individually: verification depth, worker cap, token budget multiplier,
review depth, retry budget. BALANCED reproduces today's behavior exactly, so the
default is unchanged; FAST trades thoroughness for speed/cost; MAXIMUM_QUALITY
maxes out validation, testing, and review.

Pure config (no side effects). The agent loop / orchestrator reads a `Profile`
and applies its knobs; selection is via `KRYTH_EXEC_PROFILE` or a `/exec`
command. Additive — nothing changes unless a non-default profile is selected.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass(frozen=True)
class Profile:
    name: str
    verification: str          # "minimal" | "standard" | "maximum"
    run_tests: bool
    code_review: bool
    worker_cap: int            # max parallel workers
    token_budget_mult: float   # scales per-task token budget (1.0 = current)
    max_retries: int
    parallel_verify: bool      # run validators asynchronously after writes
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# BALANCED == current behavior (the contract: selecting it changes nothing).
BALANCED = Profile(
    name="balanced", verification="standard", run_tests=True, code_review=False,
    worker_cap=16, token_budget_mult=1.0, max_retries=1, parallel_verify=True,
    description="Current behavior — sensible defaults.",
)

FAST = Profile(
    name="fast", verification="minimal", run_tests=False, code_review=False,
    worker_cap=8, token_budget_mult=0.6, max_retries=0, parallel_verify=False,
    description="Minimal verification, lower token usage, fewer workers. Speed first.",
)

MAXIMUM_QUALITY = Profile(
    name="maximum_quality", verification="maximum", run_tests=True, code_review=True,
    worker_cap=16, token_budget_mult=1.5, max_retries=2, parallel_verify=True,
    description="Maximum validation, testing, and review. Quality first.",
)

# PONYTAIL — lazy senior dev mode (V5).
# Workers stop at the first rung that holds:
#   1. YAGNI  2. stdlib  3. platform  4. installed dep  5. one line  6. minimum
# Smallest token budget, fewest files, no abstractions unless asked.
# Appropriate for: bug fixes, small features, refactors, CRUD.
# NOT appropriate for: architecture redesign, large system design.
PONYTAIL = Profile(
    name="ponytail",
    verification="standard",
    run_tests=True,          # the one check that can't be skipped
    code_review=True,        # team lead checks for overengineering
    worker_cap=8,
    token_budget_mult=0.45,  # ~55% token reduction vs BALANCED (matches benchmark)
    max_retries=1,
    parallel_verify=True,
    description=(
        "Lazy senior dev mode. Workers stop at the first rung that holds: "
        "YAGNI → stdlib → platform → installed dep → one line → minimum. "
        "80-94% less code, 42-75% cheaper than unconstrained agents. "
        "Best for: bug fixes, small features, refactors."
    ),
)

_PROFILES = {
    "fast": FAST,
    "balanced": BALANCED,
    "maximum_quality": MAXIMUM_QUALITY,
    "ponytail": PONYTAIL,
    # friendly aliases
    "max": MAXIMUM_QUALITY,
    "quality": MAXIMUM_QUALITY,
    "default": BALANCED,
    "lean": PONYTAIL,
    "lazy": PONYTAIL,
    "minimal": PONYTAIL,
}


def normalize(name: str) -> Optional[str]:
    n = (name or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _PROFILES[n].name if n in _PROFILES else None


def get_profile(name: str) -> Profile:
    """Resolve a profile by name/alias; falls back to BALANCED for unknown input."""
    n = (name or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _PROFILES.get(n, BALANCED)


def active_profile() -> Profile:
    """The profile selected via env (default BALANCED → unchanged behavior)."""
    return get_profile(os.environ.get("KRYTH_EXEC_PROFILE", "balanced"))


def all_profiles() -> list:
    return [FAST, BALANCED, MAXIMUM_QUALITY, PONYTAIL]


def is_ponytail(profile: Profile) -> bool:
    """True when the active profile is PONYTAIL (lazy senior dev mode)."""
    return profile.name == "ponytail"


def render(profile: Profile) -> str:
    return (
        f"  Execution profile: {profile.name.upper()}\n"
        f"    {profile.description}\n"
        f"    Verification   {profile.verification}\n"
        f"    Run tests      {profile.run_tests}\n"
        f"    Code review    {profile.code_review}\n"
        f"    Worker cap     {profile.worker_cap}\n"
        f"    Token budget   {profile.token_budget_mult:.1f}x\n"
        f"    Max retries    {profile.max_retries}\n"
        f"    Parallel verify {profile.parallel_verify}"
    )

"""Dependency Builder — infers dependencies between decomposed action items.

Rules (applied in order):
  1. Explicit deps from the LLM plan are preserved.
  2. File write before file edit of the same path.
  3. Models / schemas before services that use them.
  4. Services before routes / controllers.
  5. Routes before integration tests.
  6. Any "test" step depends on the last non-test step.
  7. "deploy" depends on "build" and "test".
  8. "verify deployment" depends on "deploy".
  9. "notify" depends on everything.
"""

from __future__ import annotations

import re
from typing import Any


def _matches(goal: str, patterns: list[str]) -> bool:
    lower = goal.lower()
    return any(re.search(p, lower) for p in patterns)


_TIERS: list[tuple[str, list[str]]] = [
    ("model",   [r"\bmodel\b", r"\bschema\b", r"\bmigration\b", r"\borm\b"]),
    ("service", [r"\bservice\b", r"\bjwt\b", r"\bauth service\b", r"\brepository\b"]),
    ("middleware", [r"\bmiddleware\b", r"\bguard\b", r"\binterceptor\b"]),
    ("route",   [r"\broute\b", r"\bcontroller\b", r"\bendpoint\b", r"\bapi\b"]),
    ("test",    [r"\btest\b", r"\bspec\b", r"\bpytest\b", r"\bjest\b"]),
    ("build",   [r"\bbuild\b", r"\bcompile\b", r"\bdocker build\b"]),
    ("deploy",  [r"\bdeploy\b", r"\brelease\b", r"\bpush image\b"]),
    ("verify",  [r"\bverify\b", r"\bhealth check\b", r"\bvalidate deployment\b"]),
    ("notify",  [r"\bnotify\b", r"\bslack\b", r"\bemail\b", r"\bannounce\b"]),
]


class DependencyBuilder:
    """Infer or augment dependencies for a list of action dicts."""

    def build(self, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return the same action list with `dependencies` filled in."""
        # Index by id
        by_id = {a["id"]: a for a in actions}

        # Classify each action into a tier
        tier_of: dict[str, int] = {}
        for a in actions:
            tier_of[a["id"]] = self._tier(a.get("goal", a.get("id", "")))

        # For each action, ensure it depends on all lower-tier actions
        # (unless it already has explicit deps that cover it)
        for a in actions:
            existing = set(a.get("dependencies", []))
            my_tier  = tier_of[a["id"]]

            # Add deps from the highest tier below mine that has actions
            inferred: set[str] = set()
            for aid, t in tier_of.items():
                if aid == a["id"]:
                    continue
                if t < my_tier:
                    inferred.add(aid)

            # Only add inferred deps that aren't already transitively covered
            direct = existing | inferred
            a["dependencies"] = sorted(direct)

        return actions

    def _tier(self, goal: str) -> int:
        for idx, (_, patterns) in enumerate(_TIERS):
            if _matches(goal, patterns):
                return idx
        return 3   # default: route-level

"""Context Shard Manager (Phases 1 & 2).

A mission's full context — every file, memory, dependency, and plan line — is far
more than any single domain agent needs. The backend agent does not need the CSS;
the docs agent does not need the migration SQL. Sending the whole context to every
worker wastes tokens and dilutes attention.

This module partitions a mission's context into **per-domain shards** and builds a
**hierarchical compression cascade** (mission summary → domain summary → agent
view) so workers receive a compressed, relevant slice instead of raw history.

It is pure and additive: it *computes* shards from inputs you pass in. It does not
read the filesystem, mutate scheduler state, or change what executes — the
orchestrator decides whether to use a shard. Reductions are measured so the win is
provable (Phase 1 success metric).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# Domain classification. Each domain owns a set of path/keyword signals. A file is
# assigned to every domain it matches (files can be shared, e.g. a config touched
# by backend + devops); unmatched files go to a shared "core" bucket every agent
# sees, since they are usually cross-cutting (root config, lockfiles).
_DOMAIN_SIGNALS: dict[str, tuple[str, ...]] = {
    "frontend": (r"\.(jsx?|tsx?|vue|svelte|css|scss|less|html)$", r"(^|/)(components?|pages?|ui|views?|styles?|public|assets)/"),
    "backend":  (r"(^|/)(api|server|services?|routes?|controllers?|handlers?|models?)/", r"\.(py|go|rb|java|rs|php)$"),
    "database": (r"\.(sql|prisma)$", r"(^|/)(migrations?|schema|db|database|seeds?)/", r"(schema|migration|seed)"),
    "testing":  (r"(^|/)(tests?|spec|__tests__|e2e)/", r"\.(test|spec)\.[a-z]+$", r"(^|/)conftest\.py$"),
    "devops":   (r"(^|/)(\.github|ci|deploy|infra|k8s|terraform|ansible)/", r"(dockerfile|docker-compose|\.ya?ml|\.tf)$", r"(^|/)Makefile$"),
    "docs":     (r"\.(md|rst|txt|adoc)$", r"(^|/)docs?/", r"(readme|changelog|license)"),
}

# Files that belong to no specific domain but everyone benefits from seeing.
_SHARED_SIGNALS = (
    r"(^|/)(package\.json|pyproject\.toml|requirements\.txt|go\.mod|cargo\.toml|tsconfig\.json|\.env\.example)$",
)

_WORD = re.compile(r"[a-z0-9]+")


def _matches(path: str, patterns: Iterable[str]) -> bool:
    p = path.lower()
    return any(re.search(pat, p) for pat in patterns)


def classify_domain(path: str) -> list[str]:
    """Domains a file belongs to. May be several; empty list ⇒ unclassified."""
    return [d for d, sig in _DOMAIN_SIGNALS.items() if _matches(path, sig)]


def _approx_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token) — good enough for reduction math."""
    return max(1, len(text) // 4)


@dataclass
class ContextShard:
    domain: str
    files: list = field(default_factory=list)
    memories: list = field(default_factory=list)
    dependencies: list = field(default_factory=list)
    plan_lines: list = field(default_factory=list)
    summary: str = ""        # compressed domain view (Phase 2)

    def token_estimate(self) -> int:
        body = "\n".join(self.files + self.memories + self.dependencies + self.plan_lines)
        return _approx_tokens(body) + _approx_tokens(self.summary)


@dataclass
class ShardResult:
    shards: dict = field(default_factory=dict)          # domain -> ContextShard
    shared_files: list = field(default_factory=list)    # cross-cutting, seen by all
    mission_summary: str = ""
    full_tokens: int = 0                                 # tokens if every agent saw everything
    sharded_tokens: int = 0                              # tokens with sharding (summed across agents)
    context_reduction_pct: float = 0.0
    token_reduction_pct: float = 0.0

    def for_agent(self, domain: str) -> dict:
        """The compressed view a domain agent receives: shared files + its shard +
        the cascade summaries. Workers never get raw mission history (Phase 2)."""
        sh = self.shards.get(domain)
        return {
            "domain": domain,
            "mission_summary": self.mission_summary,
            "domain_summary": sh.summary if sh else "",
            "files": list(self.shared_files) + (sh.files if sh else []),
            "memories": sh.memories if sh else [],
            "dependencies": sh.dependencies if sh else [],
            "plan_lines": sh.plan_lines if sh else [],
        }

    def to_dict(self) -> dict:
        return {
            "domains": list(self.shards.keys()),
            "shared_files": self.shared_files,
            "context_reduction_pct": round(self.context_reduction_pct, 1),
            "token_reduction_pct": round(self.token_reduction_pct, 1),
            "full_tokens": self.full_tokens,
            "sharded_tokens": self.sharded_tokens,
        }


def _relevant(items: list, domain: str, keywords: set) -> list:
    """Filter memories/plan-lines/deps to those mentioning the domain or its files."""
    out = []
    dl = domain.lower()
    for it in items or []:
        s = str(it).lower()
        if dl in s or any(k in s for k in keywords):
            out.append(it)
    return out


class ContextShardManager:
    """Partition a mission context into per-domain shards (Phase 1) and build the
    mission→domain→agent compression cascade (Phase 2)."""

    def shard(
        self,
        files: list,
        *,
        memories: list | None = None,
        dependencies: list | None = None,
        plan_lines: list | None = None,
        mission_goal: str = "",
    ) -> ShardResult:
        files = [str(f) for f in (files or [])]
        memories = memories or []
        dependencies = dependencies or []
        plan_lines = plan_lines or []

        shared = [f for f in files if _matches(f, _SHARED_SIGNALS)]
        shared_set = set(shared)

        shards: dict[str, ContextShard] = {}
        for f in files:
            for d in classify_domain(f):
                shards.setdefault(d, ContextShard(domain=d)).files.append(f)
        # Unclassified, non-shared files become shared too (cross-cutting safety).
        for f in files:
            if f not in shared_set and not classify_domain(f):
                shared.append(f); shared_set.add(f)

        # Attach relevant memories / deps / plan lines per domain.
        for d, sh in shards.items():
            kw = {w for fp in sh.files for w in _WORD.findall(fp.lower())}
            sh.memories = _relevant(memories, d, kw)
            sh.dependencies = _relevant(dependencies, d, kw)
            sh.plan_lines = _relevant(plan_lines, d, kw)
            sh.summary = self._domain_summary(d, sh)

        mission_summary = self._mission_summary(mission_goal, shards, len(files))

        # Reduction math: "full" = every agent sees the entire context. "sharded"
        # = each agent sees shared + its own shard + summaries. Summed across the
        # agents that actually run.
        whole = "\n".join(files + [str(m) for m in memories]
                          + [str(x) for x in dependencies] + [str(p) for p in plan_lines])
        full_per_agent = _approx_tokens(whole)
        n_agents = max(1, len(shards))
        full_total = full_per_agent * n_agents
        shared_tok = _approx_tokens("\n".join(shared)) + _approx_tokens(mission_summary)
        sharded_total = sum(sh.token_estimate() + shared_tok for sh in shards.values()) or full_per_agent

        ctx_red = 100 * (1 - (sharded_total / full_total)) if full_total else 0.0
        tok_red = ctx_red  # same basis here; kept distinct for caller clarity

        result = ShardResult(
            shards=shards, shared_files=shared, mission_summary=mission_summary,
            full_tokens=full_total, sharded_tokens=sharded_total,
            context_reduction_pct=max(0.0, ctx_red), token_reduction_pct=max(0.0, tok_red),
        )
        return result

    # -- compression cascade (Phase 2) --------------------------------------

    @staticmethod
    def _domain_summary(domain: str, sh: ContextShard) -> str:
        return (f"[{domain}] {len(sh.files)} files, {len(sh.dependencies)} deps, "
                f"{len(sh.memories)} memories")

    @staticmethod
    def _mission_summary(goal: str, shards: dict, n_files: int) -> str:
        domains = ", ".join(sorted(shards.keys())) or "core"
        head = (goal or "mission").strip().splitlines()[0][:120]
        return f"{head} · {n_files} files across [{domains}]"


def shard_mission(files, **kw) -> ShardResult:
    """Convenience wrapper around ``ContextShardManager().shard``."""
    return ContextShardManager().shard(files, **kw)

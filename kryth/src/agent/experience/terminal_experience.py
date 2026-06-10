"""Terminal Experience Engine — canonical commands for every project type.

Reads from TerminalMemory (primary) and ExperienceStore. Returns
known-good build/startup/test/deploy commands without guessing.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from agent.experience.store import ExperienceRecord, ExperienceStore


@dataclass
class TerminalExperience:
    stack: str              # e.g. "python", "node", "rust"
    kind: str               # "build", "startup", "test", "deploy", "recovery"
    commands: List[str]
    success_rate: float
    reuse_count: int
    last_used: float
    source: str             # "terminal_memory" | "experience_store"


class TerminalExperienceEngine:
    """Retrieves canonical terminal commands before guessing."""

    def __init__(self, store: ExperienceStore) -> None:
        self._store = store

    def lookup(self, kind: str, stack: str = "") -> Optional[TerminalExperience]:
        """Return the best-known command sequence for a given kind + stack."""
        # 1. TerminalMemory (most up-to-date for this project)
        try:
            from agent.terminal.memory import TerminalMemory
            tm = TerminalMemory()
            wf = tm.recall_workflow(kind, stack)
            if wf and wf.commands:
                return TerminalExperience(
                    stack=stack,
                    kind=kind,
                    commands=wf.commands,
                    success_rate=1.0 if wf.success_count > 0 else 0.5,
                    reuse_count=wf.success_count,
                    last_used=wf.last_used,
                    source="terminal_memory",
                )
        except Exception:
            pass

        # 2. Experience store
        query = f"{kind} {stack}"
        hits = self._store.search_fts(query, kind="terminal", limit=3)
        if hits:
            best = hits[0]
            return TerminalExperience(
                stack=best.extra.get("stack", stack),
                kind=best.extra.get("cmd_kind", kind),
                commands=best.extra.get("commands", []),
                success_rate=best.success_rate,
                reuse_count=best.reuse_count,
                last_used=best.recency_ts,
                source="experience_store",
            )
        return None

    def record(
        self,
        kind: str,
        stack: str,
        commands: List[str],
        success: bool,
    ) -> None:
        """Record a terminal workflow outcome."""
        try:
            from agent.terminal.memory import TerminalMemory
            tm = TerminalMemory()
            tm.record_workflow(kind, commands, stack, cwd=".")
        except Exception:
            pass

        rec = ExperienceRecord(
            id=None,
            project_hash=self._store._ph,
            kind="terminal",
            title=f"{kind}:{stack}",
            summary=f"{kind} for {stack}: {'; '.join(commands[:3])}",
            tags=[kind, stack],
            source_refs={},
            outcome="success" if success else "failure",
            importance=0.7,
            confidence=0.85 if success else 0.3,
            success_rate=1.0 if success else 0.0,
            reuse_count=0,
            novelty=0.3,
            recency_ts=time.time(),
            created_at=time.time(),
            composite_score=0.0,
            extra={"cmd_kind": kind, "stack": stack, "commands": commands},
        )
        self._store.insert(rec)

    def get_canonical(self, stack: str) -> Dict[str, List[str]]:
        """Return canonical commands dict for a stack (best known for each kind)."""
        result: Dict[str, List[str]] = {}
        for kind in ("build", "startup", "test", "deploy"):
            exp = self.lookup(kind, stack)
            if exp and exp.commands:
                result[kind] = exp.commands
        return result

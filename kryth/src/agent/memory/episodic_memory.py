"""EpisodicMemory — mission-level task memory.

Tracks hypotheses, root causes, edits, and decisions made during
a mission to prevent re-discovering the same issues.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Episode:
    objective: str = ""
    hypotheses: list[str] = field(default_factory=list)
    rejected_hypotheses: list[str] = field(default_factory=list)
    root_causes: list[str] = field(default_factory=list)
    edits: list[dict] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    results: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    successful_strategies: list[dict] = field(default_factory=list)
    failed_strategies: list[dict] = field(default_factory=list)

    def add_hypothesis(self, hypothesis: str) -> None:
        if hypothesis not in self.hypotheses and hypothesis not in self.rejected_hypotheses:
            self.hypotheses.append(hypothesis)

    def reject_hypothesis(self, hypothesis: str) -> None:
        if hypothesis in self.hypotheses:
            self.hypotheses.remove(hypothesis)
        if hypothesis not in self.rejected_hypotheses:
            self.rejected_hypotheses.append(hypothesis)

    def add_root_cause(self, cause: str) -> None:
        if cause not in self.root_causes:
            self.root_causes.append(cause)
            self.hypotheses = [h for h in self.hypotheses if h != cause]

    def add_edit(self, path: str, description: str = "") -> None:
        self.edits.append({
            "path": path,
            "description": description,
            "timestamp": time.time(),
        })

    def add_decision(self, decision: str) -> None:
        if decision not in self.decisions:
            self.decisions.append(decision)

    def add_result(self, result: str) -> None:
        if result not in self.results:
            self.results.append(result)

    def has_root_cause(self, cause: str) -> bool:
        return cause in self.root_causes

    def was_hypothesis_rejected(self, hypothesis: str) -> bool:
        return hypothesis in self.rejected_hypotheses

    def to_prompt_block(self, max_chars: int = 3000) -> str:
        parts = ["MISSION FINDINGS:"]
        if self.root_causes:
            parts.append(f"Root causes: {', '.join(self.root_causes[:5])}")
        if self.rejected_hypotheses:
            parts.append(f"Rejected: {', '.join(self.rejected_hypotheses[:5])}")
        if self.hypotheses:
            parts.append(f"Active hypotheses: {', '.join(self.hypotheses[:5])}")
        if self.edits:
            edits_short = [f"{e['path']}: {e['description'][:40]}" for e in self.edits[-5:]]
            parts.append("Edits applied:\n  " + "\n  ".join(edits_short))
        if self.decisions:
            parts.append("Decisions:\n  " + "\n  ".join(self.decisions[-5:]))
        if self.results:
            parts.append("Results:\n  " + "\n  ".join(self.results[-5:]))
        if self.successful_strategies:
            strat_lines = [f"  ✓ {s['strategy']}" for s in self.successful_strategies[-3:]]
            parts.append("What worked:\n" + "\n".join(strat_lines))
        if self.failed_strategies:
            strat_lines = [f"  ✗ {s['strategy']}" for s in self.failed_strategies[-3:]]
            parts.append("What failed:\n" + "\n".join(strat_lines))

        block = "\n\n".join(parts)
        if len(block) > max_chars:
            return block[:max_chars] + "\n... (truncated)"
        return block

    def add_strategy(self, strategy: str, outcome: str, detail: str = "") -> None:
        entry = {"strategy": strategy, "outcome": outcome, "detail": detail[:120], "timestamp": time.time()}
        if outcome == "success":
            existing = [s for s in self.successful_strategies if s["strategy"] == strategy]
            if not existing:
                self.successful_strategies.append(entry)
        else:
            existing = [s for s in self.failed_strategies if s["strategy"] == strategy]
            if not existing:
                self.failed_strategies.append(entry)

    def was_strategy_tried(self, strategy: str) -> Optional[str]:
        for s in self.successful_strategies:
            if s["strategy"] == strategy:
                return "success"
        for s in self.failed_strategies:
            if s["strategy"] == strategy:
                return "failed"
        return None

    def get_stats(self) -> dict:
        return {
            "root_causes": len(self.root_causes),
            "hypotheses": len(self.hypotheses),
            "rejected": len(self.rejected_hypotheses),
            "edits": len(self.edits),
            "decisions": len(self.decisions),
            "successful_strategies": len(self.successful_strategies),
            "failed_strategies": len(self.failed_strategies),
            "elapsed_s": time.time() - self.started_at,
        }


class EpisodicMemoryManager:
    """Thread-safe per-session EpisodicMemory."""

    def __init__(self):
        self._sessions: dict[int, Episode] = {}
        self._lock = threading.RLock()

    def _get(self, session_id: int) -> Episode:
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = Episode()
            return self._sessions[session_id]

    def set_objective(self, session_id: int, objective: str) -> None:
        ep = self._get(session_id)
        if not ep.objective:
            ep.objective = objective

    def add_hypothesis(self, session_id: int, hypothesis: str) -> None:
        self._get(session_id).add_hypothesis(hypothesis)

    def reject_hypothesis(self, session_id: int, hypothesis: str) -> None:
        self._get(session_id).reject_hypothesis(hypothesis)

    def add_root_cause(self, session_id: int, cause: str) -> None:
        self._get(session_id).add_root_cause(cause)

    def add_edit(self, session_id: int, path: str, description: str = "") -> None:
        self._get(session_id).add_edit(path, description)

    def add_decision(self, session_id: int, decision: str) -> None:
        self._get(session_id).add_decision(decision)

    def has_root_cause(self, session_id: int, cause: str) -> bool:
        return self._get(session_id).has_root_cause(cause)

    def was_hypothesis_rejected(self, session_id: int, hypothesis: str) -> bool:
        return self._get(session_id).was_hypothesis_rejected(hypothesis)

    def add_strategy(self, session_id: int, strategy: str, outcome: str, detail: str = "") -> None:
        self._get(session_id).add_strategy(strategy, outcome, detail)

    def was_strategy_tried(self, session_id: int, strategy: str) -> Optional[str]:
        return self._get(session_id).was_strategy_tried(strategy)

    def clear(self, session_id: int) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def get_prompt_block(self, session_id: int, max_chars: int = 3000) -> str:
        return self._get(session_id).to_prompt_block(max_chars=max_chars)

    def get_stats(self, session_id: int) -> dict:
        return self._get(session_id).get_stats()

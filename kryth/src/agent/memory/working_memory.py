"""WorkingMemory — per-turn reasoning state for the current task."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkingMemory:
    objective: str = ""
    plan: str = ""
    active_files: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    next_action: str = ""
    confidence: float = 0.0
    _turn_count: int = 0
    _started_at: float = field(default_factory=time.time)

    def set_objective(self, obj: str) -> None:
        self.objective = obj
        self._turn_count = 0
        self.findings.clear()

    def add_blocker(self, blocker: str) -> None:
        if blocker not in self.blockers:
            self.blockers.append(blocker)

    def resolve_blocker(self, blocker: str) -> None:
        self.blockers = [b for b in self.blockers if b != blocker]

    def add_finding(self, finding: str) -> None:
        if finding not in self.findings:
            self.findings.append(finding)

    def touch_turn(self) -> None:
        self._turn_count += 1

    def to_prompt_block(self, max_chars: int = 2000) -> str:
        parts = ["CURRENT OBJECTIVE:"]
        if self.objective:
            parts.append(f"  {self.objective}")
        else:
            parts.append("  (none)")

        if self.active_files:
            parts.append(f"\nActive files:\n  " + "\n  ".join(self.active_files[:8]))

        if self.blockers:
            parts.append(f"\nBlockers:\n  " + "\n  ".join(self.blockers[:5]))

        if self.findings:
            parts.append(f"\nFindings so far:\n  " + "\n  ".join(self.findings[:8]))

        if self.next_action:
            parts.append(f"\nNext action:\n  {self.next_action}")

        parts.append(f"\nConfidence: {self.confidence:.0%}")

        block = "\n".join(parts)
        if len(block) > max_chars:
            return block[:max_chars] + "\n... (truncated)"
        return block

    def get_stats(self) -> dict:
        return {
            "turns": self._turn_count,
            "active_files": len(self.active_files),
            "blockers": len(self.blockers),
            "findings": len(self.findings),
            "elapsed_s": time.time() - self._started_at,
            "confidence": self.confidence,
        }

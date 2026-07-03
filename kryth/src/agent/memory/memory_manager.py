"""MemoryManager — single entrypoint for all memory operations.

Session owns: session.memory_manager
Controllers gate all reads and writes.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from agent.memory.working_memory import WorkingMemory
from agent.memory.repo_memory import RepoMemoryManager
from agent.memory.execution_memory import ExecutionMemoryManager
from agent.memory.episodic_memory import EpisodicMemoryManager
from agent.memory.long_term_memory import LongTermMemory
from agent.memory.mutation_memory import MutationMemoryManager
from agent.memory.controllers import (
    WriteController,
    RetrievalController,
    DuplicateDetector,
    CompressionController,
    ConfidenceController,
    PolicyController,
    RetryController,
    compute_state_hash,
)


class MemoryManager:
    """Unified memory manager with 5 memory layers and 4 controllers."""

    def __init__(
        self,
        session_id: int = 0,
        long_term_db: Optional[str] = None,
    ):
        self.session_id = session_id

        # ── 5 Memory Layers ──────────────────────────────────────────────
        self.working = WorkingMemory()
        self.repo = RepoMemoryManager()
        self.execution = ExecutionMemoryManager()
        self.episodic = EpisodicMemoryManager()
        self.long_term = LongTermMemory(db_path=long_term_db)
        self.mutation = MutationMemoryManager()

        # ── 6 Controllers ────────────────────────────────────────────────
        self.write = WriteController()
        self.retrieval = RetrievalController()
        self.duplicate = DuplicateDetector()
        self.compression = CompressionController()
        self.confidence = ConfidenceController()
        self.policy = PolicyController()
        self.retry = RetryController()

        self._turn: int = 0
        self._lock = threading.RLock()

    # ── Turn management ──────────────────────────────────────────────────

    def next_turn(self) -> int:
        with self._lock:
            self._turn += 1
            self.working.touch_turn()
            if self._turn % 10 == 0:
                self._promote_to_long_term()
                self.compression.compress_episodic(self.episodic, self.session_id)
                self.compression.compress_execution(self.execution, self.session_id)
            return self._turn

    def set_objective(self, objective: str) -> None:
        self.write.set_objective(self, self.session_id, objective)

    # ── Tool result handler ──────────────────────────────────────────────

    def on_tool_result(
        self,
        tool_name: str,
        args: dict,
        result: Any,
        error: bool = False,
    ) -> None:
        self.write.on_tool_result(
            tool_name, args, result, error,
            self.session_id, self, self._turn,
        )

    # ── Duplicate detection (soft — returns memory summary, never blocks) ─

    def check_duplicate_read(self, path: str) -> Optional[dict]:
        return self.duplicate.check_read(self.repo, self.session_id, path)

    def check_duplicate_command(self, command: str, cwd: str, state_hash: str = "") -> Optional[dict]:
        sha = state_hash or (__import__("agent.memory.controllers", fromlist=["compute_state_hash"]).compute_state_hash(cwd) if cwd else "")
        return self.duplicate.check_command(self.execution, self.session_id, command, cwd, sha)

    def check_duplicate_edit(self, path: str, old_string: str, new_string: str) -> Optional[dict]:
        return self.duplicate.check_edit(self.mutation, self.session_id, path, old_string, new_string)

    # ── Policy decision ──────────────────────────────────────────────────

    def decide(
        self,
        tool_name: str,
        args: dict,
        user_override: bool = False,
    ) -> str:
        """Ask PolicyController what to do: REUSE / READ / EXECUTE / INVESTIGATE / OVERRIDE."""
        return self.policy.decide(
            self, self.session_id, tool_name, args, self._turn,
            user_override=user_override,
        )

    # ── Prompt block ─────────────────────────────────────────────────────

    def get_prompt_block(self, include_long_term: bool = False, user_input: str = "") -> str:
        return self.retrieval.build_prompt_block(
            self, self.session_id, include_long_term, user_input=user_input,
        )

    # ── Promote in-memory findings to persistent LongTermMemory ─────────

    def _promote_to_long_term(self) -> int:
        """Dump episodic findings, working findings, strategies, and
        command outcomes into the SQLite-backed LongTermMemory so they
        survive session resets and process restarts."""
        sid = self.session_id
        entries: list[dict] = []

        ep = self.episodic._get(sid) if sid in self.episodic._sessions else None
        if ep:
            for cause in ep.root_causes:
                entries.append({"value": cause, "category": "heuristic",
                                "source": "episodic_promote", "importance": 0.7})
            for s in ep.successful_strategies:
                entries.append({"value": f"{s['strategy']} — {s.get('detail', '')}",
                                "category": "heuristic", "key": "strategy",
                                "source": "episodic_promote", "importance": 0.6})
            for s in ep.failed_strategies:
                entries.append({"value": f"[DON'T] {s['strategy']} — {s.get('detail', '')}",
                                "category": "heuristic", "key": "antipattern",
                                "source": "episodic_promote", "importance": 0.5})
            for d in ep.decisions:
                entries.append({"value": d, "category": "arch",
                                "source": "episodic_promote", "importance": 0.5})
            if ep.objective:
                entries.append({"value": f"Completed task: {ep.objective}",
                                "category": "note", "key": "task",
                                "source": "episodic_promote", "importance": 0.4})

        wk = self.working
        if wk.findings:
            for f in wk.findings:
                entries.append({"value": f, "category": "note",
                                "source": "working_promote", "importance": 0.4})

        ex = self.execution._get(sid)
        if ex and ex.commands:
            failed = [c for c in ex.commands if c.exit_code != 0][-5:]
            for c in failed:
                summary = c.output_summary[:200]
                entries.append({"value": f"Command failed: {c.command} — {summary}",
                                "category": "note", "key": "command_failure",
                                "source": "execution_promote", "importance": 0.3})

        if not entries:
            return 0

        count = self.long_term.add_batch(entries)
        return count

    # ── Clear ────────────────────────────────────────────────────────────

    def clear(self) -> None:
        with self._lock:
            self._promote_to_long_term()
            self.working = WorkingMemory()
            self.repo.clear(self.session_id)
            self.execution.clear(self.session_id)
            self.episodic.clear(self.session_id)
            self.mutation.clear(self.session_id)
            self._turn = 0

    # ── Stats ────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        return {
            "working": self.working.get_stats(),
            "repo": self.repo.get_stats(self.session_id),
            "execution": self.execution.get_stats(self.session_id),
            "episodic": self.episodic.get_stats(self.session_id),
            "long_term": self.long_term.get_stats(),
            "mutation": self.mutation.get_stats(self.session_id),
            "turn": self._turn,
        }

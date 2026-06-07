"""Phase 4 — Parallel Terminal Workers.

Each worker has an isolated PersistentShell so parallel agents never
share a cwd or environment. Workers are managed by TerminalWorker and
coordinated by TerminalCoordinator (Phase 15).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from agent.terminal.shell import PersistentShell
from agent.terminal.output_parser import output_parser
from agent.terminal.log_compressor import log_compressor


@dataclass
class WorkerTask:
    task_id: str
    command: str
    timeout: int = 60
    cwd: str = ""
    label: str = ""


@dataclass
class WorkerResult:
    task_id: str
    output: str
    exit_code: int
    duration: float
    parsed_summary: str = ""
    worker_name: str = ""


class TerminalWorker:
    """An isolated terminal worker with its own PersistentShell.

    One TerminalWorker per logical agent (backend, frontend, tests, docker).
    Workers do not share shell state.
    """

    def __init__(self, name: str, cwd: str = "."):
        self.name = name
        self._shell = PersistentShell()
        self._lock = threading.Lock()
        self._busy = False
        if cwd and cwd != ".":
            self._shell.change_dir(cwd)

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def cwd(self) -> str:
        return self._shell.cwd

    def execute(
        self,
        command: str,
        timeout: int = 60,
        on_output: Callable[[str], None] | None = None,
    ) -> WorkerResult:
        with self._lock:
            self._busy = True
            start = time.monotonic()
            try:
                raw, rc = self._shell.execute(command, timeout=timeout, on_output=on_output)
                duration = time.monotonic() - start
                parsed = output_parser.parse(raw, rc)
                summary = log_compressor.compress_for_llm(raw, rc)
                return WorkerResult(
                    task_id="",
                    output=raw,
                    exit_code=rc,
                    duration=duration,
                    parsed_summary=summary,
                    worker_name=self.name,
                )
            finally:
                self._busy = False

    def cd(self, path: str) -> bool:
        return self._shell.change_dir(path)

    def set_env(self, key: str, value: str) -> None:
        self._shell.set_env(key, value)

    def close(self) -> None:
        self._shell.close()

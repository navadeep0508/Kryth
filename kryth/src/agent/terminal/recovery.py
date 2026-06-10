"""Phase 10 & 11 — Autonomous Recovery + Build/Test Loop.

RecoveryEngine: maps ParsedErrors to fix actions (install dep, fix syntax, etc.)
BuildTestLoop: generate → build → lint → test → fix → retry until success or limit.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from agent.terminal.output_parser import (
    ErrorKind,
    ParsedOutput,
    output_parser,
)
from agent.terminal.log_compressor import log_compressor


@dataclass
class RecoveryAction:
    description: str
    command: str
    confidence: float = 1.0   # 0-1, used to decide whether to auto-apply


@dataclass
class RecoveryResult:
    attempted: bool = False
    action: Optional[RecoveryAction] = None
    success: bool = False
    output: str = ""


class RecoveryEngine:
    """Map structured errors to concrete fix commands."""

    def suggest(self, parsed: ParsedOutput, cwd: str = ".") -> list[RecoveryAction]:
        actions: list[RecoveryAction] = []
        for err in parsed.errors:
            action = self._recover_error(err.kind, err.error_type, err.message, cwd)
            if action:
                actions.append(action)
        return actions

    def _recover_error(
        self, kind: ErrorKind, error_type: str, message: str, cwd: str
    ) -> Optional[RecoveryAction]:
        msg_lower = message.lower()

        # Python: missing module
        if kind == ErrorKind.PYTHON_TRACEBACK:
            m = re.search(r"No module named '([^']+)'", message)
            if m:
                pkg = m.group(1).split(".")[0].replace("_", "-")
                return RecoveryAction(
                    description=f"install missing Python package '{pkg}'",
                    command=f"pip install {pkg}",
                    confidence=0.9,
                )
            if "syntaxerror" in error_type.lower():
                return None  # syntax errors need code fix, not a command

        # Node: missing module
        if kind == ErrorKind.NODE_STACK:
            m = re.search(r"Cannot find module '([^']+)'", message)
            if m:
                pkg = m.group(1)
                if not pkg.startswith("."):
                    return RecoveryAction(
                        description=f"install missing npm package '{pkg}'",
                        command=f"npm install {pkg}",
                        confidence=0.85,
                    )

        # Rust / Cargo
        if kind in (ErrorKind.RUST_COMPILE, ErrorKind.CARGO_ERROR):
            if "could not find" in msg_lower and "in the list of available" in msg_lower:
                m = re.search(r"could not find `([^`]+)`", message)
                if m:
                    return RecoveryAction(
                        description=f"add missing Cargo dependency '{m.group(1)}'",
                        command=f"cargo add {m.group(1)}",
                        confidence=0.7,
                    )

        # npm / yarn install errors
        if kind == ErrorKind.NPM_ERROR:
            if "enotfound" in msg_lower or "network" in msg_lower:
                return RecoveryAction(
                    description="retry npm install (network error)",
                    command="npm install",
                    confidence=0.8,
                )
            if "peer dep" in msg_lower:
                return RecoveryAction(
                    description="install with legacy peer deps",
                    command="npm install --legacy-peer-deps",
                    confidence=0.75,
                )

        # Docker: image not found
        if kind == ErrorKind.DOCKER_ERROR:
            m = re.search(r"No such image: (.+)", message)
            if m:
                img = m.group(1).strip()
                return RecoveryAction(
                    description=f"pull missing docker image '{img}'",
                    command=f"docker pull {img}",
                    confidence=0.9,
                )

        # Git: unmerged files
        if kind == ErrorKind.GIT_ERROR:
            if "unmerged" in msg_lower or "conflict" in msg_lower:
                return RecoveryAction(
                    description="abort git merge to clean state",
                    command="git merge --abort",
                    confidence=0.6,
                )

        return None


@dataclass
class LoopResult:
    success: bool = False
    iterations: int = 0
    final_output: str = ""
    final_parsed: Optional[ParsedOutput] = None
    recovery_log: list[str] = field(default_factory=list)


class BuildTestLoop:
    """Run a build/test plan, detect errors, auto-recover, and retry.

    Flow: execute plan steps → parse output → suggest recovery → apply
    recovery → retry the failing step → repeat up to max_retries.
    """

    def __init__(
        self,
        run_fn: Callable[[str, int], tuple[str, int]],
        recovery: RecoveryEngine | None = None,
    ):
        """
        Args:
            run_fn: Function(command, timeout) → (output, exit_code).
                    This is typically PersistentShell.execute or run_command.
            recovery: RecoveryEngine instance. Uses module singleton if None.
        """
        self._run = run_fn
        self._recovery = recovery or RecoveryEngine()

    def run(
        self,
        steps: list,          # list of CommandStep
        cwd: str = ".",
        max_retries: int = 3,
        on_progress: Callable[[str], None] | None = None,
    ) -> LoopResult:
        from agent.terminal.command_planner import CommandStep

        result = LoopResult()
        log = result.recovery_log

        def _notify(msg: str) -> None:
            log.append(msg)
            if on_progress:
                on_progress(msg)

        for step in steps:
            if isinstance(step, dict):
                cmd = step.get("command", "")
                timeout = step.get("timeout", 60)
                label = step.get("label", cmd[:40])
                required = step.get("required", True)
            else:
                cmd = step.command
                timeout = step.timeout
                label = step.label
                required = step.required

            _notify(f"→ {label}: {cmd}")
            retries_left = max_retries

            while True:
                raw, rc = self._run(cmd, timeout)
                parsed = output_parser.parse(raw, rc)

                if parsed.success:
                    _notify(f"  ✓ {label} passed")
                    result.final_output = raw
                    result.final_parsed = parsed
                    break

                compressed = log_compressor.compress(raw, rc)
                _notify(f"  ✗ {label} failed: {compressed.error}")

                if retries_left <= 0:
                    if not required:
                        _notify(f"  (skipping optional step: {label})")
                        break
                    result.success = False
                    result.iterations = max_retries + 1
                    result.final_output = raw
                    result.final_parsed = parsed
                    return result

                # Attempt recovery
                actions = self._recovery.suggest(parsed, cwd)
                if actions:
                    action = actions[0]
                    _notify(f"  ↻ recovery: {action.description}")
                    rec_out, rec_rc = self._run(action.command, 120)
                    if rec_rc == 0:
                        _notify(f"  ✓ recovery succeeded")
                    else:
                        _notify(f"  ✗ recovery failed (rc={rec_rc})")
                else:
                    _notify(f"  (no automatic recovery available)")
                    if required:
                        result.final_output = raw
                        result.final_parsed = parsed
                        return result
                    break

                retries_left -= 1
                result.iterations += 1
                _notify(f"  retrying ({max_retries - retries_left}/{max_retries})…")

        result.success = True
        return result


# Module-level singleton used by terminal/__init__.py
recovery_engine = RecoveryEngine()

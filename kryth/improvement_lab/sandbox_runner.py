"""Applies FileChange objects to a sandbox worktree using exact string substitution.

Safety rules:
  - Only touches files inside the sandbox path (never the main worktree)
  - Each change is a reversible string replacement (old → new)
  - Validates every change after applying it
  - Can revert all changes if validation fails
"""

from __future__ import annotations

import logging
from pathlib import Path

from improvement_lab.experiment_generator import FileChange

logger = logging.getLogger("improvement_lab.sandbox_runner")


class SandboxError(RuntimeError):
    pass


class SandboxRunner:
    """Applies and validates file changes inside a git worktree sandbox."""

    def apply(self, sandbox_path: Path, changes: list[FileChange]) -> None:
        """Apply all changes. Raises SandboxError if any fragment is not found."""
        for change in changes:
            self._apply_one(sandbox_path, change)
        self.validate(sandbox_path, changes)
        logger.info(f"Applied {len(changes)} change(s) to {sandbox_path}")

    def validate(self, sandbox_path: Path, changes: list[FileChange]) -> None:
        """Verify every new_fragment is present in its target file."""
        for change in changes:
            file_path = self._resolve(sandbox_path, change.relative_path)
            content = file_path.read_text(encoding="utf-8")
            if change.new_fragment not in content:
                raise SandboxError(
                    f"Validation failed for {change.relative_path}: "
                    f"new_fragment not found after apply.\n"
                    f"Expected: {change.new_fragment[:120]!r}"
                )

    def revert(self, sandbox_path: Path, changes: list[FileChange]) -> None:
        """Reverse all changes (new → old).  Best-effort: logs but does not raise."""
        for change in reversed(changes):
            try:
                self._apply_one(
                    sandbox_path,
                    FileChange(
                        relative_path=change.relative_path,
                        old_fragment=change.new_fragment,
                        new_fragment=change.old_fragment,
                        description=f"REVERT: {change.description}",
                    ),
                )
            except Exception as exc:
                logger.warning(f"Revert partial failure for {change.relative_path}: {exc}")
        logger.info(f"Reverted {len(changes)} change(s) in {sandbox_path}")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _apply_one(self, sandbox_path: Path, change: FileChange) -> None:
        file_path = self._resolve(sandbox_path, change.relative_path)

        if not file_path.exists():
            raise SandboxError(
                f"Target file does not exist in sandbox: {change.relative_path}"
            )

        content = file_path.read_text(encoding="utf-8")

        if change.old_fragment not in content:
            raise SandboxError(
                f"Fragment not found in {change.relative_path}:\n"
                f"{change.old_fragment[:200]!r}"
            )

        new_content = content.replace(change.old_fragment, change.new_fragment, 1)
        file_path.write_text(new_content, encoding="utf-8")
        logger.debug(f"Changed {change.relative_path}: {change.description}")

    @staticmethod
    def _resolve(sandbox_path: Path, relative_path: str) -> Path:
        """Resolve and validate the path stays inside the sandbox."""
        resolved = (sandbox_path / relative_path).resolve()
        if not str(resolved).startswith(str(sandbox_path.resolve())):
            raise SandboxError(
                f"Path traversal detected: {relative_path!r} escapes sandbox"
            )
        return resolved

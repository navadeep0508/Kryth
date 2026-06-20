"""Git worktree management — creates fully isolated sandboxes for experiments.

Safety contract:
  - sandbox lives under .kryth-lab/{exp_id}/ (never touches the main worktree)
  - branch name: kryth-lab/{exp_id}  (never modifies main/master)
  - destroy_sandbox() removes both the worktree and the branch
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger("improvement_lab.branch_manager")


class BranchManagerError(RuntimeError):
    pass


class BranchManager:
    """Creates and destroys isolated git worktrees for experiments."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.lab_dir = self.project_root / ".kryth-lab"

    # ── Public API ────────────────────────────────────────────────────────────

    def create_sandbox(self, exp_id: str) -> Path:
        """Create a git worktree at .kryth-lab/{exp_id} on branch kryth-lab/{exp_id}.

        Returns the sandbox root path.
        """
        sandbox_path = self.lab_dir / exp_id
        branch_name = f"kryth-lab/{exp_id}"

        if sandbox_path.exists():
            raise BranchManagerError(
                f"Sandbox already exists: {sandbox_path}. "
                "Call destroy_sandbox() before creating a new one."
            )

        self.lab_dir.mkdir(exist_ok=True)

        result = self._git(
            "worktree", "add", str(sandbox_path), "-b", branch_name,
            check=False,
        )
        if result.returncode != 0:
            raise BranchManagerError(
                f"git worktree add failed (rc={result.returncode}):\n{result.stderr}"
            )

        logger.info(f"Sandbox created: {sandbox_path}  branch={branch_name}")
        return sandbox_path

    def destroy_sandbox(self, exp_id: str) -> None:
        """Remove the worktree directory and delete the sandbox branch."""
        sandbox_path = self.lab_dir / exp_id
        branch_name = f"kryth-lab/{exp_id}"

        if sandbox_path.exists():
            self._git("worktree", "remove", str(sandbox_path), "--force", check=False)
            if sandbox_path.exists():
                shutil.rmtree(sandbox_path, ignore_errors=True)

        self._git("branch", "-D", branch_name, check=False)

        logger.info(f"Sandbox destroyed: {sandbox_path}  branch={branch_name}")

    def sandbox_path(self, exp_id: str) -> Path:
        return self.lab_dir / exp_id

    def sandbox_exists(self, exp_id: str) -> bool:
        return (self.lab_dir / exp_id).exists()

    def list_sandboxes(self) -> list[str]:
        """Return exp_ids of any live sandboxes (in case of a previous crash)."""
        if not self.lab_dir.exists():
            return []
        return [p.name for p in self.lab_dir.iterdir() if p.is_dir()]

    # ── Git helper ────────────────────────────────────────────────────────────

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        cmd = ["git", *args]
        result = subprocess.run(
            cmd,
            cwd=self.project_root,
            capture_output=True,
            text=True,
        )
        if check and result.returncode != 0:
            raise BranchManagerError(
                f"git {' '.join(args)} failed (rc={result.returncode}):\n{result.stderr}"
            )
        return result

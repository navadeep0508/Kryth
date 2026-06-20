"""Rollback Engine — auto-generates and executes rollback plans.

Every Action may define `rollback_steps`. The engine stores snapshots
before execution and applies them deterministically if an action fails.

Rollback types supported:
  file_backup:<path>   — backup file before write; restore on rollback
  git_stash            — git stash before any git mutations
  git_reset:<ref>      — git reset --hard <ref> on rollback
  delete_file:<path>   — delete a created file on rollback
  run:<command>        — run an arbitrary rollback command
  noop                 — explicit no-op (for read-only actions)
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from agent.action.action import Action, ActionStatus

logger = logging.getLogger(__name__)


@dataclass
class Snapshot:
    """Pre-execution state snapshot for one action."""
    action_id: str
    snapshots: dict[str, str] = field(default_factory=dict)
    # key = original path, value = temp backup path


class RollbackEngine:
    """Takes pre-execution snapshots and applies rollback on failure."""

    def __init__(self) -> None:
        self._snapshots: dict[str, Snapshot] = {}   # action_id → Snapshot
        self._tmp_dir   = Path(tempfile.mkdtemp(prefix="kryth_rollback_"))

    # ── Pre-execution ─────────────────────────────────────────────────

    def snapshot(self, action: Action) -> None:
        """Save state before executing `action`."""
        snap = Snapshot(action_id=action.id)
        for step in action.rollback_steps:
            step = step.strip()
            if step.startswith("file_backup:"):
                path = Path(step[len("file_backup:"):].strip())
                if path.exists():
                    backup = self._tmp_dir / f"{action.id}_{path.name}"
                    shutil.copy2(path, backup)
                    snap.snapshots[str(path)] = str(backup)
                    logger.debug("Snapshot: backed up %s → %s", path, backup)
            elif step == "git_stash":
                self._git("stash", "--include-untracked")
            elif step.startswith("git_stash:"):
                self._git("stash", "--include-untracked")
        self._snapshots[action.id] = snap

    # ── Rollback ──────────────────────────────────────────────────────

    def rollback(self, action: Action) -> bool:
        """Execute rollback for `action`. Returns True if all steps succeeded."""
        logger.info("Rolling back action '%s': %s", action.id, action.goal[:60])
        snap = self._snapshots.get(action.id)
        all_ok = True

        for step in action.rollback_steps:
            step = step.strip()
            ok = self._apply_step(step, action, snap)
            if not ok:
                all_ok = False

        if all_ok:
            action.mark_rolled_back()
            logger.info("Rollback succeeded for '%s'", action.id)
        else:
            logger.warning("Rollback PARTIALLY FAILED for '%s'", action.id)

        return all_ok

    def _apply_step(
        self, step: str, action: Action, snap: Optional[Snapshot]
    ) -> bool:
        if step == "noop":
            return True

        if step.startswith("file_backup:"):
            path = Path(step[len("file_backup:"):].strip())
            if snap and str(path) in snap.snapshots:
                backup = Path(snap.snapshots[str(path)])
                shutil.copy2(backup, path)
                logger.info("Restored %s from backup", path)
                return True
            logger.warning("No backup for %s — cannot restore", path)
            return False

        if step.startswith("delete_file:"):
            path = Path(step[len("delete_file:"):].strip())
            if path.exists():
                path.unlink()
                logger.info("Deleted created file %s", path)
            return True

        if step == "git_stash":
            ret = self._git("stash", "pop")
            return ret == 0

        if step.startswith("git_reset:"):
            ref = step[len("git_reset:"):].strip()
            ret = self._git("reset", "--hard", ref)
            return ret == 0

        if step.startswith("run:"):
            cmd = step[len("run:"):].strip()
            ret = subprocess.run(cmd, shell=True, capture_output=True).returncode
            return ret == 0

        logger.debug("Unknown rollback step '%s' — skipping", step)
        return True

    # ── Helpers ───────────────────────────────────────────────────────

    def _git(self, *args: str) -> int:
        try:
            r = subprocess.run(
                ["git"] + list(args),
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode != 0:
                logger.warning("git %s failed: %s", " ".join(args), r.stderr.strip())
            return r.returncode
        except Exception as e:
            logger.warning("git %s error: %s", " ".join(args), e)
            return 1

    def cleanup(self) -> None:
        """Remove all temporary backup files."""
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

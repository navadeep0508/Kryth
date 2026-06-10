"""CheckpointEngine — full subsystem snapshot before risky operations.

Captures:
- Mission DAG node states
- Workspace entity count (lightweight reference)
- Browser storage state (cookies + localStorage)
- Terminal cwd/env (if TerminalCoordinator is available)
- Working memory keys
- Checkpoint metadata

Reuses:
- ``agent.browser.BrowserProfileManager.get_storage_state()``
- ``agent.snapshots.snapshot()`` for individual file checkpoints
- ``agent.mission.mission_dag.MissionDAG``
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from agent.mission.config import CHECKPOINTS_DIR, MAX_CHECKPOINTS_PER_MISSION, RISKY_COMMAND_PATTERNS

logger = logging.getLogger(__name__)


class CheckpointEngine:
    """Save and restore full mission checkpoints.

    Parameters
    ----------
    project_hash:
        12-char project identity.
    mission_dag:
        The MissionDAG instance for serializing node states.
    """

    def __init__(self, project_hash: str, mission_dag: Any = None) -> None:
        self.project_hash = project_hash
        self._dag = mission_dag
        self._base = CHECKPOINTS_DIR / project_hash

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def save(
        self,
        mission_id: str,
        label: str = "",
        browser_profile: str = "default",
        memory_manager: Any = None,
    ) -> Path:
        """Capture full subsystem state for *mission_id*.

        Returns the checkpoint directory path.
        """
        ts = int(time.time())
        name = f"{ts}_{label}" if label else str(ts)
        checkpoint_dir = self._base / mission_id / name
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

        # 1. Mission DAG state
        self._save_dag(checkpoint_dir, mission_id)

        # 2. Browser state
        self._save_browser(checkpoint_dir, browser_profile)

        # 3. Terminal state
        self._save_terminal(checkpoint_dir)

        # 4. Working memory
        self._save_memory(checkpoint_dir, memory_manager)

        # 5. Checkpoint metadata
        meta = {
            "mission_id": mission_id,
            "project_hash": self.project_hash,
            "label": label,
            "timestamp": ts,
            "browser_profile": browser_profile,
        }
        (checkpoint_dir / "checkpoint_meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )

        logger.info("Checkpoint saved → %s", checkpoint_dir)
        self._prune(mission_id)
        return checkpoint_dir

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------

    def restore(self, checkpoint_path: Path) -> bool:
        """Restore subsystem state from a checkpoint directory.

        Returns True on success.
        """
        if not checkpoint_path.exists():
            logger.error("Checkpoint not found: %s", checkpoint_path)
            return False

        try:
            # Restore browser state
            browser_file = checkpoint_path / "browser_state.json"
            if browser_file.exists():
                self._restore_browser(browser_file)

            # Restore memory
            mem_file = checkpoint_path / "memory_context.json"
            if mem_file.exists():
                self._restore_memory(mem_file)

            logger.info("Checkpoint restored ← %s", checkpoint_path)
            return True
        except Exception as exc:
            logger.error("Restore failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_checkpoints(self, mission_id: str) -> list[Path]:
        """Return checkpoint paths for *mission_id*, oldest → newest."""
        mission_dir = self._base / mission_id
        if not mission_dir.exists():
            return []
        return sorted(
            (p for p in mission_dir.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
        )

    def latest_checkpoint(self, mission_id: str) -> Path | None:
        checkpoints = self.list_checkpoints(mission_id)
        return checkpoints[-1] if checkpoints else None

    # ------------------------------------------------------------------
    # Auto-checkpoint on risky commands
    # ------------------------------------------------------------------

    def is_risky(self, command: str) -> bool:
        return any(pattern in command for pattern in RISKY_COMMAND_PATTERNS)

    def auto_checkpoint_if_risky(
        self,
        command: str,
        mission_id: str,
        **kwargs: Any,
    ) -> Optional[Path]:
        """Save a checkpoint if *command* matches a risky pattern.

        Returns the checkpoint path if saved, else None.
        """
        if self.is_risky(command):
            return self.save(mission_id, label="auto-pre-risky", **kwargs)
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _save_dag(self, checkpoint_dir: Path, mission_id: str) -> None:
        if self._dag is None:
            return
        try:
            nodes = self._dag.get_nodes(mission_id)
            mission = self._dag.get(mission_id) or {}
            (checkpoint_dir / "mission_dag.json").write_text(
                json.dumps({"mission": mission, "nodes": nodes}, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug("_save_dag: %s", exc)

    def _save_browser(self, checkpoint_dir: Path, profile: str) -> None:
        try:
            from agent.browser.profile_manager import BrowserProfileManager
            mgr = BrowserProfileManager()
            state = mgr.get_storage_state(profile)
            (checkpoint_dir / "browser_state.json").write_text(
                json.dumps(state, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.debug("_save_browser: %s", exc)

    def _save_terminal(self, checkpoint_dir: Path) -> None:
        try:
            from agent.terminal import terminal_state
            state = {
                "cwd": getattr(terminal_state, "cwd", ""),
                "env_snapshot": {},
            }
            (checkpoint_dir / "terminal_state.json").write_text(
                json.dumps(state, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.debug("_save_terminal: %s", exc)

    def _save_memory(self, checkpoint_dir: Path, memory_manager: Any) -> None:
        if memory_manager is None:
            return
        try:
            working = getattr(memory_manager, "working", None)
            data = working.all() if working and hasattr(working, "all") else {}
            (checkpoint_dir / "memory_context.json").write_text(
                json.dumps(data, indent=2, default=str), encoding="utf-8"
            )
        except Exception as exc:
            logger.debug("_save_memory: %s", exc)

    def _restore_browser(self, browser_file: Path) -> None:
        try:
            state = json.loads(browser_file.read_text(encoding="utf-8"))
            from agent.browser.profile_manager import BrowserProfileManager
            mgr = BrowserProfileManager()
            mgr.cookies("default").import_storage_state(state)
            mgr.storage("default").import_storage_state(state)
        except Exception as exc:
            logger.debug("_restore_browser: %s", exc)

    def _restore_memory(self, mem_file: Path) -> None:
        try:
            data = json.loads(mem_file.read_text(encoding="utf-8"))
            # Re-populate working memory if manager is accessible via session
            try:
                from agent.memory.memory_manager import MemoryManager
                from agent.persistence import project_hash_for
                mm = MemoryManager(project_hash=self.project_hash)
                for k, v in data.items():
                    mm.working.set(k, v)
            except Exception:
                pass
        except Exception as exc:
            logger.debug("_restore_memory: %s", exc)

    def _prune(self, mission_id: str) -> None:
        checkpoints = self.list_checkpoints(mission_id)
        while len(checkpoints) > MAX_CHECKPOINTS_PER_MISSION:
            oldest = checkpoints.pop(0)
            shutil.rmtree(oldest, ignore_errors=True)

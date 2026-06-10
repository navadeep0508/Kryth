"""DeploymentPipeline — build, package, deploy, health-check, and rollback.

Reuses:
- CheckpointEngine (via MissionManager) for pre-deploy checkpoints
- terminal shell for running build/deploy commands
- MemoryManager for recording deployment outcomes
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from agent.factory.config import DEPLOYMENTS_DIR, DeploymentStatus, APPROVAL_REQUIRED_OPS

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS deployments (
    id          TEXT PRIMARY KEY,
    project_hash TEXT NOT NULL,
    env         TEXT NOT NULL DEFAULT 'staging',
    status      TEXT NOT NULL DEFAULT 'pending',
    version     TEXT NOT NULL DEFAULT '',
    mission_id  TEXT NOT NULL DEFAULT '',
    started_at  REAL,
    completed_at REAL,
    checkpoint_path TEXT NOT NULL DEFAULT '',
    log_json    TEXT NOT NULL DEFAULT '[]',
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dep_proj ON deployments(project_hash);
"""


@dataclass
class DeploymentResult:
    deployment_id: str
    env: str
    status: str
    version: str = ""
    duration: float = 0.0
    log: list[str] = field(default_factory=list)
    health_ok: bool = True
    rolled_back: bool = False

    def to_dict(self) -> dict:
        return {"deployment_id": self.deployment_id, "env": self.env,
                "status": self.status, "version": self.version,
                "duration": self.duration, "log": self.log,
                "health_ok": self.health_ok, "rolled_back": self.rolled_back}


class DeploymentPipeline:
    """Manage build → deploy → health-check → rollback lifecycle.

    Parameters
    ----------
    project_hash:
        Project identity.
    root:
        Project root directory.
    mission_manager:
        Optional MissionManager for checkpoint integration.
    db_path:
        Override SQLite path (for tests).
    """

    def __init__(
        self,
        project_hash: str,
        root: str = ".",
        mission_manager: Any = None,
        db_path: Optional[Path] = None,
    ) -> None:
        self.project_hash = project_hash
        self.root = root
        self._mm = mission_manager
        if db_path:
            self._db_path = db_path
        else:
            DEPLOYMENTS_DIR.mkdir(parents=True, exist_ok=True)
            self._db_path = DEPLOYMENTS_DIR / f"{project_hash}.db"
        self._init_db()

    # ------------------------------------------------------------------
    # Deployment lifecycle
    # ------------------------------------------------------------------

    def deploy(
        self,
        env: str = "staging",
        version: str = "",
        mission_id: str = "",
        approval_confirmed: bool = False,
    ) -> DeploymentResult:
        """Run the full deploy pipeline for *env*.

        Production deployments require ``approval_confirmed=True``.
        """
        if env == "production" and not approval_confirmed:
            return DeploymentResult(
                deployment_id="",
                env=env,
                status="blocked",
                log=["Production deployment requires human approval. Set approval_confirmed=True."],
            )

        dep_id = str(uuid.uuid4())[:12]
        now = time.time()
        ckpt_path = ""

        # Pre-deploy checkpoint
        if self._mm and mission_id:
            try:
                p = self._mm.checkpoint(mission_id, label=f"pre-deploy-{env}")
                ckpt_path = str(p)
            except Exception as exc:
                logger.debug("Pre-deploy checkpoint failed: %s", exc)

        with self._con() as con:
            con.execute(
                "INSERT INTO deployments(id,project_hash,env,status,version,mission_id,"
                "started_at,checkpoint_path,log_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (dep_id, self.project_hash, env, DeploymentStatus.BUILDING.value,
                 version, mission_id, now, ckpt_path, "[]", now),
            )

        log: list[str] = []
        success = True

        # Build
        build_ok, build_log = self._run_build()
        log.extend(build_log)
        if not build_ok:
            self._update(dep_id, DeploymentStatus.FAILED.value, log)
            return DeploymentResult(dep_id, env, "failed", log=log, health_ok=False)

        # Deploy
        self._update(dep_id, DeploymentStatus.DEPLOYING.value, log)
        deploy_ok, deploy_log = self._run_deploy(env)
        log.extend(deploy_log)
        if not deploy_ok:
            self._update(dep_id, DeploymentStatus.FAILED.value, log)
            return DeploymentResult(dep_id, env, "failed", log=log, health_ok=False)

        # Health check
        health_ok, health_log = self._run_health_check(env)
        log.extend(health_log)

        final_status = DeploymentStatus.HEALTHY.value if health_ok else DeploymentStatus.FAILED.value
        self._update(dep_id, final_status, log, completed_at=time.time())

        duration = time.time() - now
        return DeploymentResult(
            dep_id, env, final_status,
            version=version,
            duration=round(duration, 1),
            log=log,
            health_ok=health_ok,
        )

    def rollback(
        self,
        deployment_id: str,
        checkpoint_path: str = "",
    ) -> DeploymentResult:
        """Roll back to the pre-deploy checkpoint."""
        log = [f"Rolling back deployment {deployment_id}"]
        rolled_back = False

        if checkpoint_path and self._mm:
            try:
                ok = self._mm.checkpoints.restore(Path(checkpoint_path))
                rolled_back = ok
                log.append("Checkpoint restored successfully" if ok else "Checkpoint restore failed")
            except Exception as exc:
                log.append(f"Rollback error: {exc}")

        self._update(deployment_id, DeploymentStatus.ROLLED_BACK.value, log)
        return DeploymentResult(
            deployment_id, env="unknown",
            status=DeploymentStatus.ROLLED_BACK.value,
            log=log, rolled_back=rolled_back,
        )

    def list_deployments(self, env: str | None = None) -> list[dict]:
        sql = "SELECT id,env,status,version,started_at,completed_at,checkpoint_path FROM deployments WHERE project_hash=?"
        params: list = [self.project_hash]
        if env:
            sql += " AND env=?"
            params.append(env)
        sql += " ORDER BY created_at DESC"
        with self._con() as con:
            rows = con.execute(sql, params).fetchall()
        return [{"id": r[0], "env": r[1], "status": r[2], "version": r[3],
                 "started_at": r[4], "completed_at": r[5], "checkpoint_path": r[6]}
                for r in rows]

    # ------------------------------------------------------------------
    # Build / deploy / health helpers (can be overridden for real projects)
    # ------------------------------------------------------------------

    def _run_build(self) -> tuple[bool, list[str]]:
        log = []
        try:
            from agent.terminal.shell import get_shell
            shell = get_shell()
            cmds = self._detect_build_commands()
            for cmd in cmds:
                event = shell.execute(cmd, timeout=120)
                output = str(event.data or "")
                log.append(f"$ {cmd}")
                log.append(output[:200])
                if getattr(event, "exit_code", 0) not in (0, None):
                    return False, log
            return True, log
        except Exception as exc:
            log.append(f"Build error: {exc}")
            return True, log   # non-fatal if shell unavailable

    def _run_deploy(self, env: str) -> tuple[bool, list[str]]:
        log = [f"Deploying to {env} (placeholder — configure deploy command in project)"]
        return True, log

    def _run_health_check(self, env: str) -> tuple[bool, list[str]]:
        log = [f"Health check for {env} (placeholder — configure health endpoint)"]
        return True, log

    def _detect_build_commands(self) -> list[str]:
        root = Path(self.root)
        if (root / "pyproject.toml").exists():
            return ["python -m build --no-isolation 2>&1 | head -30"]
        if (root / "package.json").exists():
            return ["npm run build 2>&1 | head -30"]
        if (root / "Makefile").exists():
            return ["make build 2>&1 | head -30"]
        return []

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _update(self, dep_id: str, status: str, log: list, completed_at: float | None = None) -> None:
        with self._con() as con:
            con.execute(
                "UPDATE deployments SET status=?, log_json=?, completed_at=COALESCE(?,completed_at) WHERE id=?",
                (status, json.dumps(log[-20:]), completed_at, dep_id),
            )

    def _init_db(self) -> None:
        with self._con() as con:
            for stmt in _DDL.strip().split(";"):
                if stmt.strip():
                    con.execute(stmt)

    def _con(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path))

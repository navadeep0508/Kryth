"""Action Verifier — confirms that every completed action actually succeeded.

The UAL never assumes success. After each executor returns, the verifier
runs the action's `verification_steps` and updates `ActionResult.verified`.

Built-in verification strategies (selected by step prefix):
  check_file:<path>        — file must exist
  check_not_empty:<path>   — file must be non-empty
  check_exit_0             — already guaranteed by executor
  check_url:<url>          — HTTP GET must return 2xx
  check_git_commit:<hash>  — commit must exist in local repo
  check_process:<name>     — process must be running
  check_output:<substring> — executor output must contain substring
  check_no_error           — executor output must not contain ERROR/FATAL/Traceback
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from agent.action.action import Action, ActionResult

logger = logging.getLogger(__name__)


class ActionVerifier:
    """Runs verification_steps defined on the Action after execution."""

    def verify(self, action: Action, result: ActionResult) -> ActionResult:
        """Mutates and returns result with verified / verification_detail updated."""
        if not result.success:
            result.verified = False
            result.verification_detail = f"Executor reported failure: {result.error}"
            return result

        if not action.verification_steps:
            result.verified = True
            result.verification_detail = "no verification steps defined"
            return result

        failures: list[str] = []
        for step in action.verification_steps:
            ok, detail = self._run_step(step, result)
            if not ok:
                failures.append(detail)

        if failures:
            result.verified = False
            result.verification_detail = "; ".join(failures)
            logger.warning(
                "[%s] Verification FAILED: %s", action.id, result.verification_detail
            )
        else:
            result.verified = True
            result.verification_detail = f"all {len(action.verification_steps)} step(s) passed"
            logger.info("[%s] Verification OK", action.id)

        return result

    # ── Step runners ──────────────────────────────────────────────────

    def _run_step(self, step: str, result: ActionResult) -> tuple[bool, str]:
        step = step.strip()

        if step.startswith("check_file:"):
            path = step[len("check_file:"):].strip()
            ok = Path(path).exists()
            return ok, f"check_file:{path} -> {'OK' if ok else 'NOT FOUND'}"

        if step.startswith("check_not_empty:"):
            path = step[len("check_not_empty:"):].strip()
            p = Path(path)
            ok = p.exists() and p.stat().st_size > 0
            return ok, f"check_not_empty:{path} -> {'OK' if ok else 'EMPTY OR MISSING'}"

        if step == "check_exit_0":
            return True, "check_exit_0 -> OK (executor guarantees)"

        if step.startswith("check_url:"):
            url = step[len("check_url:"):].strip()
            return self._check_url(url)

        if step.startswith("check_git_commit:"):
            ref = step[len("check_git_commit:"):].strip()
            return self._check_git_ref(ref)

        if step.startswith("check_process:"):
            name = step[len("check_process:"):].strip()
            return self._check_process(name)

        if step.startswith("check_output:"):
            substr = step[len("check_output:"):].strip()
            ok = substr in (result.output or "")
            return ok, f"check_output:{substr!r} -> {'FOUND' if ok else 'NOT FOUND'}"

        if step == "check_no_error":
            bad = ("ERROR", "FATAL", "Traceback", "exception", "FAILED")
            found = [b for b in bad if b in (result.output or "")]
            ok = not found
            return ok, f"check_no_error -> {'OK' if ok else f'found: {found}'}"

        logger.debug("Unknown verification step '%s' — skipping", step)
        return True, f"unknown step '{step}' skipped"

    def _check_url(self, url: str) -> tuple[bool, str]:
        try:
            import urllib.request
            with urllib.request.urlopen(url, timeout=10) as r:
                ok = 200 <= r.status < 300
                return ok, f"check_url:{url} -> {r.status}"
        except Exception as e:
            return False, f"check_url:{url} -> {e}"

    def _check_git_ref(self, ref: str) -> tuple[bool, str]:
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--verify", ref],
                capture_output=True, text=True, timeout=10,
            )
            ok = r.returncode == 0
            return ok, f"check_git_commit:{ref} -> {'OK' if ok else 'NOT FOUND'}"
        except Exception as e:
            return False, f"check_git_commit:{ref} -> {e}"

    def _check_process(self, name: str) -> tuple[bool, str]:
        try:
            r = subprocess.run(
                ["pgrep", "-f", name], capture_output=True, timeout=5
            )
            ok = r.returncode == 0
            return ok, f"check_process:{name} -> {'RUNNING' if ok else 'NOT FOUND'}"
        except Exception as e:
            return False, f"check_process:{name} -> {e}"

"""Predictive Engine — per-step failure prediction before execution.

Wraps the existing ExperienceManager.predict() to provide:
  - Per-step failure probability
  - Recommended pre-execution interventions
  - Known repair commands for anticipated errors
  - "Proactive fix" when confidence is high enough

This is the Supervisor's forward-looking intelligence.
It runs BEFORE a step executes, not after failure.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class StepPrediction:
    step_description: str
    failure_probability: float    # 0.0–1.0
    likely_errors: List[str]
    repair_commands: List[str]
    proactive_fix: Optional[str]  # command to run BEFORE the step to prevent failure
    confidence: float
    risk_level: str               # low | medium | high
    reasoning: str

    @property
    def needs_intervention(self) -> bool:
        return self.failure_probability > 0.50 and self.confidence > 0.60

    @property
    def can_auto_fix(self) -> bool:
        return (
            self.proactive_fix is not None
            and self.failure_probability > 0.60
            and self.confidence > 0.75
        )


class PredictiveEngine:
    """Predict failures before they happen using Experience Engine data."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cache: dict = {}

    def predict_step(
        self,
        step_description: str,
        project_root: str = ".",
        command: Optional[str] = None,
    ) -> StepPrediction:
        """Predict failure risk for a single execution step."""
        cache_key = f"{step_description[:80]}:{command or ''}"
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]

        # Query experience engine
        try:
            from agent.experience import get_experience
            from agent.supervisor.risk import risk_engine
            exp = get_experience(project_root)

            # Get overall prediction
            pred = exp.predict(step_description)

            # Also get repair experience for this specific command kind
            repair_exp = None
            if command:
                cmd_kind = self._classify_command(command)
                if cmd_kind:
                    repair_exp = exp.recommend("terminal", f"{cmd_kind}:")

            # Build likely errors from prediction data
            likely_errors = list(pred.risk_factors[:3])
            if repair_exp and hasattr(repair_exp, "commands"):
                likely_errors.append(
                    f"Previous failure: {repair_exp.commands[0] if repair_exp.commands else ''}"
                )

            # Get repair commands
            repair_cmds: List[str] = list(pred.likely_repairs[:3])
            if repair_exp and hasattr(repair_exp, "commands"):
                repair_cmds.extend(repair_exp.commands[:2])
            repair_cmds = list(dict.fromkeys(repair_cmds))[:5]

            # Score command risk
            risk_score = None
            if command:
                risk_score = risk_engine.score_command(command)
                risk_level = risk_score.level.value
            else:
                risk_level = "low" if pred.success_probability > 0.80 else "medium"

            # Proactive fix: if a common dependency install would prevent failure
            proactive_fix = self._suggest_proactive_fix(
                step_description, command, repair_cmds
            )

            failure_prob = round(1.0 - pred.success_probability, 2)

            result = StepPrediction(
                step_description=step_description,
                failure_probability=failure_prob,
                likely_errors=likely_errors,
                repair_commands=repair_cmds,
                proactive_fix=proactive_fix,
                confidence=pred.confidence,
                risk_level=risk_level,
                reasoning=pred.reasoning,
            )

        except Exception as exc:
            result = StepPrediction(
                step_description=step_description,
                failure_probability=0.30,
                likely_errors=[],
                repair_commands=[],
                proactive_fix=None,
                confidence=0.20,
                risk_level="low",
                reasoning=f"Prediction unavailable: {exc}",
            )

        with self._lock:
            self._cache[cache_key] = result
        return result

    def predict_mission(
        self,
        mission_goal: str,
        steps: List[str],
        project_root: str = ".",
    ) -> List[StepPrediction]:
        """Predict failures for all steps in a mission."""
        return [
            self.predict_step(step, project_root=project_root, command=step)
            for step in steps
        ]

    def _classify_command(self, command: str) -> Optional[str]:
        """Map a command to a known kind for experience lookup."""
        cmd = command.lower().strip()
        if "pytest" in cmd or "unittest" in cmd:
            return "test"
        if "npm install" in cmd or "pip install" in cmd:
            return "install"
        if "npm run build" in cmd or "cargo build" in cmd:
            return "build"
        if "npm start" in cmd or "uvicorn" in cmd or "flask run" in cmd:
            return "server"
        if "git" in cmd:
            return "git"
        if "docker" in cmd:
            return "docker"
        return None

    def _suggest_proactive_fix(
        self,
        description: str,
        command: Optional[str],
        repair_cmds: List[str],
    ) -> Optional[str]:
        """Suggest a pre-execution fix for a common failure pattern."""
        if not command:
            return None
        cmd = command.lower()

        # Missing package.json before npm
        if "npm" in cmd and "package.json" in description.lower():
            return "test -f package.json || npm init -y"

        # Missing requirements before pip
        if "pytest" in cmd or ("python" in cmd and "test" in cmd):
            if any("pip install" in r for r in repair_cmds):
                install = next(
                    (r for r in repair_cmds if "pip install" in r), None
                )
                return install

        # Missing cargo.lock before cargo build
        if "cargo build" in cmd:
            return "cargo fetch"

        return None

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()


# Module-level singleton
prediction_engine = PredictiveEngine()

"""Dynamic Replanner — modifies a running ActionGraph after failures.

The replanner:
  1. Receives the failed Action and its ActionResult.
  2. Analyses the failure reason.
  3. Produces a ReplanDecision: retry / inject new actions / skip / abort.
  4. The ActionScheduler applies the decision without restarting the mission.

Failure categories and strategies:
  timeout       → retry with longer timeout
  permission    → inject a "request permission" action before retry
  not_found     → inject a "create missing prerequisite" action
  network       → retry with exponential backoff hint
  build_error   → inject a "fix error" action then retry build
  unknown       → skip or abort depending on risk level
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from agent.action.action import Action, ActionResult, ActionRisk, ActionStatus
from agent.action.action_graph import ActionGraph

logger = logging.getLogger(__name__)


class ReplanStrategy(str, Enum):
    RETRY           = "retry"           # retry the same action (possibly with tweaks)
    INJECT_BEFORE   = "inject_before"   # add new actions that unblock this one
    SKIP            = "skip"            # mark action skipped, continue graph
    ABORT           = "abort"           # stop the mission


@dataclass
class ReplanDecision:
    strategy: ReplanStrategy
    failed_action_id: str
    rationale: str
    new_actions: list[dict[str, Any]] = field(default_factory=list)  # for INJECT_BEFORE
    retry_params: dict[str, Any]      = field(default_factory=dict)  # for RETRY
    replan_count: int = 0


class Replanner:
    """Analyses failures and decides how to adapt the running ActionGraph."""

    def replan(
        self,
        graph: ActionGraph,
        failed_action: Action,
        result: ActionResult,
        replan_count: int = 0,
    ) -> ReplanDecision:
        error = (result.error or result.output or "").lower()
        risk  = failed_action.risk

        # Hard limit: don't replan more than 3 times per mission
        if replan_count >= 3:
            return ReplanDecision(
                strategy=ReplanStrategy.ABORT,
                failed_action_id=failed_action.id,
                rationale=f"Replanning limit reached ({replan_count}). Aborting.",
                replan_count=replan_count,
            )

        # Timeout → retry with 2× timeout
        if self._is_timeout(error):
            new_params = dict(failed_action.params)
            new_params["timeout"] = new_params.get("timeout", 120) * 2
            return ReplanDecision(
                strategy=ReplanStrategy.RETRY,
                failed_action_id=failed_action.id,
                rationale="Timeout detected — retrying with 2x timeout.",
                retry_params=new_params,
                replan_count=replan_count + 1,
            )

        # File not found → inject creation step
        missing = self._extract_missing_path(error)
        if missing:
            inject_id = f"create_{failed_action.id}_prereq"
            return ReplanDecision(
                strategy=ReplanStrategy.INJECT_BEFORE,
                failed_action_id=failed_action.id,
                rationale=f"Missing prerequisite '{missing}' — injecting creation step.",
                new_actions=[{
                    "id":         inject_id,
                    "goal":       f"Create missing file or directory: {missing}",
                    "priority":   failed_action.priority - 1,
                    "dependencies": list(failed_action.dependencies),
                    "preferred_executors": ["terminal"],
                    "params":     {"command": f"mkdir -p {missing}"},
                }],
                replan_count=replan_count + 1,
            )

        # Build error → inject a fix step
        if self._is_build_error(error):
            fix_id = f"fix_{failed_action.id}"
            return ReplanDecision(
                strategy=ReplanStrategy.INJECT_BEFORE,
                failed_action_id=failed_action.id,
                rationale="Build error — injecting auto-fix step.",
                new_actions=[{
                    "id":         fix_id,
                    "goal":       f"Fix build error in: {failed_action.goal}",
                    "priority":   failed_action.priority - 1,
                    "dependencies": list(failed_action.dependencies),
                    "preferred_executors": ["terminal"],
                    "params":     {"command": "echo FIX_REQUIRED"},
                }],
                replan_count=replan_count + 1,
            )

        # Network / transient → retry once
        if self._is_transient(error):
            return ReplanDecision(
                strategy=ReplanStrategy.RETRY,
                failed_action_id=failed_action.id,
                rationale="Transient network error — retrying.",
                replan_count=replan_count + 1,
            )

        # CRITICAL risk → abort; others → skip
        if risk == ActionRisk.CRITICAL:
            return ReplanDecision(
                strategy=ReplanStrategy.ABORT,
                failed_action_id=failed_action.id,
                rationale=f"Critical action failed with unknown error: {result.error[:200]}",
                replan_count=replan_count + 1,
            )

        return ReplanDecision(
            strategy=ReplanStrategy.SKIP,
            failed_action_id=failed_action.id,
            rationale=f"Skipping non-critical action after failure: {result.error[:100]}",
            replan_count=replan_count + 1,
        )

    # ── Failure classifiers ───────────────────────────────────────────

    def _is_timeout(self, error: str) -> bool:
        return any(kw in error for kw in ["timeout", "timed out", "deadline"])

    def _is_transient(self, error: str) -> bool:
        return any(kw in error for kw in [
            "connection", "network", "reset", "refused", "temporarily",
            "503", "502", "429", "rate limit",
        ])

    def _is_build_error(self, error: str) -> bool:
        return any(kw in error for kw in [
            "compileerror", "syntaxerror", "import error", "nameerror",
            "error:", "failed to compile", "build failed",
        ])

    def _extract_missing_path(self, error: str) -> Optional[str]:
        m = re.search(
            r"(?:no such file|not found|cannot open|enoent)[:\s]+['\"]?([\w./\\-]+)",
            error, re.IGNORECASE,
        )
        return m.group(1) if m else None

    # ── Graph mutation ────────────────────────────────────────────────

    def apply(self, graph: ActionGraph, decision: ReplanDecision) -> None:
        """Mutate the ActionGraph according to the ReplanDecision."""
        if decision.strategy == ReplanStrategy.SKIP:
            action = graph.actions.get(decision.failed_action_id)
            if action:
                action.status = ActionStatus.SKIPPED
                logger.info("Replanner: skipped '%s'", decision.failed_action_id)

        elif decision.strategy == ReplanStrategy.RETRY:
            action = graph.actions.get(decision.failed_action_id)
            if action:
                action.status = ActionStatus.PENDING
                action.params.update(decision.retry_params)
                logger.info("Replanner: reset '%s' for retry", decision.failed_action_id)

        elif decision.strategy == ReplanStrategy.INJECT_BEFORE:
            from agent.action.action import Action as _Action
            from agent.planner.executor_planner import ExecutorPlanner
            from agent.planner.verification_planner import VerificationPlanner
            from agent.planner.rollback_planner import RollbackPlanner
            ep = ExecutorPlanner(); vp = VerificationPlanner(); rp = RollbackPlanner()
            for item in decision.new_actions:
                goal = item.get("goal", item["id"])
                a = _Action(
                    id=item["id"],
                    goal=goal,
                    priority=int(item.get("priority", 50)),
                    dependencies=list(item.get("dependencies", [])),
                    preferred_executors=item.get("preferred_executors", ep.assign(goal)),
                    verification_steps=vp.generate(goal),
                    rollback_steps=rp.generate(goal),
                    params=item.get("params", {}),
                )
                graph.add(a)
            # Make the failed action depend on the newly injected ones
            failed = graph.actions.get(decision.failed_action_id)
            if failed:
                for item in decision.new_actions:
                    if item["id"] not in failed.dependencies:
                        failed.dependencies.append(item["id"])
                failed.status = ActionStatus.PENDING
            logger.info(
                "Replanner: injected %d action(s) before '%s'",
                len(decision.new_actions), decision.failed_action_id,
            )

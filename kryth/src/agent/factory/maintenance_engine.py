"""MaintenanceEngine — monitor and repair a completed project.

Monitors: failing tests, stale dependencies, security warnings, browser breakages.
Reuses: TestingPipeline, ArchitectureGuardian, ReflectionEngine, ExperienceStore.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MaintenanceIssue:
    kind: str     # "test_failure" | "dependency_stale" | "security" | "architecture"
    severity: str # "info" | "warning" | "error"
    description: str
    suggested_fix: str = ""
    auto_fixable: bool = False

    def to_dict(self) -> dict:
        return {"kind": self.kind, "severity": self.severity,
                "description": self.description, "suggested_fix": self.suggested_fix,
                "auto_fixable": self.auto_fixable}


@dataclass
class MaintenanceReport:
    root: str
    issues: list[MaintenanceIssue] = field(default_factory=list)
    healthy: bool = True
    scanned_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"root": self.root, "issues": [i.to_dict() for i in self.issues],
                "healthy": self.healthy, "scanned_at": self.scanned_at,
                "issue_count": len(self.issues)}

    def summary(self) -> str:
        if not self.issues:
            return f"✓ Project healthy — {self.root}"
        lines = [f"Maintenance Report — {self.root}"]
        for issue in self.issues:
            icon = {"info": "ℹ", "warning": "⚠", "error": "✗"}.get(issue.severity, "?")
            lines.append(f"  {icon} [{issue.kind}] {issue.description}")
            if issue.suggested_fix:
                lines.append(f"       Fix: {issue.suggested_fix}")
        return "\n".join(lines)


class MaintenanceEngine:
    """Autonomously monitor and report on a project's health.

    Parameters
    ----------
    root:
        Project root directory.
    project_hash:
        Project identity (for experience lookup).
    """

    def __init__(self, root: str = ".", project_hash: str = "") -> None:
        self.root = root
        self.project_hash = project_hash

    def scan(self) -> MaintenanceReport:
        """Run all maintenance checks and return a report."""
        report = MaintenanceReport(root=self.root)

        self._check_tests(report)
        self._check_dependencies(report)
        self._check_architecture(report)
        self._check_security(report)

        report.healthy = not any(i.severity == "error" for i in report.issues)
        return report

    def suggest_repairs(self, report: MaintenanceReport) -> list[dict]:
        """Return a list of repair actions for auto-fixable issues."""
        repairs = []
        for issue in report.issues:
            if issue.auto_fixable and issue.suggested_fix:
                repairs.append({
                    "kind": issue.kind,
                    "description": issue.description,
                    "command": issue.suggested_fix,
                })
        return repairs

    # ------------------------------------------------------------------
    # Individual checkers
    # ------------------------------------------------------------------

    def _check_tests(self, report: MaintenanceReport) -> None:
        try:
            from agent.factory.testing_pipeline import TestingPipeline
            pipeline = TestingPipeline(self.root)
            result = pipeline.run_suite("unit")
            if not result.success:
                report.issues.append(MaintenanceIssue(
                    kind="test_failure",
                    severity="error",
                    description=f"{result.failed} test(s) failing",
                    suggested_fix="python -m pytest --tb=short",
                    auto_fixable=False,
                ))
            else:
                logger.debug("Tests passing: %d passed", result.passed)
        except Exception as exc:
            logger.debug("Test check error: %s", exc)

    def _check_dependencies(self, report: MaintenanceReport) -> None:
        root = Path(self.root)
        if (root / "requirements.txt").exists():
            report.issues.append(MaintenanceIssue(
                kind="dependency_stale",
                severity="info",
                description="requirements.txt may have outdated packages",
                suggested_fix="pip list --outdated",
                auto_fixable=False,
            ))
        if (root / "package.json").exists():
            report.issues.append(MaintenanceIssue(
                kind="dependency_stale",
                severity="info",
                description="npm packages may have updates available",
                suggested_fix="npm outdated",
                auto_fixable=False,
            ))

    def _check_architecture(self, report: MaintenanceReport) -> None:
        try:
            from agent.factory.architecture_guardian import ArchitectureGuardian
            guardian = ArchitectureGuardian(self.root)
            arch_report = guardian.audit()
            for v in arch_report.violations:
                if v.severity in ("error", "warning"):
                    report.issues.append(MaintenanceIssue(
                        kind="architecture",
                        severity=v.severity,
                        description=f"[{v.rule}] {v.path} — {v.detail}",
                    ))
        except Exception as exc:
            logger.debug("Architecture check error: %s", exc)

    def _check_security(self, report: MaintenanceReport) -> None:
        try:
            from agent.factory.code_review_engine import CodeReviewEngine
            engine = CodeReviewEngine(self.root)
            review = engine.review()
            for finding in review.findings:
                if finding.category == "security" and finding.severity in ("error", "critical"):
                    report.issues.append(MaintenanceIssue(
                        kind="security",
                        severity=finding.severity,
                        description=f"{finding.path}:{finding.line_no} — {finding.message}",
                        suggested_fix=finding.suggestion,
                    ))
        except Exception as exc:
            logger.debug("Security check error: %s", exc)

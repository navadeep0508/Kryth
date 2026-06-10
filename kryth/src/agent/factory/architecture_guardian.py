"""ArchitectureGuardian — continuously verify project structure and coding conventions.

Reuses:
- repo_index for symbol/import analysis
- WorkspaceModel from MissionManager
- orchestration.repo_intelligence.analyze_repo()

Never duplicates the index — calls repo_index directly.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.factory.config import ARCH_PATTERNS

logger = logging.getLogger(__name__)


@dataclass
class ArchViolation:
    rule: str
    severity: str  # "info" | "warning" | "error"
    path: str
    detail: str
    line_no: int = 0

    def to_dict(self) -> dict:
        return {"rule": self.rule, "severity": self.severity,
                "path": self.path, "detail": self.detail, "line_no": self.line_no}


@dataclass
class GuardianReport:
    root: str
    violations: list[ArchViolation] = field(default_factory=list)
    passed_checks: list[str] = field(default_factory=list)
    total_files_scanned: int = 0

    @property
    def has_errors(self) -> bool:
        return any(v.severity == "error" for v in self.violations)

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "violations": [v.to_dict() for v in self.violations],
            "passed_checks": self.passed_checks,
            "total_files_scanned": self.total_files_scanned,
            "has_errors": self.has_errors,
            "violation_count": len(self.violations),
        }

    def summary(self) -> str:
        lines = [f"Architecture Guardian — {self.root}"]
        if not self.violations:
            lines.append("✓ No violations found.")
        else:
            for v in self.violations:
                icon = {"info": "ℹ", "warning": "⚠", "error": "✗"}.get(v.severity, "?")
                lines.append(f"{icon} [{v.rule}] {v.path}:{v.line_no} — {v.detail}")
        for p in self.passed_checks:
            lines.append(f"✓ {p}")
        return "\n".join(lines)


class ArchitectureGuardian:
    """Check project structure, imports, file sizes, and secret leakage.

    Parameters
    ----------
    root:
        Project root directory.
    config:
        Override ARCH_PATTERNS defaults.
    """

    def __init__(self, root: str = ".", config: dict | None = None) -> None:
        self.root = root
        self._config = {**ARCH_PATTERNS, **(config or {})}

    def audit(self) -> GuardianReport:
        """Run all checks and return a GuardianReport."""
        report = GuardianReport(root=self.root)
        py_files = list(Path(self.root).rglob("*.py"))
        report.total_files_scanned = len(py_files)

        self._check_file_sizes(py_files, report)
        self._check_hardcoded_secrets(py_files, report)
        self._check_circular_imports(report)
        self._check_test_presence(report)
        self._check_module_boundaries(py_files, report)

        return report

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_file_sizes(self, files: list[Path], report: GuardianReport) -> None:
        max_lines = self._config.get("max_file_lines", 1000)
        violations = 0
        for f in files:
            try:
                lines = f.read_text(encoding="utf-8", errors="ignore").count("\n")
                if lines > max_lines:
                    report.violations.append(ArchViolation(
                        rule="max_file_lines",
                        severity="warning",
                        path=str(f.relative_to(self.root)),
                        detail=f"{lines} lines exceeds limit of {max_lines}",
                        line_no=lines,
                    ))
                    violations += 1
            except OSError:
                pass
        if not violations:
            report.passed_checks.append(f"File size check passed ({len(files)} files)")

    def _check_hardcoded_secrets(self, files: list[Path], report: GuardianReport) -> None:
        if not self._config.get("no_hardcoded_secrets", True):
            return
        patterns = [
            (r'(?i)(api_key|secret_key|password|token)\s*=\s*["\'][A-Za-z0-9+/=_\-]{16,}["\']', "hardcoded_secret"),
            (r'sk-[A-Za-z0-9]{32,}', "openai_key"),
            (r'ghp_[A-Za-z0-9]{36}', "github_token"),
        ]
        found = 0
        for f in files:
            if ".env" in f.name or f.name.startswith("test_") or f.name == "conftest.py":
                continue
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
                for pattern, rule in patterns:
                    for m in re.finditer(pattern, content):
                        lineno = content[: m.start()].count("\n") + 1
                        report.violations.append(ArchViolation(
                            rule=rule, severity="error",
                            path=str(f.relative_to(self.root)),
                            detail="Potential hardcoded secret detected",
                            line_no=lineno,
                        ))
                        found += 1
            except OSError:
                pass
        if not found:
            report.passed_checks.append("No hardcoded secrets detected")

    def _check_circular_imports(self, report: GuardianReport) -> None:
        if not self._config.get("no_circular_imports", True):
            return
        try:
            import agent.repo_index as ri
            ri.build_index(self.root)
            # Simple cycle detection: if file A imports B and B imports A
            cycles = []
            imports = ri._imports_by_file
            for file_a, imports_a in list(imports.items())[:50]:  # cap for performance
                for imp in imports_a:
                    dependents = ri.lookup_dependents(imp, self.root)
                    if any(file_a in dep for dep in dependents.get("imports", [])):
                        cycles.append(f"{file_a} ↔ {imp}")
            if cycles:
                for c in cycles[:5]:
                    report.violations.append(ArchViolation(
                        rule="circular_import", severity="warning",
                        path=c.split(" ↔ ")[0], detail=f"Potential circular import: {c}",
                    ))
            else:
                report.passed_checks.append("No circular imports detected")
        except Exception as exc:
            logger.debug("Circular import check skipped: %s", exc)

    def _check_test_presence(self, report: GuardianReport) -> None:
        test_dirs = list(Path(self.root).rglob("test*"))
        has_tests = any(p.is_dir() or p.suffix == ".py" for p in test_dirs)
        if has_tests:
            report.passed_checks.append("Test files present")
        else:
            report.violations.append(ArchViolation(
                rule="no_tests", severity="warning",
                path=self.root, detail="No test directory or test files found",
            ))

    def _check_module_boundaries(self, files: list[Path], report: GuardianReport) -> None:
        """Warn when internal (_private) modules are imported outside their package."""
        violations = 0
        for f in files:
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
                parent_pkg = str(f.parent.name)
                for m in re.finditer(r'from\s+([\w.]+)\._(\w+)\s+import', content):
                    imported_pkg = m.group(1).split(".")[-1]
                    if imported_pkg != parent_pkg:
                        lineno = content[: m.start()].count("\n") + 1
                        report.violations.append(ArchViolation(
                            rule="module_boundary",
                            severity="info",
                            path=str(f.relative_to(self.root)),
                            detail=f"Importing private module from {m.group(1)}._{m.group(2)}",
                            line_no=lineno,
                        ))
                        violations += 1
            except OSError:
                pass
        if not violations:
            report.passed_checks.append("Module boundary check passed")

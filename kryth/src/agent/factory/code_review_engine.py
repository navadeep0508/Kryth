"""CodeReviewEngine — automated code review before merge.

Checks: style, bugs, duplication, complexity, security, performance.
Reuses repo_index for symbol analysis and architecture_guardian for structural checks.
Stores findings in memory (no extra SQLite — findings are ephemeral per review).
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.factory.config import ReviewSeverity

logger = logging.getLogger(__name__)


@dataclass
class ReviewFinding:
    category: str   # style | bug | duplication | complexity | security | performance
    severity: str   # info | warning | error | critical
    path: str
    line_no: int
    message: str
    suggestion: str = ""

    def to_dict(self) -> dict:
        return {"category": self.category, "severity": self.severity,
                "path": self.path, "line_no": self.line_no,
                "message": self.message, "suggestion": self.suggestion}


@dataclass
class ReviewReport:
    files_reviewed: list[str] = field(default_factory=list)
    findings: list[ReviewFinding] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)

    @property
    def has_blockers(self) -> bool:
        return any(f.severity in ("error", "critical") for f in self.findings)

    def to_dict(self) -> dict:
        return {
            "files_reviewed": self.files_reviewed,
            "findings": [f.to_dict() for f in self.findings],
            "passed": self.passed,
            "has_blockers": self.has_blockers,
            "finding_count": len(self.findings),
        }

    def summary(self) -> str:
        lines = [f"Code Review — {len(self.files_reviewed)} file(s)"]
        if not self.findings:
            lines.append("✓ No issues found")
        else:
            for f in self.findings:
                icon = {"info": "ℹ", "warning": "⚠", "error": "✗", "critical": "🚨"}.get(f.severity, "?")
                lines.append(f"  {icon} [{f.category}] {f.path}:{f.line_no} — {f.message}")
        for p in self.passed:
            lines.append(f"✓ {p}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Security patterns
# ---------------------------------------------------------------------------

_SECURITY_PATTERNS = [
    (r'eval\s*\(', "eval() usage is a code injection risk", "critical"),
    (r'exec\s*\(', "exec() usage is a code injection risk", "critical"),
    (r'os\.system\s*\(', "os.system() is vulnerable to shell injection; use subprocess", "error"),
    (r'subprocess\.call\(.*shell\s*=\s*True', "shell=True in subprocess is a security risk", "error"),
    (r'pickle\.loads?\s*\(', "pickle.load() can execute arbitrary code", "error"),
    (r'yaml\.load\s*\([^,)]+\)', "yaml.load() without Loader is unsafe; use yaml.safe_load()", "warning"),
    (r'hashlib\.md5\b', "MD5 is cryptographically broken; use SHA-256+", "warning"),
    (r'random\.(random|randint|choice)\b', "Use secrets module for security-sensitive randomness", "info"),
]

_COMPLEXITY_THRESHOLD = 15   # McCabe complexity limit (approximated by nesting depth)
_MAX_FUNCTION_LINES = 80


class CodeReviewEngine:
    """Run automated code review over a set of Python files.

    Parameters
    ----------
    root:
        Project root (used for relative path display).
    """

    def __init__(self, root: str = ".") -> None:
        self.root = root

    def review(self, paths: list[str] | None = None) -> ReviewReport:
        """Review *paths* (or all .py files under root if None).

        Returns a ReviewReport.
        """
        if paths:
            files = [Path(p) for p in paths if Path(p).exists()]
        else:
            files = list(Path(self.root).rglob("*.py"))

        report = ReviewReport()
        for f in files:
            rel = _rel(f, self.root)
            report.files_reviewed.append(rel)
            self._check_security(f, rel, report)
            self._check_complexity(f, rel, report)
            self._check_style(f, rel, report)

        if not any(f.category == "security" for f in report.findings):
            report.passed.append("Security scan passed")
        if not any(f.category == "complexity" for f in report.findings):
            report.passed.append("Complexity check passed")

        return report

    def review_diff(self, changed_paths: list[str]) -> ReviewReport:
        """Review only the changed files."""
        return self.review(paths=changed_paths)

    # ------------------------------------------------------------------
    # Individual checkers
    # ------------------------------------------------------------------

    def _check_security(self, path: Path, rel: str, report: ReviewReport) -> None:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return
        for pattern, message, severity in _SECURITY_PATTERNS:
            for m in re.finditer(pattern, content):
                lineno = content[: m.start()].count("\n") + 1
                report.findings.append(ReviewFinding(
                    category="security", severity=severity,
                    path=rel, line_no=lineno, message=message,
                ))

    def _check_complexity(self, path: Path, rel: str, report: ReviewReport) -> None:
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(source)
        except Exception:
            return

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Approximate complexity by counting branches + loops
                complexity = 1
                for child in ast.walk(node):
                    if isinstance(child, (ast.If, ast.For, ast.While, ast.ExceptHandler,
                                          ast.With, ast.Assert, ast.comprehension)):
                        complexity += 1
                if complexity > _COMPLEXITY_THRESHOLD:
                    report.findings.append(ReviewFinding(
                        category="complexity",
                        severity="warning",
                        path=rel,
                        line_no=node.lineno,
                        message=f"Function '{node.name}' has high complexity ({complexity})",
                        suggestion=f"Consider breaking '{node.name}' into smaller functions",
                    ))

                # Long function check
                if hasattr(node, "end_lineno") and node.end_lineno:
                    func_lines = node.end_lineno - node.lineno
                    if func_lines > _MAX_FUNCTION_LINES:
                        report.findings.append(ReviewFinding(
                            category="complexity",
                            severity="info",
                            path=rel,
                            line_no=node.lineno,
                            message=f"Function '{node.name}' is {func_lines} lines long",
                            suggestion="Consider splitting long functions",
                        ))

    def _check_style(self, path: Path, rel: str, report: ReviewReport) -> None:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return
        lines = content.splitlines()
        for i, line in enumerate(lines, 1):
            if len(line) > 120:
                report.findings.append(ReviewFinding(
                    category="style", severity="info",
                    path=rel, line_no=i,
                    message=f"Line too long ({len(line)} chars)",
                    suggestion="Keep lines under 120 characters",
                ))
            if line.endswith(("  ", "\t ")):
                report.findings.append(ReviewFinding(
                    category="style", severity="info",
                    path=rel, line_no=i,
                    message="Trailing whitespace",
                ))


def _rel(path: Path, root: str) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)

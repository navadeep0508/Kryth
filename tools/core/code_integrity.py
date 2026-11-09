"""
KRYTH Code Generation Integrity.

Validates code before and after write/edit operations: checks for existing
files (overwrite prevention), validates Python syntax, checks import
resolution, and performs read-back verification after writes.
"""

from __future__ import annotations

import ast
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from utils.paths import resolve_path


@dataclass
class IntegrityIssue:
    """A single integrity issue found during validation."""

    severity: str  # "error", "warning", "info"
    message: str
    code: str  # e.g. "FILE_EXISTS", "SYNTAX_ERROR", "UNRESOLVED_IMPORT"


@dataclass
class IntegrityReport:
    """Result of validating code integrity."""

    issues: list[IntegrityIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[IntegrityIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[IntegrityIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.is_valid,
            "issues": [
                {"severity": i.severity, "message": i.message, "code": i.code}
                for i in self.issues
            ],
        }


class CodeIntegrityValidator:
    """Validates code generation integrity before and after write/edit operations."""

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd.resolve()

    # -- write validation ---------------------------------------------------

    def validate_write(
        self,
        path: str | Path,
        content: str,
        allow_overwrite: bool = True,
    ) -> IntegrityReport:
        """Validate a write operation before it happens.

        Checks:
        - File already exists (overwrite warning/error)
        - Python syntax (if .py file)
        - Import resolution (if .py file)
        """
        report = IntegrityReport()
        resolved = resolve_path(self.cwd, path)

        # Check if file already exists.
        if resolved.exists():
            if allow_overwrite:
                report.issues.append(IntegrityIssue(
                    severity="warning",
                    message=f"File already exists and will be overwritten: {resolved}",
                    code="FILE_EXISTS",
                ))
            else:
                report.issues.append(IntegrityIssue(
                    severity="error",
                    message=f"File already exists: {resolved}. Overwrite not allowed.",
                    code="FILE_EXISTS",
                ))

        # Check Python syntax.
        if resolved.suffix == ".py":
            report.issues.extend(self._check_python_syntax(content))
            report.issues.extend(self._check_imports(content))

        return report

    def validate_edit(
        self,
        path: str | Path,
        old_string: str,
        new_string: str,
    ) -> IntegrityReport:
        """Validate an edit operation before it happens.

        Checks:
        - File exists
        - old_string is present in the file
        - Resulting content has valid Python syntax
        - Resulting imports are resolvable
        """
        report = IntegrityReport()
        resolved = resolve_path(self.cwd, path)

        if not resolved.exists():
            report.issues.append(IntegrityIssue(
                severity="error",
                message=f"File does not exist: {resolved}",
                code="FILE_NOT_FOUND",
            ))
            return report

        try:
            old_content = resolved.read_text(encoding="utf-8")
        except OSError as e:
            report.issues.append(IntegrityIssue(
                severity="error",
                message=f"Cannot read file: {e}",
                code="READ_ERROR",
            ))
            return report

        # Check old_string is present (when not creating a new file).
        if old_string and old_string not in old_content:
            report.issues.append(IntegrityIssue(
                severity="error",
                message=f"old_string not found in file: {resolved}",
                code="STRING_NOT_FOUND",
            ))
            return report

        # Compute resulting content and check syntax/imports.
        if old_string:
            new_content = old_content.replace(old_string, new_string, 1)
        else:
            new_content = new_string

        if resolved.suffix == ".py":
            report.issues.extend(self._check_python_syntax(new_content))
            report.issues.extend(self._check_imports(new_content))

        return report

    def verify_write(
        self,
        path: str | Path,
        expected_content: str,
    ) -> IntegrityReport:
        """Read back a file after writing and verify content matches."""
        report = IntegrityReport()
        resolved = resolve_path(self.cwd, path)

        try:
            actual = resolved.read_text(encoding="utf-8")
        except OSError as e:
            report.issues.append(IntegrityIssue(
                severity="error",
                message=f"Read-back verification failed: cannot read {resolved}: {e}",
                code="READ_BACK_ERROR",
            ))
            return report

        if actual != expected_content:
            report.issues.append(IntegrityIssue(
                severity="error",
                message=f"Read-back verification failed: content does not match for {resolved}",
                code="READ_BACK_MISMATCH",
            ))

        return report

    # -- internal checks ---------------------------------------------------

    def _check_python_syntax(self, content: str) -> list[IntegrityIssue]:
        """Check Python syntax by compiling to AST."""
        issues: list[IntegrityIssue] = []
        try:
            compile(content, "<string>", "exec")
        except SyntaxError as e:
            issues.append(IntegrityIssue(
                severity="error",
                message=f"Python syntax error: {e.msg} (line {e.lineno}, offset {e.offset})",
                code="SYNTAX_ERROR",
            ))
        return issues

    def _check_imports(self, content: str) -> list[IntegrityIssue]:
        """Check that top-level imports in Python code can be resolved."""
        issues: list[IntegrityIssue] = []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return issues  # syntax errors already reported

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name.split(".")[0]
                    if not self._import_resolvable(module):
                        issues.append(IntegrityIssue(
                            severity="warning",
                            message=f"Import may not be resolvable: {module}",
                            code="UNRESOLVED_IMPORT",
                        ))
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    module = node.module.split(".")[0]
                    if not self._import_resolvable(module):
                        issues.append(IntegrityIssue(
                            severity="warning",
                            message=f"Import may not be resolvable: {node.module}",
                            code="UNRESOLVED_IMPORT",
                        ))
        return issues

    def _import_resolvable(self, module: str) -> bool:
        """Check if a top-level module can be imported (without side effects)."""
        return importlib.util.find_spec(module) is not None


def validate_code_integrity(
    cwd: Path | str,
    path: str | Path,
    content: str,
    operation: str = "write",
    old_string: str = "",
) -> IntegrityReport:
    """Convenience function: validate code integrity for a write or edit."""
    validator = CodeIntegrityValidator(Path(cwd).resolve())
    if operation == "write":
        return validator.validate_write(path, content)
    elif operation == "edit":
        return validator.validate_edit(path, old_string, content)
    else:
        raise ValueError(f"Unknown operation: {operation}")


__all__ = [
    "IntegrityIssue",
    "IntegrityReport",
    "CodeIntegrityValidator",
    "validate_code_integrity",
]

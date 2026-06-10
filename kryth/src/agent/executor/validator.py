"""Step result validator — checks if step results meet expected criteria.

Validates that:
- Required output files exist
- Content contains expected patterns
- Exit codes are clean
- Other custom validation criteria
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ValidationResult:
    """Result of a validation check."""

    def __init__(
        self,
        passed: bool,
        message: str = "",
        details: str = "",
    ) -> None:
        self.passed = passed
        self.message = message
        self.details = details

    def __bool__(self) -> bool:
        return self.passed

    def __repr__(self) -> str:
        return f"ValidationResult(passed={self.passed}, message={self.message!r})"


class Validator:
    """Validates step execution results.

    Supports built-in check types:
    - file_exists: Check that a file exists at the given path
    - file_contains: Check that a file contains expected text
    - file_not_empty: Check that a file has content
    - exit_code_zero: Check that a shell command exited with 0
    - output_contains: Check that output string contains expected text
    - output_matches: Check that output matches a regex
    - url_changed: Check that the page URL changed from a starting URL
    - custom: Run a custom validation function

    Usage:
        validator = Validator()
        result = validator.validate("file_exists", {"path": "output.txt"})
        if result.passed:
            print("Validation passed!")
    """

    VALIDATORS = {
        "file_exists": "_check_file_exists",
        "file_not_empty": "_check_file_not_empty",
        "file_contains": "_check_file_contains",
        "exit_code_zero": "_check_exit_code_zero",
        "output_contains": "_check_output_contains",
        "output_not_contains": "_check_output_not_contains",
        "output_matches": "_check_output_matches",
        "url_changed": "_check_url_changed",
    }

    def validate(
        self,
        check_type: str,
        params: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> ValidationResult:
        """Run a validation check.

        Args:
            check_type: The type of check to run (see class docstring).
            params: Parameters for the check (path, expected, etc.).
            context: Optional execution context (step results, page state).

        Returns:
            A ValidationResult indicating pass/fail.
        """
        params = params or {}
        context = context or {}

        method_name = self.VALIDATORS.get(check_type)
        if method_name is None:
            return ValidationResult(
                False,
                f"Unknown check type: {check_type}",
                f"Available: {', '.join(sorted(self.VALIDATORS.keys()))}",
            )

        method = getattr(self, method_name)
        try:
            return method(params, context)
        except Exception as e:
            return ValidationResult(
                False,
                f"Validation error: {e}",
                f"{type(e).__name__}: {e}",
            )

    def validate_many(
        self,
        checks: list[tuple[str, dict]],
        context: dict | None = None,
    ) -> list[ValidationResult]:
        """Run multiple validation checks and return all results."""
        return [self.validate(check_type, params, context) for check_type, params in checks]

    def validate_all(
        self,
        checks: list[tuple[str, dict]],
        context: dict | None = None,
    ) -> ValidationResult:
        """Run multiple checks; return first failure or overall success."""
        for check_type, params in checks:
            result = self.validate(check_type, params, context)
            if not result.passed:
                return result
        return ValidationResult(True, "All checks passed")

    # ------------------------------------------------------------------
    # Individual check implementations
    # ------------------------------------------------------------------

    def _check_file_exists(self, params: dict, context: dict) -> ValidationResult:
        path = params.get("path", "")
        if not path:
            return ValidationResult(False, "Missing 'path' parameter")
        if os.path.exists(path):
            return ValidationResult(True, f"File exists: {path}")
        return ValidationResult(False, f"File not found: {path}")

    def _check_file_not_empty(self, params: dict, context: dict) -> ValidationResult:
        path = params.get("path", "")
        if not path:
            return ValidationResult(False, "Missing 'path' parameter")
        if not os.path.exists(path):
            return ValidationResult(False, f"File not found: {path}")
        size = os.path.getsize(path)
        if size > 0:
            return ValidationResult(True, f"File is not empty ({size} bytes)")
        return ValidationResult(False, f"File is empty: {path}")

    def _check_file_contains(self, params: dict, context: dict) -> ValidationResult:
        path = params.get("path", "")
        expected = params.get("expected", "")
        if not path:
            return ValidationResult(False, "Missing 'path' parameter")
        if not expected:
            return ValidationResult(False, "Missing 'expected' parameter")
        if not os.path.exists(path):
            return ValidationResult(False, f"File not found: {path}")
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            if expected in content:
                return ValidationResult(True, f"Found expected text in {path}")
            return ValidationResult(
                False,
                f"Expected text not found in {path}",
                f"Searched for: {expected[:200]}",
            )
        except Exception as e:
            return ValidationResult(False, f"Error reading file: {e}")

    def _check_exit_code_zero(self, params: dict, context: dict) -> ValidationResult:
        exit_code = params.get("exit_code", context.get("exit_code"))
        if exit_code is None:
            return ValidationResult(False, "No exit code available")
        if exit_code == 0:
            return ValidationResult(True, "Exit code is 0")
        return ValidationResult(False, f"Exit code is {exit_code}")

    def _check_output_contains(self, params: dict, context: dict) -> ValidationResult:
        output = params.get("output", context.get("output", ""))
        expected = params.get("expected", "")
        if not expected:
            return ValidationResult(False, "Missing 'expected' parameter")
        if expected in output:
            return ValidationResult(True, "Output contains expected text")
        return ValidationResult(
            False,
            "Expected text not found in output",
            f"Searched for: {expected[:200]}",
        )

    def _check_output_not_contains(self, params: dict, context: dict) -> ValidationResult:
        output = params.get("output", context.get("output", ""))
        unexpected = params.get("unexpected", params.get("expected", ""))
        if not unexpected:
            return ValidationResult(False, "Missing 'unexpected' parameter")
        if unexpected not in output:
            return ValidationResult(True, "Output does not contain unexpected text")
        return ValidationResult(False, f"Output contains unwanted text: {unexpected[:200]}")

    def _check_output_matches(self, params: dict, context: dict) -> ValidationResult:
        output = params.get("output", context.get("output", ""))
        pattern = params.get("pattern", "")
        if not pattern:
            return ValidationResult(False, "Missing 'pattern' parameter")
        try:
            if re.search(pattern, output):
                return ValidationResult(True, f"Output matches pattern: {pattern}")
            return ValidationResult(False, f"Output does not match pattern: {pattern}")
        except re.error as e:
            return ValidationResult(False, f"Invalid regex pattern: {e}")

    def _check_url_changed(self, params: dict, context: dict) -> ValidationResult:
        old_url = params.get("old_url", "")
        new_url = params.get("new_url", context.get("page_url", ""))
        if not old_url:
            return ValidationResult(False, "Missing 'old_url' parameter")
        if new_url and new_url != old_url:
            return ValidationResult(True, f"URL changed: {old_url} -> {new_url}")
        return ValidationResult(False, f"URL did not change (still: {old_url})")

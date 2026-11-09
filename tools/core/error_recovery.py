"""
KRYTH Error Recovery Engine.

Provides structured error classification, recovery procedures, and failure
tracking. Errors are classified into categories (shell, import, dependency,
config, syntax, runtime, API incompatibility, permission, network, timeout),
each with a suggested recovery procedure. The engine tracks consecutive
similar failures and recommends stopping after 2 of the same type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ErrorCategory(str, Enum):
    """Structured error categories for recovery planning."""

    SHELL = "shell"
    IMPORT = "import"
    DEPENDENCY = "dependency"
    CONFIG = "config"
    SYNTAX = "syntax"
    RUNTIME = "runtime"
    API_INCOMPATIBILITY = "api_incompatibility"
    PERMISSION = "permission"
    NETWORK = "network"
    TIMEOUT = "timeout"
    SHELL_MISMATCH = "SHELL_MISMATCH"
    INVALID_PATH = "INVALID_PATH"
    WORKDIR_NOT_FOUND = "WORKDIR_NOT_FOUND"
    COMMAND_NOT_FOUND = "COMMAND_NOT_FOUND"
    ARGUMENT_PARSING_ERROR = "ARGUMENT_PARSING_ERROR"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    IMPORT_ERROR = "IMPORT_ERROR"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    APPLICATION_RUNTIME_ERROR = "APPLICATION_RUNTIME_ERROR"
    TIMEOUT_ERROR = "TIMEOUT"
    UNKNOWN = "unknown"


@dataclass
class ErrorClassification:
    """Result of classifying an error."""

    category: ErrorCategory
    message: str
    suggested_action: str
    retryable: bool
    severity: str  # "error", "warning", "info"


@dataclass
class FailureRecord:
    """A recorded failure with timestamp and category."""

    category: ErrorCategory
    message: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class RecoveryPlan:
    """A recovery plan for a classified error."""

    category: ErrorCategory
    primary_action: str
    fallback_actions: list[str]
    max_retries: int
    should_stop: bool
    reason: str


class ErrorRecoveryEngine:
    """Classifies errors, suggests recovery actions, and tracks failures."""

    # -- classification patterns -------------------------------------------

    _SHELL_PATTERNS = [
        r"command not found",
        r"no such file or directory",
        r"permission denied",
        r"exit code",
        r"subprocess",
    ]

    _IMPORT_PATTERNS = [
        r"ModuleNotFoundError",
        r"ImportError",
        r"cannot import name",
        r"No module named",
    ]

    _DEPENDENCY_PATTERNS = [
        r"dependency",
        r"package.*not.*found",
        r"requirement.*not.*satisfied",
        r"version conflict",
    ]

    _CONFIG_PATTERNS = [
        r"config",
        r"configuration",
        r"setting",
        r"api key",
        r"base_url",
        r"provider",
    ]

    _SYNTAX_PATTERNS = [
        r"SyntaxError",
        r"IndentationError",
        r"invalid syntax",
    ]

    _RUNTIME_PATTERNS = [
        r"RuntimeError",
        r"AttributeError",
        r"TypeError",
        r"ValueError",
        r"KeyError",
        r"IndexError",
    ]

    _API_INCOMPATIBILITY_PATTERNS = [
        r"API.*incompatible",
        r"version.*mismatch",
        r"unsupported.*version",
        r"method.*not.*found",
    ]

    _PERMISSION_PATTERNS = [
        r"PermissionError",
        r"Access denied",
        r"forbidden",
        r"unauthorized",
    ]

    _NETWORK_PATTERNS = [
        r"ConnectionError",
        r"timeout",
        r"network",
        r"unreachable",
        r"DNS",
    ]

    _TIMEOUT_PATTERNS = [
        r"TimeoutError",
        r"timed out",
        r"deadline exceeded",
    ]

    def __init__(self) -> None:
        self._failures: list[FailureRecord] = []
        self._max_similar_failures = 2

    # -- classification -----------------------------------------------------

    def classify(self, error: Exception | str) -> ErrorClassification:
        """Classify an error into a category with recovery guidance."""
        message = str(error)

        category = self._categorize(message)
        suggested = self._suggest_action(category, message)
        retryable = self._is_retryable(category)
        severity = self._severity(category)

        return ErrorClassification(
            category=category,
            message=message,
            suggested_action=suggested,
            retryable=retryable,
            severity=severity,
        )

    def _categorize(self, message: str) -> ErrorCategory:
        lowered = message.lower()

        if "shell_mismatch" in lowered or "shell mismatch" in lowered:
            return ErrorCategory.SHELL_MISMATCH
        if "working directory doesn't exist" in lowered or "workdir_not_found" in lowered:
            return ErrorCategory.WORKDIR_NOT_FOUND
        if "invalid path" in lowered or "invalid_path" in lowered:
            return ErrorCategory.INVALID_PATH
        if "command not found" in lowered or "is not recognized as the name" in lowered:
            return ErrorCategory.COMMAND_NOT_FOUND
        if "argument parsing" in lowered or "argument_parsing_error" in lowered:
            return ErrorCategory.ARGUMENT_PARSING_ERROR
        if "permission_denied" in lowered or "permission denied" in lowered or "access is denied" in lowered:
            return ErrorCategory.PERMISSION_DENIED

        # Check in priority order.
        for pattern in self._SYNTAX_PATTERNS:
            if pattern.lower() in lowered:
                return ErrorCategory.SYNTAX

        for pattern in self._IMPORT_PATTERNS:
            if pattern.lower() in lowered:
                return ErrorCategory.IMPORT_ERROR

        for pattern in self._DEPENDENCY_PATTERNS:
            if pattern.lower() in lowered:
                return ErrorCategory.DEPENDENCY_ERROR

        for pattern in self._CONFIG_PATTERNS:
            if pattern.lower() in lowered:
                return ErrorCategory.CONFIGURATION_ERROR

        for pattern in self._PERMISSION_PATTERNS:
            if pattern.lower() in lowered:
                return ErrorCategory.PERMISSION_DENIED

        for pattern in self._NETWORK_PATTERNS:
            if pattern.lower() in lowered:
                return ErrorCategory.NETWORK

        for pattern in self._TIMEOUT_PATTERNS:
            if pattern.lower() in lowered:
                return ErrorCategory.TIMEOUT_ERROR

        for pattern in self._API_INCOMPATIBILITY_PATTERNS:
            if pattern.lower() in lowered:
                return ErrorCategory.API_INCOMPATIBILITY

        for pattern in self._RUNTIME_PATTERNS:
            if pattern.lower() in lowered:
                return ErrorCategory.APPLICATION_RUNTIME_ERROR

        for pattern in self._SHELL_PATTERNS:
            if pattern.lower() in lowered:
                return ErrorCategory.SHELL

        return ErrorCategory.UNKNOWN

    def _suggest_action(self, category: ErrorCategory, message: str) -> str:
        suggestions = {
            ErrorCategory.SHELL_MISMATCH: "Use the pinned shell's syntax or explicitly configure the requested shell.",
            ErrorCategory.INVALID_PATH: "Normalize the path for the selected operating system and check quoting.",
            ErrorCategory.WORKDIR_NOT_FOUND: "Verify that the working directory exists and resolve it from the workspace root.",
            ErrorCategory.COMMAND_NOT_FOUND: "Check the executable name and PATH, then retry only after correcting them.",
            ErrorCategory.ARGUMENT_PARSING_ERROR: "Recheck shell-specific quoting and pass paths with spaces as one argument.",
            ErrorCategory.PERMISSION_DENIED: "Check access permissions for the target path or executable.",
            ErrorCategory.DEPENDENCY_ERROR: "Install or select the project environment's declared dependency.",
            ErrorCategory.IMPORT_ERROR: "Verify module names and use the project's virtual environment.",
            ErrorCategory.CONFIGURATION_ERROR: "Review the active project configuration and its resolved file paths.",
            ErrorCategory.APPLICATION_RUNTIME_ERROR: "Inspect the full application traceback and reproduce with a focused check.",
            ErrorCategory.TIMEOUT_ERROR: "Check process progress, then split the work or adjust its timeout.",
            ErrorCategory.SHELL: (
                "Verify the command exists and the path is correct. "
                "Check shell type and working directory."
            ),
            ErrorCategory.IMPORT: (
                "Install the missing module or check the import path. "
                "Verify the package is in the environment."
            ),
            ErrorCategory.DEPENDENCY: (
                "Check installed package versions against requirements. "
                "Run pip install or update dependencies."
            ),
            ErrorCategory.CONFIG: (
                "Review configuration settings. "
                "Run /doctor to check config and credentials."
            ),
            ErrorCategory.SYNTAX: (
                "Fix the syntax error in the source file. "
                "Check indentation and brackets."
            ),
            ErrorCategory.RUNTIME: (
                "Check the object type and method arguments. "
                "Review the stack trace for the failing call."
            ),
            ErrorCategory.API_INCOMPATIBILITY: (
                "Update the package or adjust the code to match the API. "
                "Check the provider's version requirements."
            ),
            ErrorCategory.PERMISSION: (
                "Check file permissions and user access. "
                "Run with appropriate privileges."
            ),
            ErrorCategory.NETWORK: (
                "Check network connectivity and DNS. "
                "Verify the endpoint URL is reachable."
            ),
            ErrorCategory.TIMEOUT: (
                "Increase the timeout or break the operation into smaller steps. "
                "Check if the service is responding."
            ),
            ErrorCategory.UNKNOWN: (
                "Review the error message and stack trace. "
                "Check logs for additional context."
            ),
        }
        return suggestions.get(category, suggestions[ErrorCategory.UNKNOWN])

    def _is_retryable(self, category: ErrorCategory) -> bool:
        non_retryable = {
            ErrorCategory.SYNTAX,
            ErrorCategory.CONFIG,
            ErrorCategory.PERMISSION,
            ErrorCategory.SHELL_MISMATCH,
            ErrorCategory.INVALID_PATH,
            ErrorCategory.WORKDIR_NOT_FOUND,
            ErrorCategory.ARGUMENT_PARSING_ERROR,
            ErrorCategory.PERMISSION_DENIED,
            ErrorCategory.CONFIGURATION_ERROR,
        }
        return category not in non_retryable

    def _severity(self, category: ErrorCategory) -> str:
        high = {ErrorCategory.CONFIG, ErrorCategory.PERMISSION}
        if category in high:
            return "error"
        return "error"

    # -- failure tracking ---------------------------------------------------

    def record_failure(self, category: ErrorCategory, message: str) -> None:
        """Record a failure for tracking similar-failure counts."""
        self._failures.append(
            FailureRecord(category=category, message=message)
        )

    def recent_failures(self, category: ErrorCategory, count: int = 5) -> list[FailureRecord]:
        """Return recent failures of a given category, newest first."""
        matching = [
            f for f in self._failures if f.category == category
        ]
        return sorted(matching, key=lambda f: f.timestamp, reverse=True)[:count]

    def similar_failure_count(self, category: ErrorCategory) -> int:
        """Return the number of recent failures of the same category."""
        return len(self.recent_failures(category))

    def should_stop_on_failure(self, category: ErrorCategory) -> bool:
        """Return True if there have been >= 2 consecutive similar failures."""
        return self.similar_failure_count(category) >= self._max_similar_failures

    # -- recovery plan ------------------------------------------------------

    def recovery_plan(
        self, category: ErrorCategory, message: str = ""
    ) -> RecoveryPlan:
        """Build a recovery plan for a category of error.

        Includes the stop-after-2-similar-failures rule.
        """
        classification = self.classify(
            Exception(message) if message else ValueError("unknown")
        )
        should_stop = self.should_stop_on_failure(category)

        primary = classification.suggested_action

        fallbacks: list[str] = {
            ErrorCategory.SHELL_MISMATCH: ["Keep the configured shell and rewrite syntax, or pin the intended shell."],
            ErrorCategory.INVALID_PATH: ["Resolve the path against the workspace and quote it using the selected shell."],
            ErrorCategory.WORKDIR_NOT_FOUND: ["List the parent directory and choose an existing working directory."],
            ErrorCategory.COMMAND_NOT_FOUND: ["Check PATH and the project's virtual environment before retrying."],
            ErrorCategory.ARGUMENT_PARSING_ERROR: ["Quote the complete path or argument with the configured shell's rules."],
            ErrorCategory.PERMISSION_DENIED: ["Check ownership and access rights for the specific target."],
            ErrorCategory.DEPENDENCY_ERROR: ["Install the project's locked dependency into its active environment."],
            ErrorCategory.IMPORT_ERROR: ["Verify the package import path and active interpreter."],
            ErrorCategory.CONFIGURATION_ERROR: ["Print resolved configuration paths without exposing credentials."],
            ErrorCategory.APPLICATION_RUNTIME_ERROR: ["Capture the traceback and reproduce one failing request."],
            ErrorCategory.TIMEOUT_ERROR: ["Check the process tree, then rerun a smaller bounded operation."],
            ErrorCategory.SHELL: [
                "Try running with a different shell (PowerShell, CMD, Bash).",
                "Check if the command is available in PATH.",
            ],
            ErrorCategory.IMPORT: [
                "Install the missing package: pip install <module>.",
                "Check if a virtual environment needs activation.",
            ],
            ErrorCategory.DEPENDENCY: [
                "Update packages: pip install -r requirements.txt.",
                "Check for version conflicts in pyproject.toml.",
            ],
            ErrorCategory.CONFIG: [
                "Run /setup to reconfigure the provider.",
                "Set API_KEY and BASE_URL environment variables.",
            ],
            ErrorCategory.SYNTAX: [
                "Run a linter or formatter on the file.",
                "Review the file at the reported line number.",
            ],
            ErrorCategory.RUNTIME: [
                "Add defensive checks for None values.",
                "Review the object's available methods.",
            ],
            ErrorCategory.API_INCOMPATIBILITY: [
                "Check the provider's API documentation.",
                "Pin the package to a compatible version.",
            ],
            ErrorCategory.PERMISSION: [
                "Change file permissions with chmod.",
                "Run as administrator or with sudo.",
            ],
            ErrorCategory.NETWORK: [
                "Check internet connectivity.",
                "Verify proxy settings if applicable.",
            ],
            ErrorCategory.TIMEOUT: [
                "Retry with a longer timeout.",
                "Break the operation into smaller chunks.",
            ],
            ErrorCategory.UNKNOWN: [
                "Review full error output and logs.",
                "Retry the operation once more.",
            ],
        }.get(category, [])

        reason = ""
        if should_stop:
            reason = (
                f"Stopping after {self._max_similar_failures} similar "
                f"{category.value} failures."
            )

        return RecoveryPlan(
            category=category,
            primary_action=primary,
            fallback_actions=fallbacks,
            max_retries=self._max_similar_failures,
            should_stop=should_stop,
            reason=reason,
        )

    def reset(self) -> None:
        """Clear all recorded failures."""
        self._failures.clear()


# Shared engine instance for convenience.
_engine: ErrorRecoveryEngine | None = None


def get_error_recovery_engine() -> ErrorRecoveryEngine:
    """Return the shared ErrorRecoveryEngine instance."""
    global _engine
    if _engine is None:
        _engine = ErrorRecoveryEngine()
    return _engine


def classify_error(error: Exception | str) -> ErrorClassification:
    """Convenience: classify an error using the shared engine."""
    return get_error_recovery_engine().classify(error)


def record_failure(category: ErrorCategory, message: str) -> None:
    """Convenience: record a failure using the shared engine."""
    get_error_recovery_engine().record_failure(category, message)


def recovery_plan(category: ErrorCategory, message: str = "") -> RecoveryPlan:
    """Convenience: build a recovery plan using the shared engine."""
    return get_error_recovery_engine().recovery_plan(category, message)


__all__ = [
    "ErrorCategory",
    "ErrorClassification",
    "FailureRecord",
    "RecoveryPlan",
    "ErrorRecoveryEngine",
    "get_error_recovery_engine",
    "classify_error",
    "record_failure",
    "recovery_plan",
]

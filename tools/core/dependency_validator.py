"""
KRYTH Dependency / API Validation.

Inspects installed package versions, verifies that dependencies declared by a
detected project are satisfied, and checks that method signatures on objects
match expected interfaces. Used before project startup or code generation to
catch dependency incompatibilities early.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DependencyIssue:
    """A single dependency or API issue."""

    severity: str  # "error", "warning", "info"
    message: str
    code: str  # e.g. "PACKAGE_MISSING", "VERSION_MISMATCH", "SIGNATURE_MISMATCH"


@dataclass
class DependencyReport:
    """Result of validating dependencies and APIs."""

    issues: list[DependencyIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[DependencyIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[DependencyIssue]:
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


@dataclass
class PackageInfo:
    """Information about an installed package."""

    name: str
    version: str | None = None
    installed: bool = False


class DependencyValidator:
    """Validates installed packages, versions, and API signatures."""

    # -- package / version checks -------------------------------------------

    def check_package(self, package_name: str) -> PackageInfo:
        """Return info about whether *package_name* is installed and its version."""
        try:
            dist = importlib.metadata.distribution(package_name)
            return PackageInfo(
                name=package_name,
                version=dist.version,
                installed=True,
            )
        except importlib.metadata.PackageNotFoundError:
            # Try alternate name normalization (e.g. "PyYAML" → "yaml").
            try:
                dist = importlib.metadata.distribution(package_name.replace("-", "_"))
                return PackageInfo(
                    name=package_name,
                    version=dist.version,
                    installed=True,
                )
            except importlib.metadata.PackageNotFoundError:
                return PackageInfo(name=package_name, version=None, installed=False)

    def check_packages(self, package_names: list[str]) -> DependencyReport:
        """Check that all packages in *package_names* are installed."""
        report = DependencyReport()
        for name in package_names:
            info = self.check_package(name)
            if not info.installed:
                report.issues.append(DependencyIssue(
                    severity="error",
                    message=f"Package not installed: {name}",
                    code="PACKAGE_MISSING",
                ))
        return report

    def check_version(
        self,
        package_name: str,
        required_version: str,
    ) -> DependencyReport:
        """Check that *package_name* is installed at *required_version* or newer.

        Uses simple semantic version comparison (major.minor.patch).
        """
        report = DependencyReport()
        info = self.check_package(package_name)

        if not info.installed:
            report.issues.append(DependencyIssue(
                severity="error",
                message=f"Package not installed: {package_name} (required >= {required_version})",
                code="PACKAGE_MISSING",
            ))
            return report

        if info.version is None:
            report.issues.append(DependencyIssue(
                severity="warning",
                message=f"Could not determine version for {package_name}",
                code="VERSION_UNKNOWN",
            ))
            return report

        if not self._version_satisfies(info.version, required_version):
            report.issues.append(DependencyIssue(
                severity="error",
                message=(
                    f"Version mismatch for {package_name}: "
                    f"installed {info.version}, required >= {required_version}"
                ),
                code="VERSION_MISMATCH",
            ))

        return report

    # -- API / signature checks ---------------------------------------------

    def check_signature(
        self,
        obj: Any,
        expected_params: dict[str, type | None],
    ) -> DependencyReport:
        """Verify that a callable's signature matches *expected_params*.

        *expected_params* maps parameter names to expected types (or None for
        any type). Only checks parameters that are present in both.
        """
        report = DependencyReport()

        if not callable(obj):
            report.issues.append(DependencyIssue(
                severity="error",
                message=f"Object is not callable: {type(obj).__name__}",
                code="NOT_CALLABLE",
            ))
            return report

        try:
            sig = inspect.signature(obj)
        except (ValueError, TypeError) as e:
            report.issues.append(DependencyIssue(
                severity="error",
                message=f"Cannot inspect signature: {e}",
                code="SIGNATURE_INSPECTION_FAILED",
            ))
            return report

        for param_name, expected_type in expected_params.items():
            if param_name not in sig.parameters:
                report.issues.append(DependencyIssue(
                    severity="error",
                    message=f"Missing parameter '{param_name}' in {obj}",
                    code="SIGNATURE_MISMATCH",
                ))
                continue

            param = sig.parameters[param_name]
            if expected_type is not None and param.annotation is not inspect.Parameter.empty:
                if param.annotation != expected_type:
                    report.issues.append(DependencyIssue(
                        severity="warning",
                        message=(
                            f"Parameter '{param_name}' type mismatch in {obj}: "
                            f"expected {expected_type}, got {param.annotation}"
                        ),
                        code="SIGNATURE_MISMATCH",
                    ))

        return report

    # -- project dependency checks ------------------------------------------

    def check_project_dependencies(
        self,
        package_manager: str,
        manifest_content: str,
    ) -> DependencyReport:
        """Check dependencies declared in a project manifest.

        Supports pip (requirements.txt / pyproject.toml) and npm (package.json).
        Returns a report of missing or mismatched packages.
        """
        report = DependencyReport()

        if package_manager == "pip":
            packages = self._parse_pip_requirements(manifest_content)
        elif package_manager == "npm":
            packages = self._parse_npm_dependencies(manifest_content)
        else:
            report.issues.append(DependencyIssue(
                severity="warning",
                message=f"Unknown package manager: {package_manager}",
                code="UNKNOWN_PACKAGE_MANAGER",
            ))
            return report

        for name, version in packages:
            if version:
                report.issues.extend(self.check_version(name, version).issues)
            else:
                info = self.check_package(name)
                if not info.installed:
                    report.issues.append(DependencyIssue(
                        severity="error",
                        message=f"Package not installed: {name}",
                        code="PACKAGE_MISSING",
                    ))

        return report

    # -- internal helpers ---------------------------------------------------

    @staticmethod
    def _version_satisfies(installed: str, required: str) -> bool:
        """Simple semver check: installed >= required."""
        try:
            installed_parts = [int(p) for p in installed.split(".")[:3]]
            required_parts = [int(p) for p in required.split(".")[:3]]
            # Pad to same length.
            while len(installed_parts) < len(required_parts):
                installed_parts.append(0)
            while len(required_parts) < len(installed_parts):
                required_parts.append(0)
            return installed_parts >= required_parts
        except (ValueError, AttributeError):
            return True  # Can't parse — assume OK

    @staticmethod
    def _parse_pip_requirements(content: str) -> list[tuple[str, str | None]]:
        """Parse requirements.txt-style content into (name, version) pairs."""
        packages: list[tuple[str, str | None]] = []
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Handle `package>=version`, `package==version`, `package~=version`.
            for sep in (">=", "==", "~=", ">", "<"):
                if sep in line:
                    name, ver = line.split(sep, 1)
                    packages.append((name.strip(), ver.strip().split(",")[0]))
                    break
            else:
                packages.append((line, None))
        return packages

    @staticmethod
    def _parse_npm_dependencies(content: str) -> list[tuple[str, str | None]]:
        """Parse package.json dependencies into (name, version) pairs."""
        import json

        packages: list[tuple[str, str | None]] = []
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return packages

        for key in ("dependencies", "devDependencies", "peerDependencies"):
            deps = data.get(key, {})
            if isinstance(deps, dict):
                for name, ver in deps.items():
                    # Strip leading ^, ~, >= etc.
                    clean = ver
                    for prefix in ("^", "~", ">=", "==", ">"):
                        if clean.startswith(prefix):
                            clean = clean[len(prefix):]
                    packages.append((name, clean))
        return packages


def validate_dependencies(
    package_names: list[str],
) -> DependencyReport:
    """Convenience: check that all packages are installed."""
    return DependencyValidator().check_packages(package_names)


def validate_version(
    package_name: str,
    required_version: str,
) -> DependencyReport:
    """Convenience: check package version."""
    return DependencyValidator().check_version(package_name, required_version)


__all__ = [
    "DependencyIssue",
    "DependencyReport",
    "PackageInfo",
    "DependencyValidator",
    "validate_dependencies",
    "validate_version",
]

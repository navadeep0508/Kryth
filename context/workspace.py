"""
KRYTH Workspace Context Validation.

Validates the working directory before complex agent requests: resolves CWD,
verifies directory existence, inspects contents, detects git status, identifies
existing project files, determines if the workspace is empty or new, and reports
whether the user's intent is ambiguous (so the agent can ask for clarification
instead of silently creating a nested project).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from utils.paths import is_binary_file, read_text_safe

# Manifest / project files that signal an existing project.
_PROJECT_MARKERS = [
    "pyproject.toml",
    "package.json",
    "requirements.txt",
    "setup.py",
    "Pipfile",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "manage.py",
    "app.py",
    "main.py",
    "tsconfig.json",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "composer.json",
    "mvn/pom.xml",
    "build.gradle",
    "settings.gradle",
    "Procfile",
    ".env.example",
    ".env",
]

# Files that, when present in an otherwise empty directory, still make it feel
# like "an existing project" rather than a blank slate.
_CONFIG_FILES = {".gitignore", ".gitattributes", "LICENSE", "README.md", "Makefile"}


@dataclass
class GitInfo:
    """Git repository detection results."""

    is_repo: bool = False
    status: str | None = None  # "clean", "modified", "unknown"
    branch: str | None = None
    commit: str | None = None
    root: Path | None = None


@dataclass
class WorkspaceInfo:
    """Result of validating and inspecting a workspace directory."""

    path: Path
    exists: bool = False
    is_dir: bool = False
    is_empty: bool = True
    is_new_workspace: bool = True
    is_git_repo: bool = False
    git: GitInfo = field(default_factory=GitInfo)
    project_files: list[str] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)
    detected_type: str | None = None
    file_count: int = 0
    dir_count: int = 0
    error: str | None = None

    @property
    def is_ambiguous(self) -> bool:
        """True when the workspace state makes user intent unclear.

        An empty non-git directory with no project markers is ambiguous: the
        user might want to start a new project here, or they might have meant
        to point at a different directory.
        """
        return self.is_new_workspace and not self.is_git_repo and not self.project_files

    @property
    def has_project(self) -> bool:
        """True when the workspace looks like an existing project."""
        return bool(self.project_files) or self.is_git_repo


class WorkspaceValidator:
    """Validates and inspects a workspace directory."""

    def __init__(self, cwd: Path | str | None = None) -> None:
        self.cwd = Path(cwd or Path.cwd()).resolve()

    # -- public API -----------------------------------------------------------

    def validate(self) -> WorkspaceInfo:
        """Run full workspace validation and return a WorkspaceInfo.

        Never raises: any inspection failure is captured in `error`.
        """
        info = WorkspaceInfo(path=self.cwd)

        try:
            info.exists = self.cwd.exists()
            if not info.exists:
                info.error = f"Directory does not exist: {self.cwd}"
                return info

            info.is_dir = self.cwd.is_dir()
            if not info.is_dir:
                info.error = f"Path is not a directory: {self.cwd}"
                return info

            info.is_empty, info.file_count, info.dir_count = self._inspect_contents()
            info.is_new_workspace = info.is_empty and not info.config_files
            info.git = self._inspect_git()
            info.is_git_repo = info.git.is_repo

            info.project_files, info.config_files = self._list_project_files()
            info.detected_type = self._infer_project_type(info.project_files)

        except (OSError, ValueError) as exc:
            info.error = str(exc)

        return info

    # -- inspection ----------------------------------------------------------

    def _inspect_contents(self) -> tuple[bool, int, int]:
        """Return (is_empty, file_count, dir_count) for the workspace."""
        file_count = 0
        dir_count = 0
        try:
            for entry in self.cwd.iterdir():
                if entry.is_file():
                    file_count += 1
                elif entry.is_dir():
                    dir_count += 1
        except OSError:
            return True, 0, 0
        return file_count == 0 and dir_count == 0, file_count, dir_count

    def _inspect_git(self) -> GitInfo:
        """Detect git repo status using `git` CLI where available."""
        git_dir = self.cwd / ".git"
        if not git_dir.exists():
            return GitInfo(is_repo=False)

        info = GitInfo(is_repo=True, root=self.cwd)

        # Try to get the branch name.
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                info.branch = result.stdout.strip() or None
        except (OSError, subprocess.TimeoutExpired):
            pass

        # Try to get the short commit hash.
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                info.commit = result.stdout.strip() or None
        except (OSError, subprocess.TimeoutExpired):
            pass

        # Determine working-tree status.
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                output = result.stdout.strip()
                info.status = "modified" if output else "clean"
        except (OSError, subprocess.TimeoutExpired):
            info.status = "unknown"

        return info

    def _list_project_files(self) -> tuple[list[str], list[str]]:
        """Return (project_files, config_files) found in the workspace."""
        project_files: list[str] = []
        config_files: list[str] = []

        for name in _PROJECT_MARKERS:
            # Handle subdirectory markers like "mvn/pom.xml".
            parts = name.split("/")
            target = self.cwd
            for part in parts:
                target = target / part
            if target.is_file() and not is_binary_file(target):
                project_files.append(name)

        for name in _CONFIG_FILES:
            if (self.cwd / name).is_file() and not is_binary_file(self.cwd / name):
                config_files.append(name)

        return project_files, config_files

    def _infer_project_type(self, project_files: list[str]) -> str | None:
        """Crudely infer project type from manifest filenames."""
        names = {name.split("/")[-1] for name in project_files}

        if "package.json" in names:
            if "pyproject.toml" in names or "requirements.txt" in names or "manage.py" in names:
                return "full-stack"
            return "node"
        if "pyproject.toml" in names or "requirements.txt" in names or "manage.py" in names:
            return "python"
        if "Dockerfile" in names or "docker-compose.yml" in names:
            return "docker"
        if "Cargo.toml" in names:
            return "rust"
        if "go.mod" in names:
            return "go"

        return None


def validate_workspace(cwd: Path | str | None = None) -> WorkspaceInfo:
    """Convenience function: validate and inspect *cwd* (default: current dir)."""
    return WorkspaceValidator(cwd).validate()


def is_workspace_ambiguous(cwd: Path | str | None = None) -> bool:
    """Return True when the workspace state makes user intent unclear."""
    return validate_workspace(cwd).is_ambiguous


__all__ = [
    "GitInfo",
    "WorkspaceInfo",
    "WorkspaceValidator",
    "validate_workspace",
    "is_workspace_ambiguous",
]

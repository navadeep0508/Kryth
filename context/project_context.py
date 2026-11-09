"""
KRYTH Project Context Manager.

Central shared context for the agent: workspace root, CWD, project type,
frameworks, runtimes, package managers, dependencies, entry points, git
status, architecture, config, and env var names.

Builds on `WorkspaceValidator` (Phase 1) and `ProjectDetector` (existing
project detection) to produce a single `ProjectContext` object that every
component can read without re-inspecting the workspace.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import platform
from typing import Any

from config.config import Config
from context.workspace import WorkspaceInfo, WorkspaceValidator, validate_workspace
from tools.core.project import (
    DependencyInfo,
    EndpointInfo,
    ProjectDetector,
    ProjectPlan,
    RuntimeInfo,
    ServiceInfo,
    detect_project,
)


@dataclass
class ProjectContext:
    """Complete shared context about the workspace and detected project."""

    # --- Workspace (Phase 1) ---
    workspace: WorkspaceInfo

    # --- Detected project (Phase 2) ---
    project_type: str = "unknown"
    frameworks: list[str] = field(default_factory=list)
    runtimes: list[str] = field(default_factory=list)
    package_managers: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    env_vars: list[str] = field(default_factory=list)
    services: list[ServiceInfo] = field(default_factory=list)
    install_steps: list[str] = field(default_factory=list)
    start_steps: list[str] = field(default_factory=list)
    required_env: list[str] = field(default_factory=list)

    # --- Config ---
    config: Config | None = None
    operating_system: str = field(default_factory=lambda: platform.system().lower())
    shell_type: str = "unknown"
    shell_executable: str | None = None
    virtual_environment: Path | None = None

    @property
    def workspace_root(self) -> Path:
        return self.workspace.path

    @property
    def current_working_directory(self) -> Path:
        return self.workspace.path

    @property
    def active_project(self) -> str | None:
        return self.workspace.path.name if self.workspace.has_project else None

    # --- Derived booleans ---
    @property
    def is_empty_workspace(self) -> bool:
        return self.workspace.is_new_workspace

    @property
    def is_git_repo(self) -> bool:
        return self.workspace.is_git_repo

    @property
    def is_ambiguous(self) -> bool:
        return self.workspace.is_ambiguous

    @property
    def has_project(self) -> bool:
        return self.workspace.has_project

    @property
    def cwd(self) -> Path:
        return self.workspace.path

    @property
    def git_status(self) -> str | None:
        return self.workspace.git.status

    @property
    def git_branch(self) -> str | None:
        return self.workspace.git.branch

    @property
    def git_commit(self) -> str | None:
        return self.workspace.git.commit

    @property
    def project_files(self) -> list[str]:
        return self.workspace.project_files

    @property
    def config_files(self) -> list[str]:
        return self.workspace.config_files


class ProjectContextManager:
    """Builds and holds the central shared project context."""

    def __init__(self, config: Config) -> None:
        self.config = config

    # -- public API ----------------------------------------------------------

    def build(self, cwd: Path | str | None = None) -> ProjectContext:
        """Build a ProjectContext for *cwd* (default: config.cwd).

        Does not validate ambiguity — use `validate_and_build` when you want
        the caller to decide what to do with an ambiguous workspace.
        """
        resolved = Path(cwd or self.config.cwd).resolve()
        workspace = WorkspaceValidator(resolved).validate()
        context = ProjectContext(workspace=workspace, config=self.config)
        try:
            from tools.core.shells import select_shell
            shell = select_shell(preferred=self.config.terminal_shell, cwd=resolved)
            context.shell_type = shell.info.type.value
            context.shell_executable = shell.info.executable
        except (RuntimeError, ValueError):
            context.shell_type = self.config.terminal_shell or "unknown"
        for candidate in (resolved / ".venv", resolved / "venv", resolved / "backend" / ".venv"):
            if candidate.is_dir():
                context.virtual_environment = candidate
                break

        if workspace.exists and workspace.is_dir and workspace.project_files:
            self._merge_project_detection(context, resolved)

        return context

    def validate_and_build(
        self, cwd: Path | str | None = None
    ) -> tuple[ProjectContext, str | None]:
        """Build context and return an ambiguity message when the workspace is
        unclear. Returns (context, None) when the workspace is unambiguous.
        """
        context = self.build(cwd)

        if context.is_ambiguous:
            hint = self._ambiguity_hint(context)
            return context, hint

        return context, None

    # -- internal ------------------------------------------------------------

    def _merge_project_detection(
        self, context: ProjectContext, resolved: Path
    ) -> None:
        """Run ProjectDetector and fold results into the context."""
        try:
            plan: ProjectPlan = detect_project(resolved)
        except Exception:
            return

        context.project_type = plan.project_type
        context.services = list(plan.services)
        context.install_steps = list(plan.install_steps)
        context.start_steps = list(plan.start_steps)
        context.required_env = list(plan.required_env)

        # Collect frameworks from service types.
        frameworks: list[str] = []
        runtimes: list[str] = []
        package_managers: list[str] = []
        entry_points: list[str] = []
        dependencies: list[str] = []
        env_vars: list[str] = []

        for svc in plan.services:
            if svc.type and svc.type not in frameworks:
                frameworks.append(svc.type)
            if svc.runtime.name and svc.runtime.name not in runtimes:
                runtimes.append(svc.runtime.name)
            if svc.dependencies.manager and svc.dependencies.manager not in package_managers:
                package_managers.append(svc.dependencies.manager)
            if svc.entry_point:
                entry_points.append(str(svc.entry_point))
            env_vars.extend(svc.env_vars)

        # Also include env vars detected at workspace level.
        for var in context.workspace.config_files:
            pass  # config files already listed

        context.frameworks = frameworks
        context.runtimes = runtimes
        context.package_managers = package_managers
        context.entry_points = entry_points
        context.dependencies = dependencies
        context.env_vars = list(dict.fromkeys(env_vars))

    def _ambiguity_hint(self, context: ProjectContext) -> str:
        """Build a human-readable hint for an ambiguous workspace."""
        lines = [
            f"Workspace `{context.cwd}` appears to be empty or new.",
            "No project files or git history were detected.",
        ]
        if context.config_files:
            lines.append(
                f"Config files found: {', '.join(context.config_files)}."
            )
        lines.append(
            "Did you mean to start a new project here, or point KRYTH at a "
            "different directory?"
        )
        return " ".join(lines)


def build_project_context(
    config: Config, cwd: Path | str | None = None
) -> ProjectContext:
    """Convenience function: build a ProjectContext."""
    return ProjectContextManager(config).build(cwd)


def validate_and_build_project_context(
    config: Config, cwd: Path | str | None = None
) -> tuple[ProjectContext, str | None]:
    """Convenience function: build context with ambiguity check."""
    return ProjectContextManager(config).validate_and_build(cwd)


__all__ = [
    "ProjectContext",
    "ProjectContextManager",
    "build_project_context",
    "validate_and_build_project_context",
]

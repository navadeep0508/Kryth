"""
KRYTH Project Detection and Run Planner.

Automatically inspects a workspace to identify project type, dependencies,
entry points, and startup commands. Builds execution plans for running
projects without assuming a specific framework.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RuntimeInfo:
    """Detected runtime requirements."""

    name: str  # e.g. "python", "node", "docker"
    version: str | None = None
    version_file: str | None = None  # file that declares the version


@dataclass
class DependencyInfo:
    """Detected dependencies."""

    manager: str  # e.g. "pip", "npm", "poetry"
    lock_file: str | None = None
    manifest_file: str | None = None


@dataclass
class EndpointInfo:
    """Detected service endpoints."""

    port: int | None = None
    url: str | None = None
    description: str | None = None


@dataclass
class ServiceInfo:
    """A single service in a project (backend, frontend, etc.)."""

    name: str
    type: str  # e.g. "fastapi", "react", "vite", "django", "flask", "node"
    runtime: RuntimeInfo
    dependencies: DependencyInfo
    entry_point: str | None = None  # file or module path
    start_command: str | None = None
    install_command: str | None = None
    env_vars: list[str] = field(default_factory=list)
    endpoints: list[EndpointInfo] = field(default_factory=list)
    cwd: Path | None = None  # working directory for this service


@dataclass
class ProjectPlan:
    """Execution plan for a project."""

    project_type: str  # e.g. "fastapi", "react", "full-stack", "python-script"
    services: list[ServiceInfo] = field(default_factory=list)
    install_steps: list[str] = field(default_factory=list)
    start_steps: list[str] = field(default_factory=list)
    required_env: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"PROJECT DETECTED: {self.project_type}"]
        for svc in self.services:
            lines.append(
                f"  {svc.name}: {svc.type} ({svc.runtime.name})"
            )
            if svc.start_command:
                lines.append(f"    start: {svc.start_command}")
            if svc.endpoints:
                for ep in svc.endpoints:
                    lines.append(f"    endpoint: {ep.url or ep.port}")
        if self.install_steps:
            lines.append("Install steps:")
            for step in self.install_steps:
                lines.append(f"  - {step}")
        if self.start_steps:
            lines.append("Start steps:")
            for step in self.start_steps:
                lines.append(f"  - {step}")
        return "\n".join(lines)


class ProjectDetector:
    """Inspects a workspace to detect project type and generate a run plan."""

    # Manifest files checked for each project type.
    _MANIFESTS = {
        "python": ["pyproject.toml", "requirements.txt", "setup.py", "Pipfile"],
        "node": ["package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"],
        "docker": ["Dockerfile", "docker-compose.yml", "docker-compose.yaml"],
    }

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd

    def detect(self) -> ProjectPlan:
        """Run full detection and return a ProjectPlan."""
        files = self._list_manifest_files()
        runtimes = self._detect_runtimes(files)

        plan = ProjectPlan(project_type="unknown")

        is_node = "node" in runtimes
        is_python = "python" in runtimes
        is_docker = "docker" in runtimes

        if is_node and is_python:
            plan.project_type = "full-stack"
        elif is_node:
            plan.project_type = self._detect_node_framework(files)
        elif is_python:
            plan.project_type = self._detect_python_framework(files)
        elif is_docker:
            plan.project_type = "docker"
        else:
            plan.project_type = "unknown"

        # Detect services.
        if is_python:
            plan.services.extend(self._detect_python_services(files, runtimes))
        if is_node:
            plan.services.extend(self._detect_node_services(files, runtimes))
        if is_docker:
            plan.services.extend(self._detect_docker_services(files))

        # Build install/start steps.
        plan.install_steps = self._build_install_steps(plan, runtimes)
        plan.start_steps = self._build_start_steps(plan)
        plan.required_env = self._detect_env_vars(files)

        return plan

    # -- file listing -------------------------------------------------------

    def _list_manifest_files(self) -> dict[str, Path | None]:
        found: dict[str, Path | None] = {}
        for category, names in self._MANIFESTS.items():
            for name in names:
                p = self.cwd / name
                if p.is_file():
                    found[name] = p
                    break
        # Also check for common framework-specific files.
        extra = [
            "package.json", "pyproject.toml", "requirements.txt",
            "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
            "manage.py", "app.py", "main.py", "wsgi.py", "asgi.py",
            "vite.config.js", "vite.config.ts", "next.config.js",
            "nuxt.config.ts", "angular.json", "Procfile", ".env.example",
        ]
        for name in extra:
            p = self.cwd / name
            if p.is_file() and name not in found:
                found[name] = p
        return found

    # -- runtime detection --------------------------------------------------

    def _detect_runtimes(self, files: dict[str, Path | None]) -> dict[str, RuntimeInfo]:
        runtimes: dict[str, RuntimeInfo] = {}

        if files.get("package.json"):
            runtimes["node"] = RuntimeInfo(name="node", version_file="package.json")

        if files.get("pyproject.toml") or files.get("requirements.txt"):
            runtimes["python"] = RuntimeInfo(
                name="python",
                version_file=files.get("pyproject.toml") or files.get("requirements.txt"),
            )

        if files.get("Dockerfile") or files.get("docker-compose.yml"):
            runtimes["docker"] = RuntimeInfo(name="docker")

        return runtimes

    # -- Python framework detection -----------------------------------------

    def _detect_python_framework(self, files: dict[str, Path | None]) -> str:
        # Check pyproject.toml for framework dependencies.
        pyproject = files.get("pyproject.toml")
        if pyproject:
            try:
                content = pyproject.read_text(encoding="utf-8", errors="replace")
                lowered = content.lower()
                if "fastapi" in lowered:
                    return "fastapi"
                if "django" in lowered:
                    return "django"
                if "flask" in lowered:
                    return "flask"
            except OSError:
                pass

        # Check requirements.txt.
        req = files.get("requirements.txt")
        if req:
            try:
                content = req.read_text(encoding="utf-8", errors="replace")
                lowered = content.lower()
                if "fastapi" in lowered:
                    return "fastapi"
                if "django" in lowered:
                    return "django"
                if "flask" in lowered:
                    return "flask"
            except OSError:
                pass

        # Check for entry point files.
        if files.get("manage.py"):
            return "django"
        if files.get("app.py") or files.get("main.py"):
            # Could be flask or fastapi; check content.
            for name in ("app.py", "main.py"):
                p = files.get(name)
                if p:
                    try:
                        content = p.read_text(encoding="utf-8", errors="replace")
                        lowered = content.lower()
                        if "fastapi" in lowered:
                            return "fastapi"
                        if "flask" in lowered:
                            return "flask"
                    except OSError:
                        pass

        return "python-script"

    def _detect_python_services(
        self, files: dict[str, Path | None], runtimes: dict[str, RuntimeInfo]
    ) -> list[ServiceInfo]:
        services: list[ServiceInfo] = []
        python_rt = runtimes.get("python")
        if not python_rt:
            return services

        dep_mgr = "pip"
        lock_file = None
        manifest = None
        if files.get("pyproject.toml"):
            dep_mgr = "pip"
            manifest = "pyproject.toml"
        elif files.get("requirements.txt"):
            dep_mgr = "pip"
            manifest = "requirements.txt"
        elif files.get("Pipfile"):
            dep_mgr = "pipenv"
            manifest = "Pipfile"

        deps = DependencyInfo(
            manager=dep_mgr,
            lock_file=lock_file,
            manifest_file=manifest,
        )

        framework = self._detect_python_framework(files)

        if framework == "fastapi":
            services.append(ServiceInfo(
                name="backend",
                type="fastapi",
                runtime=python_rt,
                dependencies=deps,
                entry_point=files.get("main.py") or files.get("app.py"),
                start_command=self._fastapi_start_command(files),
                install_command="pip install -r requirements.txt" if files.get("requirements.txt") else "pip install -e .",
                env_vars=self._read_env_example(files),
                cwd=self.cwd,
            ))
        elif framework == "django":
            services.append(ServiceInfo(
                name="backend",
                type="django",
                runtime=python_rt,
                dependencies=deps,
                entry_point=files.get("manage.py"),
                start_command="python manage.py runserver",
                install_command="pip install -r requirements.txt" if files.get("requirements.txt") else "pip install -e .",
                env_vars=self._read_env_example(files),
                cwd=self.cwd,
            ))
        elif framework == "flask":
            services.append(ServiceInfo(
                name="backend",
                type="flask",
                runtime=python_rt,
                dependencies=deps,
                entry_point=files.get("app.py") or files.get("main.py"),
                start_command="flask run",
                install_command="pip install -r requirements.txt" if files.get("requirements.txt") else "pip install -e .",
                env_vars=self._read_env_example(files),
                cwd=self.cwd,
            ))
        else:
            # Generic Python script.
            entry = files.get("main.py") or files.get("app.py")
            if entry:
                services.append(ServiceInfo(
                    name="script",
                    type="python-script",
                    runtime=python_rt,
                    dependencies=deps,
                    entry_point=entry,
                    start_command=f"python {entry.name}",
                    install_command="pip install -r requirements.txt" if files.get("requirements.txt") else None,
                    env_vars=self._read_env_example(files),
                    cwd=self.cwd,
                ))

        return services

    def _fastapi_start_command(self, files: dict[str, Path | None]) -> str | None:
        entry = files.get("main.py") or files.get("app.py")
        if not entry:
            return None
        module = entry.stem
        return f"uvicorn {module}:app --reload"

    # -- Node.js framework detection ----------------------------------------

    def _detect_node_framework(self, files: dict[str, Path | None]) -> str:
        pkg = files.get("package.json")
        if not pkg:
            return "node"
        try:
            content = pkg.read_text(encoding="utf-8", errors="replace")
            data = json.loads(content)
        except (OSError, json.JSONDecodeError):
            return "node"

        deps = {}
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            deps.update(data.get(key, {}))

        lowered_deps = {k.lower() for k in deps}

        if "next" in lowered_deps:
            return "nextjs"
        if "react" in lowered_deps:
            # Check for Vite.
            if files.get("vite.config.js") or files.get("vite.config.ts"):
                return "vite"
            # Check for Create React App.
            if "react-scripts" in lowered_deps:
                return "react"
            return "react"
        if "vue" in lowered_deps:
            return "vue"
        if "express" in lowered_deps:
            return "express"
        if "nestjs" in lowered_deps:
            return "nestjs"

        return "node"

    def _detect_node_services(
        self, files: dict[str, Path | None], runtimes: dict[str, RuntimeInfo]
    ) -> list[ServiceInfo]:
        services: list[ServiceInfo] = []
        node_rt = runtimes.get("node")
        if not node_rt:
            return services

        framework = self._detect_node_framework(files)

        dep_mgr = "npm"
        manifest = "package.json"
        lock_file = None
        if files.get("package-lock.json"):
            lock_file = "package-lock.json"
        elif files.get("yarn.lock"):
            dep_mgr = "yarn"
            lock_file = "yarn.lock"
        elif files.get("pnpm-lock.yaml"):
            dep_mgr = "pnpm"
            lock_file = "pnpm-lock.yaml"

        deps = DependencyInfo(
            manager=dep_mgr,
            lock_file=lock_file,
            manifest_file=manifest,
        )

        install_cmd = {
            "npm": "npm install",
            "yarn": "yarn install",
            "pnpm": "pnpm install",
        }.get(dep_mgr, "npm install")

        if framework == "nextjs":
            services.append(ServiceInfo(
                name="frontend",
                type="nextjs",
                runtime=node_rt,
                dependencies=deps,
                entry_point=files.get("package.json"),
                start_command="npm run dev",
                install_command=install_cmd,
                env_vars=self._read_env_example(files),
                endpoints=[EndpointInfo(port=3000, url="http://localhost:3000", description="Next.js dev server")],
                cwd=self.cwd,
            ))
        elif framework == "vite":
            services.append(ServiceInfo(
                name="frontend",
                type="vite",
                runtime=node_rt,
                dependencies=deps,
                entry_point=files.get("package.json"),
                start_command="npm run dev",
                install_command=install_cmd,
                env_vars=self._read_env_example(files),
                endpoints=[EndpointInfo(port=5173, url="http://localhost:5173", description="Vite dev server")],
                cwd=self.cwd,
            ))
        elif framework == "react":
            services.append(ServiceInfo(
                name="frontend",
                type="react",
                runtime=node_rt,
                dependencies=deps,
                entry_point=files.get("package.json"),
                start_command="npm start",
                install_command=install_cmd,
                env_vars=self._read_env_example(files),
                endpoints=[EndpointInfo(port=3000, url="http://localhost:3000", description="React dev server")],
                cwd=self.cwd,
            ))
        elif framework == "express":
            services.append(ServiceInfo(
                name="backend",
                type="express",
                runtime=node_rt,
                dependencies=deps,
                entry_point=files.get("package.json"),
                start_command="npm start",
                install_command=install_cmd,
                env_vars=self._read_env_example(files),
                endpoints=[EndpointInfo(port=3000, url="http://localhost:3000", description="Express server")],
                cwd=self.cwd,
            ))
        else:
            services.append(ServiceInfo(
                name="app",
                type="node",
                runtime=node_rt,
                dependencies=deps,
                entry_point=files.get("package.json"),
                start_command="npm start",
                install_command=install_cmd,
                env_vars=self._read_env_example(files),
                cwd=self.cwd,
            ))

        return services

    # -- Docker detection ---------------------------------------------------

    def _detect_docker_services(self, files: dict[str, Path | None]) -> list[ServiceInfo]:
        services: list[ServiceInfo] = []
        if files.get("docker-compose.yml") or files.get("docker-compose.yaml"):
            compose_file = files.get("docker-compose.yml") or files.get("docker-compose.yaml")
            services.append(ServiceInfo(
                name="docker-compose",
                type="docker",
                runtime=RuntimeInfo(name="docker"),
                dependencies=DependencyInfo(manager="docker", manifest_file=str(compose_file.name) if compose_file else None),
                start_command="docker-compose up",
                cwd=self.cwd,
            ))
        elif files.get("Dockerfile"):
            services.append(ServiceInfo(
                name="app",
                type="docker",
                runtime=RuntimeInfo(name="docker"),
                dependencies=DependencyInfo(manager="docker", manifest_file="Dockerfile"),
                start_command="docker build -t app . && docker run app",
                cwd=self.cwd,
            ))
        return services

    # -- helpers ------------------------------------------------------------

    def _read_env_example(self, files: dict[str, Path | None]) -> list[str]:
        env_example = self.cwd / ".env.example"
        if env_example.is_file():
            try:
                content = env_example.read_text(encoding="utf-8", errors="replace")
                vars_found: list[str] = []
                for line in content.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key = line.split("=", 1)[0].strip()
                        if key:
                            vars_found.append(key)
                return vars_found
            except OSError:
                pass
        return []

    def _detect_env_vars(self, files: dict[str, Path | None]) -> list[str]:
        env = self.cwd / ".env"
        if env.is_file():
            try:
                content = env.read_text(encoding="utf-8", errors="replace")
                vars_found: list[str] = []
                for line in content.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key = line.split("=", 1)[0].strip()
                        if key:
                            vars_found.append(key)
                return vars_found
            except OSError:
                pass
        return self._read_env_example(files)

    def _build_install_steps(self, plan: ProjectPlan, runtimes: dict[str, RuntimeInfo]) -> list[str]:
        steps: list[str] = []
        seen: set[str] = set()
        for svc in plan.services:
            if svc.install_command and svc.install_command not in seen:
                steps.append(svc.install_command)
                seen.add(svc.install_command)
        return steps

    def _build_start_steps(self, plan: ProjectPlan) -> list[str]:
        steps: list[str] = []
        for svc in plan.services:
            if svc.start_command:
                steps.append(f"Start {svc.name}: {svc.start_command}")
        return steps


def detect_project(cwd: Path | str | None = None) -> ProjectPlan:
    """Convenience function: detect project at *cwd* (default: current dir)."""
    if cwd is None:
        cwd = Path.cwd()
    return ProjectDetector(Path(cwd)).detect()
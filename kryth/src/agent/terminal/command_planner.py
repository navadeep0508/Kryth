"""Phase 8 & 9 — Command Planner + Smart Command Generator.

Instead of blindly executing commands:
  1. Detect project type from marker files.
  2. Build an execution plan (check env → install → compile → test).
  3. Generate the correct commands for the detected stack.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class CommandStep:
    label: str
    command: str
    timeout: int = 60
    skip_if_missing: str = ""   # skip this step if this binary is not on PATH
    required: bool = True       # if False, failure of this step does not abort the plan


@dataclass
class ExecutionPlan:
    goal: str
    stack: str
    steps: list[CommandStep] = field(default_factory=list)
    cwd: str = "."

    def commands(self) -> list[str]:
        return [s.command for s in self.steps]

    def format(self) -> str:
        lines = [f"Plan: {self.goal} [{self.stack}]"]
        for i, step in enumerate(self.steps, 1):
            req = "" if step.required else " (optional)"
            lines.append(f"  {i}. {step.label}: {step.command}{req}")
        return "\n".join(lines)


class CommandPlanner:
    """Detect project type and build smart execution plans."""

    def detect_stack(self, cwd: str = ".") -> str:
        base = Path(cwd)
        if (base / "pyproject.toml").exists() or (base / "setup.py").exists():
            # Distinguish uv vs pip
            if shutil.which("uv"):
                return "python-uv"
            return "python-pip"
        if (base / "requirements.txt").exists():
            return "python-pip"
        if (base / "Cargo.toml").exists():
            return "rust"
        if (base / "go.mod").exists():
            return "go"
        if (base / "package.json").exists():
            return self._detect_node(base)
        if (base / "pom.xml").exists():
            return "maven"
        if (base / "build.gradle").exists() or (base / "build.gradle.kts").exists():
            return "gradle"
        if (base / "Makefile").exists():
            return "make"
        return "unknown"

    def _detect_node(self, base: Path) -> str:
        if (base / "pnpm-lock.yaml").exists():
            return "node-pnpm"
        if (base / "yarn.lock").exists():
            return "node-yarn"
        if (base / "bun.lockb").exists():
            return "node-bun"
        return "node-npm"

    def plan_tests(self, cwd: str = ".") -> ExecutionPlan:
        """Build a plan to run tests for the detected stack."""
        stack = self.detect_stack(cwd)
        plan = ExecutionPlan(goal="run tests", stack=stack, cwd=cwd)

        if stack in ("python-pip", "python-uv"):
            runner = "uv run pytest" if stack == "python-uv" else "python -m pytest"
            plan.steps = [
                CommandStep("check env", f"{'uv' if stack == 'python-uv' else 'python'} --version", timeout=5),
                CommandStep("install deps", self._install_cmd(stack, cwd), timeout=120),
                CommandStep("run tests", f"{runner} -q --tb=short", timeout=300),
            ]
        elif stack == "rust":
            plan.steps = [
                CommandStep("check toolchain", "cargo --version", timeout=5),
                CommandStep("build", "cargo build", timeout=180),
                CommandStep("run tests", "cargo test", timeout=300),
            ]
        elif stack == "go":
            plan.steps = [
                CommandStep("check go", "go version", timeout=5),
                CommandStep("tidy modules", "go mod tidy", timeout=60),
                CommandStep("run tests", "go test ./...", timeout=300),
            ]
        elif stack.startswith("node"):
            pm = self._node_pm(stack)
            plan.steps = [
                CommandStep("check node", "node --version", timeout=5),
                CommandStep("install deps", f"{pm} install", timeout=180),
                CommandStep("run tests", f"{pm} test", timeout=300),
            ]
        elif stack == "maven":
            plan.steps = [
                CommandStep("check java", "java --version", timeout=5),
                CommandStep("run tests", "mvn test -q", timeout=300),
            ]
        elif stack == "gradle":
            plan.steps = [
                CommandStep("check java", "java --version", timeout=5),
                CommandStep("run tests", "./gradlew test", timeout=300),
            ]
        elif stack == "make":
            plan.steps = [
                CommandStep("run tests", "make test", timeout=300),
            ]
        else:
            plan.steps = [
                CommandStep("run tests", "echo 'No test runner detected'", timeout=5, required=False),
            ]

        return plan

    def plan_build(self, cwd: str = ".") -> ExecutionPlan:
        """Build a plan to compile / build the project."""
        stack = self.detect_stack(cwd)
        plan = ExecutionPlan(goal="build project", stack=stack, cwd=cwd)

        if stack in ("python-pip", "python-uv"):
            plan.steps = [
                CommandStep("install", self._install_cmd(stack, cwd), timeout=120),
            ]
        elif stack == "rust":
            plan.steps = [
                CommandStep("build", "cargo build --release", timeout=300),
            ]
        elif stack == "go":
            plan.steps = [
                CommandStep("build", "go build ./...", timeout=120),
            ]
        elif stack.startswith("node"):
            pm = self._node_pm(stack)
            plan.steps = [
                CommandStep("install", f"{pm} install", timeout=180),
                CommandStep("build", f"{pm} run build", timeout=180, required=False),
            ]
        elif stack == "maven":
            plan.steps = [
                CommandStep("build", "mvn package -DskipTests -q", timeout=300),
            ]
        elif stack == "gradle":
            plan.steps = [
                CommandStep("build", "./gradlew build -x test", timeout=300),
            ]
        elif stack == "make":
            plan.steps = [
                CommandStep("build", "make", timeout=300),
            ]
        else:
            plan.steps = []

        return plan

    def plan_install(self, cwd: str = ".") -> ExecutionPlan:
        """Build a plan to install dependencies."""
        stack = self.detect_stack(cwd)
        plan = ExecutionPlan(goal="install dependencies", stack=stack, cwd=cwd)
        plan.steps = [CommandStep("install", self._install_cmd(stack, cwd), timeout=180)]
        return plan

    def plan_custom(self, goal: str, commands: list[str], cwd: str = ".") -> ExecutionPlan:
        """Wrap a custom command list in a plan."""
        stack = self.detect_stack(cwd)
        plan = ExecutionPlan(goal=goal, stack=stack, cwd=cwd)
        plan.steps = [
            CommandStep(label=f"step {i+1}", command=cmd)
            for i, cmd in enumerate(commands)
        ]
        return plan

    def _install_cmd(self, stack: str, cwd: str) -> str:
        base = Path(cwd)
        if stack == "python-uv":
            return "uv sync"
        if (base / "pyproject.toml").exists():
            return "pip install -e ."
        if (base / "requirements.txt").exists():
            return "pip install -r requirements.txt"
        return "pip install ."

    def _node_pm(self, stack: str) -> str:
        pm_map = {
            "node-pnpm": "pnpm",
            "node-yarn": "yarn",
            "node-bun": "bun",
            "node-npm": "npm",
        }
        return pm_map.get(stack, "npm")


# Module-level singleton
command_planner = CommandPlanner()

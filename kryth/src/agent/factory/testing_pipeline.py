"""TestingPipeline — run unit, integration, API, and build verification tests.

Reuses:
- ``agent.terminal.shell.get_shell()`` / ``agent.tools._terminal.shell_exec``
  for running test commands in the persistent shell
- ``agent.orchestration.repo_intelligence.analyze_repo()`` to detect test frameworks
- ``agent.mission.mission_manager.MissionManager`` for artifact tracking
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TestSuiteResult:
    suite: str          # "unit" | "integration" | "api" | "build" | "browser"
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration: float = 0.0
    output: str = ""
    success: bool = True

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.errors + self.skipped

    def to_dict(self) -> dict:
        return {"suite": self.suite, "passed": self.passed, "failed": self.failed,
                "errors": self.errors, "skipped": self.skipped, "total": self.total,
                "duration": self.duration, "success": self.success}


@dataclass
class PipelineResult:
    root: str
    suites: list[TestSuiteResult] = field(default_factory=list)
    overall_success: bool = True
    generated_tests: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "suites": [s.to_dict() for s in self.suites],
            "overall_success": self.overall_success,
            "generated_tests": self.generated_tests,
        }

    def summary(self) -> str:
        lines = [f"Testing Pipeline — {self.root}"]
        for s in self.suites:
            icon = "✓" if s.success else "✗"
            lines.append(f"  {icon} {s.suite}: {s.passed} passed / {s.failed} failed / {s.skipped} skipped")
        if self.generated_tests:
            lines.append(f"  Auto-generated {len(self.generated_tests)} test stubs")
        return "\n".join(lines)


class TestingPipeline:
    """Discover and run the project's test suites.

    Parameters
    ----------
    root:
        Project root directory.
    """

    def __init__(self, root: str = ".") -> None:
        self.root = root

    def run_all(self) -> PipelineResult:
        """Detect test frameworks and run all available test suites."""
        result = PipelineResult(root=self.root)
        commands = self._detect_test_commands()

        for suite, cmd in commands.items():
            sr = self._run_suite(suite, cmd)
            result.suites.append(sr)
            if not sr.success:
                result.overall_success = False

        return result

    def run_suite(self, suite: str) -> TestSuiteResult:
        """Run a single named suite (unit | integration | build)."""
        commands = self._detect_test_commands()
        cmd = commands.get(suite, self._fallback_command(suite))
        return self._run_suite(suite, cmd)

    def generate_missing_tests(self, paths: list[str]) -> list[str]:
        """Generate minimal test stubs for files that lack tests.

        Returns list of created test file paths.
        """
        generated = []
        for path_str in paths:
            src = Path(path_str)
            if not src.exists() or not src.suffix == ".py":
                continue
            test_path = self._test_path_for(src)
            if test_path.exists():
                continue
            stub = self._generate_stub(src, test_path)
            if stub:
                test_path.parent.mkdir(parents=True, exist_ok=True)
                test_path.write_text(stub, encoding="utf-8")
                generated.append(str(test_path))
                logger.info("Generated test stub: %s", test_path)
        return generated

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _detect_test_commands(self) -> dict[str, str]:
        root = Path(self.root)
        cmds: dict[str, str] = {}

        if (root / "pyproject.toml").exists() or (root / "setup.py").exists():
            cmds["unit"] = "python -m pytest -x -q --tb=short 2>&1 | head -100"
        if (root / "package.json").exists():
            cmds["unit"] = "npm test -- --watchAll=false 2>&1 | head -100"
        if (root / "Makefile").exists():
            try:
                makefile = (root / "Makefile").read_text(errors="ignore")
                if "test:" in makefile:
                    cmds["unit"] = "make test 2>&1 | head -100"
            except OSError:
                pass
        if not cmds:
            cmds["unit"] = "python -m pytest -x -q 2>&1 | head -100"

        return cmds

    def _fallback_command(self, suite: str) -> str:
        mapping = {
            "unit": "python -m pytest -x -q 2>&1 | head -60",
            "integration": "python -m pytest tests/integration/ -x -q 2>&1 | head -60",
            "build": "python -m build --no-isolation 2>&1 | head -40",
            "api": "python -m pytest tests/api/ -x -q 2>&1 | head -60",
        }
        return mapping.get(suite, f"python -m pytest -k {suite} -x -q 2>&1 | head -60")

    def _run_suite(self, suite: str, cmd: str) -> TestSuiteResult:
        try:
            from agent.terminal.shell import get_shell
            shell = get_shell()
            event = shell.execute(cmd, timeout=120)
            output = str(event.data or "")
            return _parse_pytest_output(suite, output)
        except Exception as exc:
            logger.warning("Test suite '%s' failed to run: %s", suite, exc)
            return TestSuiteResult(suite=suite, success=False, output=str(exc))

    def _test_path_for(self, src: Path) -> Path:
        parts = src.parts
        if "src" in parts:
            idx = list(parts).index("src")
            rel = Path(*parts[idx + 1:])
        else:
            rel = src.relative_to(self.root) if self.root in str(src) else src
        return Path(self.root) / "tests" / f"test_{rel.name}"

    def _generate_stub(self, src: Path, test_path: Path) -> str:
        try:
            import ast
            tree = ast.parse(src.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            return ""
        funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")]
        if not funcs:
            return ""
        module = src.stem
        lines = [f'"""Auto-generated test stubs for {module}."""', "import pytest", ""]
        for fn in funcs[:10]:
            lines += [f"def test_{fn}():", f"    # TODO: implement test for {fn}", "    pass", ""]
        return "\n".join(lines)


def _parse_pytest_output(suite: str, output: str) -> TestSuiteResult:
    sr = TestSuiteResult(suite=suite, output=output[:2000])
    m = re.search(r"(\d+) passed", output)
    if m:
        sr.passed = int(m.group(1))
    m = re.search(r"(\d+) failed", output)
    if m:
        sr.failed = int(m.group(1))
        sr.success = False
    m = re.search(r"(\d+) error", output)
    if m:
        sr.errors = int(m.group(1))
        sr.success = False
    m = re.search(r"(\d+) skipped", output)
    if m:
        sr.skipped = int(m.group(1))
    if "no tests ran" in output.lower() or "collected 0 items" in output.lower():
        sr.success = True   # no tests is not a failure
    return sr

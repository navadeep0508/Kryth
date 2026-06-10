"""BrowserQA — browser-based quality assurance using persistent profiles.

Reuses:
- ``agent.browser.BrowserProfileManager`` for persistent sessions
- ``agent.browser.snapshot.SnapshotManager`` for pre-test snapshots
- ``agent.tools._opencli`` / browser_use for automation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class QACheck:
    name: str
    url: str
    check_type: str   # "login" | "navigation" | "form" | "screenshot" | "responsive"
    passed: bool = False
    error: str = ""
    screenshot_path: str = ""
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"name": self.name, "url": self.url, "check_type": self.check_type,
                "passed": self.passed, "error": self.error,
                "screenshot_path": self.screenshot_path, "details": self.details}


@dataclass
class BrowserQAReport:
    profile: str
    base_url: str
    checks: list[QACheck] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def failed(self) -> int:
        return sum(1 for c in self.checks if not c.passed)

    @property
    def success(self) -> bool:
        return self.failed == 0

    def to_dict(self) -> dict:
        return {
            "profile": self.profile,
            "base_url": self.base_url,
            "checks": [c.to_dict() for c in self.checks],
            "passed": self.passed,
            "failed": self.failed,
            "success": self.success,
        }

    def summary(self) -> str:
        lines = [f"Browser QA — {self.base_url} (profile: {self.profile})"]
        for c in self.checks:
            icon = "✓" if c.passed else "✗"
            lines.append(f"  {icon} [{c.check_type}] {c.name}")
            if c.error:
                lines.append(f"       Error: {c.error}")
        lines.append(f"  Result: {self.passed}/{len(self.checks)} passed")
        return "\n".join(lines)


class BrowserQA:
    """Run browser-based QA checks against a running application.

    Parameters
    ----------
    base_url:
        The base URL of the application under test.
    profile:
        Browser profile name to use for persistent sessions.
    screenshots_dir:
        Where to save screenshots.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:3000",
        profile: str = "testing",
        screenshots_dir: Optional[Path] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.profile = profile
        self._screenshots_dir = screenshots_dir or (Path.home() / ".kryth" / "browser_qa_screenshots")
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)

    def run_standard_suite(self) -> BrowserQAReport:
        """Run the standard QA suite: page load, navigation, console errors."""
        report = BrowserQAReport(profile=self.profile, base_url=self.base_url)

        report.checks.append(self._check_page_loads("/"))
        report.checks.append(self._check_console_errors("/"))

        return report

    def run_checks(self, checks: list[dict]) -> BrowserQAReport:
        """Run a custom list of checks.

        Each check dict: {name, path, type}
        """
        report = BrowserQAReport(profile=self.profile, base_url=self.base_url)
        for c in checks:
            check_type = c.get("type", "navigation")
            path = c.get("path", "/")
            name = c.get("name", path)
            if check_type == "console_errors":
                report.checks.append(self._check_console_errors(path, name=name))
            else:
                report.checks.append(self._check_page_loads(path, name=name))
        return report

    def screenshot(self, path: str = "/", label: str = "") -> Optional[Path]:
        """Take a screenshot of *path* and return the file path."""
        try:
            from agent.tools._opencli import browser_screenshot, open_url
            url = f"{self.base_url}{path}"
            open_url(url)
            name = label or path.strip("/").replace("/", "_") or "root"
            dest = self._screenshots_dir / f"{name}.png"
            browser_screenshot(str(dest))
            return dest
        except Exception as exc:
            logger.debug("Screenshot failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_page_loads(self, path: str, name: str = "") -> QACheck:
        url = f"{self.base_url}{path}"
        check = QACheck(name=name or path, url=url, check_type="navigation")
        try:
            from agent.tools._opencli import open_url, browser_get_url
            open_url(url)
            current = browser_get_url()
            check.passed = True
            check.details["final_url"] = current
        except Exception as exc:
            check.passed = False
            check.error = str(exc)
        return check

    def _check_console_errors(self, path: str, name: str = "") -> QACheck:
        url = f"{self.base_url}{path}"
        check = QACheck(name=name or f"{path} (console)", url=url, check_type="console_errors")
        try:
            from agent.tools._browser import check_browser_errors
            result = check_browser_errors(url, wait_seconds=3)
            has_errors = "CONSOLE ERRORS" in result
            check.passed = not has_errors
            if has_errors:
                check.error = result[:200]
            check.details["report"] = result[:500]
        except Exception as exc:
            check.passed = False
            check.error = str(exc)
        return check

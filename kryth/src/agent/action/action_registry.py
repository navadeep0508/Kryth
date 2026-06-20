"""Executor Registry — plugin registry for all UAL executors.

Each executor registers itself here. The ExecutorSelector queries the
registry to score and pick the best executor for any given Action.

Built-in executors (always registered):
  terminal  — shell, git, docker, build, python
  browser   — web forms, navigation, uploads, downloads
  api       — REST/GraphQL/SDK calls (GitHub, Slack, Stripe, AWS …)

Future executors (plug in without changing core):
  desktop   — native UI, file dialogs, OCR, mouse/keyboard
  cloud     — AWS/Azure/GCP/K8s infrastructure
  ide       — VS Code, JetBrains integrations
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.action.action import Action, ActionResult, ActionRisk

logger = logging.getLogger(__name__)


# ── Executor interface ────────────────────────────────────────────────────────

class BaseExecutor(ABC):
    """Interface every executor must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this executor (e.g. 'terminal')."""

    @property
    @abstractmethod
    def capabilities(self) -> list[str]:
        """Human-readable list of what this executor can do."""

    @abstractmethod
    def can_handle(self, action: Action) -> bool:
        """Return True if this executor can attempt the action."""

    @abstractmethod
    def estimate_confidence(self, action: Action) -> float:
        """Return 0–1 confidence that this executor can succeed."""

    @abstractmethod
    def estimate_speed_ms(self, action: Action) -> float:
        """Estimated wall-clock time in ms for this executor."""

    @abstractmethod
    def execute(self, action: Action) -> ActionResult:
        """Execute the action. Never raises — returns ActionResult."""

    def verify(self, action: Action, result: ActionResult) -> ActionResult:
        """Post-execution verification (default: no-op, returns result unchanged)."""
        return result

    def rollback(self, action: Action) -> bool:
        """Undo the action. Returns True if rollback succeeded."""
        return False


# ── Built-in executors ────────────────────────────────────────────────────────

class TerminalExecutor(BaseExecutor):
    """Shell / git / docker / build / Python executor."""

    _KEYWORDS = {
        "build", "compile", "test", "run", "install", "git", "docker",
        "python", "npm", "pip", "make", "gradle", "cargo", "go",
        "lint", "format", "deploy", "script", "bash", "shell", "command",
    }

    @property
    def name(self) -> str:
        return "terminal"

    @property
    def capabilities(self) -> list[str]:
        return [
            "Build / compile code",
            "Run tests",
            "Git operations",
            "Docker build / push",
            "Python / Node / shell scripts",
            "Package installation",
        ]

    def can_handle(self, action: Action) -> bool:
        if "terminal" in action.preferred_executors:
            return True
        goal_lower = action.goal.lower()
        return any(kw in goal_lower for kw in self._KEYWORDS)

    def estimate_confidence(self, action: Action) -> float:
        if "terminal" in action.preferred_executors:
            return 0.95
        goal_lower = action.goal.lower()
        hits = sum(1 for kw in self._KEYWORDS if kw in goal_lower)
        return min(0.9, 0.5 + hits * 0.1)

    def estimate_speed_ms(self, action: Action) -> float:
        return action.estimated_time_ms or 5_000.0

    def execute(self, action: Action) -> ActionResult:
        import subprocess, time
        command = action.params.get("command", "")
        if not command:
            return ActionResult(
                success=False,
                error="TerminalExecutor: no 'command' in action.params",
                executor_used=self.name,
            )
        start = time.time()
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=action.params.get("timeout", 120),
            )
            elapsed = (time.time() - start) * 1000
            output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
            success = proc.returncode == 0
            if not success:
                output += f"\n(exit code: {proc.returncode})"
            return ActionResult(
                success=success,
                output=output.strip(),
                error="" if success else f"Exit {proc.returncode}",
                executor_used=self.name,
                duration_ms=elapsed,
            )
        except subprocess.TimeoutExpired:
            return ActionResult(
                success=False, error="Command timed out", executor_used=self.name,
                duration_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return ActionResult(
                success=False, error=str(e), executor_used=self.name,
                duration_ms=(time.time() - start) * 1000,
            )

    def verify(self, action: Action, result: ActionResult) -> ActionResult:
        if not result.success:
            return result
        for step in action.verification_steps:
            if step.startswith("check_file:"):
                path = step.split(":", 1)[1].strip()
                from pathlib import Path
                if not Path(path).exists():
                    result.verified = False
                    result.verification_detail = f"Expected file not found: {path}"
                    return result
        result.verified = True
        result.verification_detail = "exit code 0"
        return result

    def rollback(self, action: Action) -> bool:
        if not action.rollback_steps:
            return False
        import subprocess
        for step in action.rollback_steps:
            subprocess.run(step, shell=True, capture_output=True)
        return True


class BrowserExecutor(BaseExecutor):
    """Web browser executor — forms, navigation, uploads, persistent sessions."""

    _KEYWORDS = {
        "browse", "web", "website", "url", "form", "upload", "download",
        "click", "navigate", "login", "submit", "scrape", "page", "browser",
        "http", "https",
    }

    @property
    def name(self) -> str:
        return "browser"

    @property
    def capabilities(self) -> list[str]:
        return [
            "Web navigation",
            "Form submission",
            "File uploads / downloads",
            "Persistent sessions",
            "DOM interaction",
            "Screenshot / OCR",
        ]

    def can_handle(self, action: Action) -> bool:
        if "browser" in action.preferred_executors:
            return True
        goal_lower = action.goal.lower()
        return any(kw in goal_lower for kw in self._KEYWORDS)

    def estimate_confidence(self, action: Action) -> float:
        if "browser" in action.preferred_executors:
            return 0.90
        goal_lower = action.goal.lower()
        hits = sum(1 for kw in self._KEYWORDS if kw in goal_lower)
        return min(0.85, 0.4 + hits * 0.12)

    def estimate_speed_ms(self, action: Action) -> float:
        return action.estimated_time_ms or 8_000.0

    def execute(self, action: Action) -> ActionResult:
        import time
        start = time.time()
        try:
            from agent.providers.browser_use_provider import open_url, click, fill, screenshot
            url = action.params.get("url", "")
            if url:
                open_url(url)
            for step in action.params.get("steps", []):
                cmd = step.get("cmd", "")
                if cmd == "click":
                    click(step.get("selector", ""))
                elif cmd == "fill":
                    fill(step.get("selector", ""), step.get("value", ""))
            output = f"Browser action completed for: {action.goal}"
            return ActionResult(
                success=True, output=output, executor_used=self.name,
                duration_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return ActionResult(
                success=False, error=str(e), executor_used=self.name,
                duration_ms=(time.time() - start) * 1000,
            )

    def verify(self, action: Action, result: ActionResult) -> ActionResult:
        result.verified = result.success
        result.verification_detail = "browser action completed" if result.success else result.error
        return result


class APIExecutor(BaseExecutor):
    """REST / GraphQL / SDK executor (GitHub, Slack, Stripe, AWS…)."""

    _KEYWORDS = {
        "api", "rest", "graphql", "webhook", "endpoint", "request",
        "github", "slack", "stripe", "aws", "azure", "gcp", "sdk",
        "post", "get", "patch", "delete", "json", "http",
    }

    @property
    def name(self) -> str:
        return "api"

    @property
    def capabilities(self) -> list[str]:
        return [
            "REST / GraphQL API calls",
            "SDK operations (GitHub, Slack, Stripe, AWS)",
            "Webhook triggers",
            "JSON payload handling",
        ]

    def can_handle(self, action: Action) -> bool:
        if "api" in action.preferred_executors:
            return True
        goal_lower = action.goal.lower()
        return any(kw in goal_lower for kw in self._KEYWORDS)

    def estimate_confidence(self, action: Action) -> float:
        if "api" in action.preferred_executors:
            return 0.92
        goal_lower = action.goal.lower()
        hits = sum(1 for kw in self._KEYWORDS if kw in goal_lower)
        return min(0.88, 0.45 + hits * 0.1)

    def estimate_speed_ms(self, action: Action) -> float:
        return action.estimated_time_ms or 3_000.0

    def execute(self, action: Action) -> ActionResult:
        import time, urllib.request, json as _json
        start = time.time()
        method  = action.params.get("method", "GET").upper()
        url     = action.params.get("url", "")
        headers = action.params.get("headers", {})
        body    = action.params.get("body")

        if not url:
            return ActionResult(
                success=False, error="APIExecutor: no 'url' in action.params",
                executor_used=self.name,
            )
        try:
            data = _json.dumps(body).encode() if body else None
            req  = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = resp.read().decode("utf-8", errors="replace")
            return ActionResult(
                success=True, output=payload[:4000], executor_used=self.name,
                duration_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            return ActionResult(
                success=False, error=str(e), executor_used=self.name,
                duration_ms=(time.time() - start) * 1000,
            )

    def verify(self, action: Action, result: ActionResult) -> ActionResult:
        result.verified = result.success
        result.verification_detail = "HTTP 2xx" if result.success else result.error
        return result


class DesktopExecutor(BaseExecutor):
    """Stub for future native-UI executor (VS Code, file dialogs, OCR)."""

    @property
    def name(self) -> str:
        return "desktop"

    @property
    def capabilities(self) -> list[str]:
        return ["Native UI automation", "File dialogs", "Mouse/keyboard", "OCR"]

    def can_handle(self, action: Action) -> bool:
        return "desktop" in action.preferred_executors

    def estimate_confidence(self, action: Action) -> float:
        return 0.60 if self.can_handle(action) else 0.0

    def estimate_speed_ms(self, action: Action) -> float:
        return 15_000.0

    def execute(self, action: Action) -> ActionResult:
        return ActionResult(
            success=False,
            error="DesktopExecutor not yet implemented — register a concrete executor.",
            executor_used=self.name,
        )


class CloudExecutor(BaseExecutor):
    """Stub for future cloud-infrastructure executor (AWS/Azure/GCP/K8s)."""

    @property
    def name(self) -> str:
        return "cloud"

    @property
    def capabilities(self) -> list[str]:
        return ["AWS", "Azure", "GCP", "Kubernetes", "Docker registry"]

    def can_handle(self, action: Action) -> bool:
        return "cloud" in action.preferred_executors

    def estimate_confidence(self, action: Action) -> float:
        return 0.65 if self.can_handle(action) else 0.0

    def estimate_speed_ms(self, action: Action) -> float:
        return 30_000.0

    def execute(self, action: Action) -> ActionResult:
        return ActionResult(
            success=False,
            error="CloudExecutor not yet implemented — register a concrete executor.",
            executor_used=self.name,
        )


# ── Registry ──────────────────────────────────────────────────────────────────

class ActionRegistry:
    """Plugin registry for all UAL executors.

    Built-in executors are registered automatically. Third-party executors
    call `registry.register(executor)` — no core changes needed.
    """

    def __init__(self) -> None:
        self._executors: dict[str, BaseExecutor] = {}
        self._register_builtins()

    def register(self, executor: BaseExecutor) -> None:
        logger.info("ActionRegistry: registered executor '%s'", executor.name)
        self._executors[executor.name] = executor

    def get(self, name: str) -> Optional[BaseExecutor]:
        return self._executors.get(name)

    def all(self) -> list[BaseExecutor]:
        return list(self._executors.values())

    def names(self) -> list[str]:
        return list(self._executors.keys())

    def _register_builtins(self) -> None:
        for ex in [
            TerminalExecutor(),
            BrowserExecutor(),
            APIExecutor(),
            DesktopExecutor(),
            CloudExecutor(),
        ]:
            self.register(ex)


# Singleton
_default_registry: Optional[ActionRegistry] = None


def get_registry() -> ActionRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = ActionRegistry()
    return _default_registry

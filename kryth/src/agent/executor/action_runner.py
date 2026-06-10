"""Action runner — dispatches individual tool actions to the appropriate handler.

Maps tool+action combinations from the task graph to the actual implementation
functions. Provides a unified dispatch interface that the executor uses.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ActionRunner:
    """Dispatches individual actions to their implementations.

    Maps (tool, action) tuples to callable implementations. The registry
    is built dynamically so tools can register themselves.

    Usage:
        runner = ActionRunner()
        result = runner.run("filesystem", "write_file", {"path": "a.txt", "content": "hello"})
    """

    def __init__(self) -> None:
        self._actions: dict[tuple[str, str], callable] = {}
        self._register_core_actions()

    def register_action(
        self,
        tool: str,
        action: str,
        handler: callable,
    ) -> None:
        """Register a handler for a tool+action combination."""
        self._actions[(tool, action)] = handler

    def register_tool(self, tool: str, handlers: dict[str, callable]) -> None:
        """Register all actions for a tool at once."""
        for action, handler in handlers.items():
            self._actions[(tool, action)] = handler

    def run(
        self,
        tool: str,
        action: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Dispatch a (tool, action, params) tuple to the registered handler.

        Args:
            tool: The tool category (filesystem, browser, shell, search, etc.).
            action: The specific action to perform.
            params: Parameters dict for the action.

        Returns:
            The action's result.

        Raises:
            ValueError: If no handler is registered for (tool, action).
        """
        handler = self._actions.get((tool, action))
        if handler is None:
            available = [f"{t}/{a}" for (t, a) in self._actions.keys()]
            raise ValueError(
                f"No handler for '{tool}/{action}'. "
                f"Available: {', '.join(sorted(available))}"
            )

        params = params or {}
        logger.debug("Running %s/%s with params: %s", tool, action, params)

        try:
            result = handler(**params)
            return result
        except TypeError as e:
            logger.error(
                "TypeError in %s/%s: %s (params: %s)",
                tool, action, e, params,
            )
            raise
        except Exception as e:
            logger.error(
                "Error in %s/%s: %s", tool, action, e,
            )
            raise

    def has_action(self, tool: str, action: str) -> bool:
        return (tool, action) in self._actions

    def list_actions(self) -> list[tuple[str, str]]:
        return list(self._actions.keys())

    # ------------------------------------------------------------------
    # Core action registrations
    # ------------------------------------------------------------------

    def _register_core_actions(self) -> None:
        """Register the built-in filesystem and shell actions."""
        # Filesystem actions
        self._actions[("filesystem", "read_file")] = self._read_file
        self._actions[("filesystem", "write_file")] = self._write_file
        self._actions[("filesystem", "edit_file")] = self._edit_file
        self._actions[("filesystem", "delete_file")] = self._delete_file
        self._actions[("filesystem", "list_files")] = self._list_files
        self._actions[("filesystem", "file_exists")] = self._file_exists
        self._actions[("filesystem", "make_directory")] = self._make_directory

        # Shell actions
        self._actions[("shell", "run")] = self._run_shell
        self._actions[("shell", "run_and_capture")] = self._run_shell

        # Browser actions — will be lazily populated when browser is available
        # These are registered dynamically by the executor if the browser module is loaded.
        self._register_browser_stubs()

    def _register_browser_stubs(self) -> None:
        """Register browser actions that lazy-import the browser module."""
        browser_actions = {
            "navigate": self._browser_navigate,
            "click": self._browser_click,
            "type": self._browser_type,
            "fill": self._browser_fill,
            "select": self._browser_select,
            "extract_text": self._browser_extract_text,
            "extract_html": self._browser_extract_html,
            "screenshot": self._browser_screenshot,
            "scroll": self._browser_scroll,
            "back": self._browser_back,
            "evaluate": self._browser_evaluate,
            "press_key": self._browser_press_key,
            "get_url": self._browser_get_url,
            "get_title": self._browser_get_title,
        }
        for action, handler in browser_actions.items():
            self._actions[("browser", action)] = handler

    # ------------------------------------------------------------------
    # Filesystem implementations
    # ------------------------------------------------------------------

    def _read_file(self, path: str, **kwargs) -> str:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    def _write_file(self, path: str, content: str = "", **kwargs) -> str:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"written {len(content)} chars to {path}"

    def _edit_file(self, path: str, old: str = "", new: str = "", **kwargs) -> str:
        if not os.path.exists(path):
            return f"[ERROR] file not found: {path}"
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if old not in content:
            return "[ERROR] old string not found"
        content = content.replace(old, new, 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"edited {path}"

    def _delete_file(self, path: str, **kwargs) -> str:
        if os.path.exists(path):
            os.remove(path)
            return f"deleted {path}"
        return f"[ERROR] file not found: {path}"

    def _list_files(self, path: str = ".", pattern: str = "", **kwargs) -> str:
        base = Path(path)
        if pattern:
            files = [str(p) for p in base.rglob(pattern)]
        else:
            files = [str(p) for p in base.iterdir()]
        return "\n".join(sorted(files)) if files else "(empty)"

    def _file_exists(self, path: str, **kwargs) -> bool:
        return os.path.exists(path)

    def _make_directory(self, path: str, **kwargs) -> str:
        os.makedirs(path, exist_ok=True)
        return f"created directory {path}"

    # ------------------------------------------------------------------
    # Shell implementations
    # ------------------------------------------------------------------

    def _run_shell(
        self,
        command: str,
        **kwargs,
    ) -> str:
        import subprocess
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=kwargs.get("timeout", 120),
        )
        output = result.stdout or ""
        if result.stderr:
            output += "\n" + result.stderr
        if result.returncode != 0:
            output += f"\n(exit code: {result.returncode})"
        return output or "(no output)"

    # ------------------------------------------------------------------
    # Browser implementations (lazy-import)
    # ------------------------------------------------------------------

    def _browser_navigate(self, url: str = "", **kwargs) -> str:
        from agent.providers.browser_use_provider import open_url
        return open_url(url)

    def _browser_click(self, selector: str = "", **kwargs) -> str:
        from agent.providers.browser_use_provider import click
        return click(selector)

    def _browser_type(self, selector: str = "", text: str = "", **kwargs) -> str:
        from agent.providers.browser_use_provider import type_text
        return type_text(selector, text)

    def _browser_fill(self, selector: str = "", value: str = "", **kwargs) -> str:
        from agent.providers.browser_use_provider import fill
        return fill(selector, value)

    def _browser_select(self, selector: str = "", value: str = "", **kwargs) -> str:
        from agent.providers.browser_use_provider import select
        return select(selector, value)

    def _browser_extract_text(self, selector: str = "body", **kwargs) -> str:
        from agent.providers.browser_use_provider import extract
        return extract(selector)

    def _browser_extract_html(self, **kwargs) -> str:
        from agent.providers.browser_use_provider import get_html
        return get_html()

    def _browser_screenshot(self, **kwargs) -> str:
        from agent.providers.browser_use_provider import screenshot
        return screenshot()

    def _browser_scroll(self, direction: str = "down", **kwargs) -> str:
        from agent.providers.browser_use_provider import scroll
        return scroll(direction)

    def _browser_back(self, **kwargs) -> str:
        from agent.providers.browser_use_provider import back
        return back()

    def _browser_evaluate(self, expression: str = "", **kwargs) -> str:
        from agent.providers.browser_use_provider import eval_js
        return eval_js(expression)

    def _browser_press_key(self, key: str = "", **kwargs) -> str:
        from agent.providers.browser_use_provider import keys
        return keys(key)

    def _browser_get_url(self, **kwargs) -> str:
        from agent.providers.browser_use_provider import get_url
        return get_url()

    def _browser_get_title(self, **kwargs) -> str:
        from agent.providers.browser_use_provider import get_title
        return get_title()

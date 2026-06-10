"""Self-healing browser executor with retry and alternate-selector fallback.

On failure:
    Capture error → Screenshot → Inspect DOM → Retry → Alternate selector → Continue
"""
from __future__ import annotations

import time
from typing import Any

from agent.providers.opencli.browser import BrowserProvider


# Known alternate selector lists, tried in order when the primary fails
ALTERNATE_SELECTORS: dict[str, list[str]] = {
    "submit": [
        "button[type=submit]",
        "input[type=submit]",
        "[class*=submit]",
        "[id*=submit]",
        "button:last-of-type",
    ],
    "email": [
        "input[type=email]",
        "input[name=email]",
        "input[name=Email]",
        "input[placeholder*=email i]",
        "input[placeholder*=username i]",
        "#email",
        "#username",
    ],
    "password": [
        "input[type=password]",
        "input[name=password]",
        "input[name=Password]",
        "#password",
    ],
    "file": [
        "input[type=file]",
        "input[name=resume]",
        "input[name=file]",
        "input[name=attachment]",
        "input[accept*=pdf]",
        "[class*=upload] input[type=file]",
        "#file-upload",
        "#resume-upload",
    ],
}

# Supported upload file extensions
UPLOAD_EXTENSIONS = frozenset([".pdf", ".docx", ".txt", ".png", ".jpg", ".jpeg", ".zip"])


def _is_err(result: str) -> bool:
    return result.startswith("[ERROR")


class Executor:
    """BrowserProvider wrapper with retry + alternate-selector self-healing."""

    def __init__(
        self,
        session: str = "default",
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        self.session = session
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._browser = BrowserProvider(session)

    # --- Self-healing primitives ---

    def _screenshot_on_error(self) -> str:
        try:
            return self._browser.screenshot()
        except Exception as e:
            return f"[screenshot failed: {e}]"

    def _inspect_dom(self, selector: str) -> str:
        try:
            return self._browser.find(selector)
        except Exception as e:
            return f"[dom inspect failed: {e}]"

    def _try_alternates(self, key: str, action_fn, *args) -> str:
        for alt in ALTERNATE_SELECTORS.get(key, []):
            result = action_fn(alt, *args)
            if not _is_err(result):
                return result
        return f"[ERROR] all alternate selectors for '{key}' failed"

    # --- Retrying browser actions ---

    def retry_click(self, selector: str) -> str:
        for attempt in range(self.max_retries):
            result = self._browser.click(selector)
            if not _is_err(result):
                return result
            if attempt < self.max_retries - 1:
                # Try submit alternates before sleeping
                alt_result = self._try_alternates("submit", self._browser.click)
                if not _is_err(alt_result):
                    return alt_result
                self._screenshot_on_error()
                time.sleep(self.retry_delay)
        return f"[ERROR] click '{selector}' failed after {self.max_retries} attempts"

    def retry_fill(self, selector: str, value: str) -> str:
        for attempt in range(self.max_retries):
            result = self._browser.fill(selector, value)
            if not _is_err(result):
                return result
            if attempt < self.max_retries - 1:
                # Infer field type from selector and try matching alternates
                for key, alts in ALTERNATE_SELECTORS.items():
                    if selector in alts or any(k in selector for k in (key, key.upper())):
                        alt_result = self._try_alternates(key, self._browser.fill, value)
                        if not _is_err(alt_result):
                            return alt_result
                self._screenshot_on_error()
                time.sleep(self.retry_delay)
        return f"[ERROR] fill '{selector}' failed after {self.max_retries} attempts"

    def retry_upload(self, selector: str, path: str) -> str:
        # opencli maps fill → setInputFiles for file inputs
        for attempt in range(self.max_retries):
            result = self._browser.fill(selector, path)
            if not _is_err(result):
                return result
            if attempt < self.max_retries - 1:
                alt_result = self._try_alternates("file", self._browser.fill, path)
                if not _is_err(alt_result):
                    return alt_result
                self._screenshot_on_error()
                time.sleep(self.retry_delay)
        return f"[ERROR] upload to '{selector}' failed after {self.max_retries} attempts"

    def retry_navigation(self, url: str) -> str:
        for attempt in range(self.max_retries):
            result = self._browser.open(url)
            if _is_err(result):
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))
                continue
            # Verify the URL actually changed (page rendered)
            try:
                time.sleep(1.5)
                current = self._browser.get_url()
                if "about:blank" in (current or ""):
                    if attempt < self.max_retries - 1:
                        time.sleep(self.retry_delay)
                    continue
            except Exception:
                pass
            return result
        return f"[ERROR] navigation to {url} failed after {self.max_retries} attempts"

    # --- Form automation ---

    def detect_form(self) -> str:
        result = self._browser.find("form")
        if _is_err(result) or result == "[]":
            result = self._browser.find("div[class*=form],section[class*=form],[role=form]")
        return result

    def extract_fields(self) -> str:
        return self._browser.find(
            "input:not([type=hidden]),select,textarea,button[type=submit]"
        )

    def fill_form(self, data: dict[str, str]) -> str:
        if not data:
            return "no fields to fill"
        lines = []
        for selector, value in data.items():
            r = self.retry_fill(selector, value)
            lines.append(f"  {selector}: {r}")
        return "\n".join(lines)

    def submit(self) -> str:
        return self.retry_click("button[type=submit]")

    # --- File uploads ---

    def upload_file(self, path: str, selector: str | None = None) -> str:
        sel = selector or "input[type=file]"
        return self.retry_upload(sel, path)

    def upload_resume(self, path: str) -> str:
        for sel in ALTERNATE_SELECTORS["file"]:
            r = self._browser.fill(sel, path)
            if not _is_err(r):
                return r
        return self.retry_upload("input[type=file]", path)

    def upload_document(self, path: str) -> str:
        return self.upload_file(path)

    def upload_image(self, path: str) -> str:
        sel = (
            "input[type=file][accept*=image],"
            "input[type=file][accept*=png],"
            "input[type=file][accept*=jpg],"
            "input[type=file][accept*=jpeg]"
        )
        return self.retry_upload(sel, path)

    # --- High-level action dispatch ---

    def execute_action(self, action: str, params: dict[str, Any]) -> str:
        _dispatch: dict[str, Any] = {
            "open":           lambda p: self.retry_navigation(p["url"]),
            "click":          lambda p: self.retry_click(p["selector"]),
            "fill":           lambda p: self.retry_fill(p["selector"], p["value"]),
            "fill_form":      lambda p: self.fill_form(p.get("data") or {}),
            "type":           lambda p: self._browser.type(p["selector"], p["text"]),
            "select":         lambda p: self._browser.select(p["selector"], p["value"]),
            "extract":        lambda p: self._browser.extract(p.get("selector", "body")),
            "get":            lambda p: self._browser.get(p["selector"]),
            "find":           lambda p: self._browser.find(p["selector"]),
            "screenshot":     lambda p: self._browser.screenshot(),
            "download":       lambda p: self._browser.download(p["url"], p.get("output", ".")),
            "close":          lambda p: self._browser.close(),
            "detect_form":    lambda p: self.detect_form(),
            "extract_fields": lambda p: self.extract_fields(),
            "submit":         lambda p: self.submit(),
            "upload_file":    lambda p: self.upload_file(p["path"], p.get("selector")),
            "upload_resume":  lambda p: self.upload_resume(p["path"]),
            "upload_document":lambda p: self.upload_document(p["path"]),
            "upload_image":   lambda p: self.upload_image(p["path"]),
            "eval":           lambda p: self._browser.eval_js(p["expression"]),
            "scroll":         lambda p: self._browser.scroll(p.get("direction", "down")),
            "keys":           lambda p: self._browser.keys(p["combo"]),
            "tab_list":       lambda p: self._browser.tab_list(),
            "tab_new":        lambda p: self._browser.tab_new(p.get("url", "")),
            "tab_select":     lambda p: self._browser.tab_select(p["target_id"]),
            "state":          lambda p: self._browser.state(),
        }
        handler = _dispatch.get(action)
        if handler is None:
            return f"[ERROR] unknown action: '{action}'"
        try:
            return handler(params)
        except KeyError as e:
            return f"[ERROR] missing required param {e} for action '{action}'"
        except Exception as e:
            return f"[ERROR] {action} raised: {e}"

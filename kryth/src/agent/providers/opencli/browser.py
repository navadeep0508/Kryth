"""OpenCLI browser provider — wraps `opencli browser <session> ...` commands.

Real v1.8.x CLI surface (all commands output JSON natively, no -f flag needed):

    open <url>                          → {url, page}
    click <target>                      → {clicked, target, matches_n}
    fill <target> <text>                → fills an input
    type <target> <text>                → types into an element
    select <target> <value>             → selects an option
    keys <combo>                        → keyboard shortcut
    scroll [up|down|left|right]         → scroll page
    back                                → navigate back
    extract                             → {url, title, content, ...} (full page)
    get text [target]                   → {value, matches_n}
    get title                           → page title
    get url                             → current URL
    get html                            → page HTML
    get value [target]                  → input value
    get attributes [target]             → element attributes
    find --css <selector>               → {entries, matches_n}
    screenshot                          → PNG bytes (base64 in stdout)
    state                               → {url, title, viewport, ...}
    close                               → close session/tab
    tab list / tab new [url] / tab select <id>

Exit codes:
    0   success
    66  empty result
    69  browser bridge down
    75  timeout
    77  auth required
    78  config error
"""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


_EC_EMPTY = 66
_EC_BROWSER_DOWN = 69
_EC_TIMEOUT = 75
_EC_AUTH = 77


def _cli() -> str:
    cli = shutil.which("opencli") or shutil.which("opencli.cmd")
    if not cli:
        raise RuntimeError("opencli not found — run: npm install -g @jackwener/opencli")
    return cli


def _run(args: list[str], timeout: int = 60) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            args, capture_output=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return _EC_TIMEOUT, "", "command timed out"
    except FileNotFoundError as e:
        return 1, "", str(e)
    except Exception as e:
        return 1, "", str(e)


def _browser(session: str, *args: str, timeout: int = 60) -> tuple[int, str, str]:
    """All browser subcommands; JSON output is native, no -f flag needed."""
    return _run([_cli(), "browser", session, *args], timeout=timeout)


# ---------------------------------------------------------------------------
# Session auto-discovery
# ---------------------------------------------------------------------------

def _discover_session() -> str | None:
    """Return the first connected Chrome profile ID from opencli profile list.

    Output format:
        Connected Browser Bridge profiles

          rt3hpxz8 — connected v1.0.17
    """
    cli = shutil.which("opencli") or shutil.which("opencli.cmd")
    if not cli:
        return None
    rc, out, err = _run([cli, "profile", "list"], timeout=10)
    text = out or err
    for line in text.splitlines():
        if "—" in line or " -- " in line:  # lines with em-dash contain profile IDs
            token = line.split()[0].strip()
            if token and token.lower() not in (
                "connected", "disconnected", "profiles", "extension", "download",
            ):
                return token
    return None


# ---------------------------------------------------------------------------
# BrowserProvider
# ---------------------------------------------------------------------------

class BrowserProvider:
    """Python wrapper around `opencli browser <session>` commands."""

    def __init__(self, session: str = "default"):
        self._session = session
        self._resolved: str | None = None

    @property
    def session(self) -> str:
        if self._resolved is not None:
            return self._resolved
        # Probe the requested name first
        rc, out, _ = _run([_cli(), "browser", self._session, "state"], timeout=5)
        if rc == 0 and out and "error" not in out.lower():
            self._resolved = self._session
            return self._resolved
        # Auto-discovery: find a connected profile
        discovered = _discover_session()
        if discovered:
            # Verify the discovered session actually works
            rc2, out2, _ = _run([_cli(), "browser", discovered, "state"], timeout=5)
            if rc2 == 0 and out2 and "error" not in out2.lower():
                self._resolved = discovered
                return self._resolved
        # Fallback: create a fresh session via tab_new
        rc3, out3, _ = _run([_cli(), "browser", self._session, "tab", "new"], timeout=10)
        if rc3 == 0:
            self._resolved = self._session
        else:
            self._resolved = self._session  # let it fail with a clear error
        return self._resolved

    # --- Navigation ---

    def open(self, url: str) -> str:
        rc, out, err = _browser(self.session, "open", url)
        if rc != 0:
            return self._err(rc, out, err)
        return out or f"opened {url}"

    def close(self) -> str:
        rc, out, err = _browser(self.session, "close")
        if rc not in (0, _EC_BROWSER_DOWN):
            return self._err(rc, out, err)
        return out or "closed"

    def state(self) -> str:
        rc, out, err = _browser(self.session, "state")
        if rc != 0:
            return self._err(rc, out, err)
        return out

    def back(self) -> str:
        rc, out, err = _browser(self.session, "back")
        if rc != 0:
            return self._err(rc, out, err)
        return out or "navigated back"

    # --- Interaction ---

    def click(self, selector: str) -> str:
        rc, out, err = _browser(self.session, "click", selector)
        if rc != 0:
            return self._err(rc, out, err)
        return out or "clicked"

    def type(self, selector: str, text: str) -> str:
        rc, out, err = _browser(self.session, "type", selector, text)
        if rc != 0:
            return self._err(rc, out, err)
        return out or "typed"

    def fill(self, selector: str, value: str) -> str:
        rc, out, err = _browser(self.session, "fill", selector, value)
        if rc != 0:
            return self._err(rc, out, err)
        return out or "filled"

    def select(self, selector: str, value: str) -> str:
        rc, out, err = _browser(self.session, "select", selector, value)
        if rc != 0:
            return self._err(rc, out, err)
        return out or "selected"

    def keys(self, combo: str) -> str:
        rc, out, err = _browser(self.session, "keys", combo)
        if rc != 0:
            return self._err(rc, out, err)
        return out or "keys sent"

    def scroll(self, direction: str = "down") -> str:
        rc, out, err = _browser(self.session, "scroll", direction)
        if rc != 0:
            return self._err(rc, out, err)
        return out or "scrolled"

    def eval_js(self, expression: str) -> str:
        rc, out, err = _browser(self.session, "eval", expression)
        if rc != 0:
            return self._err(rc, out, err)
        return out

    # --- Extraction ---

    def extract(self, selector: str = "") -> str:
        """Extract page content. Uses get text <selector> for targeted extraction,
        or bare extract for full page markdown."""
        if selector and selector not in ("body", "*", ""):
            rc, out, err = _browser(self.session, "get", "text", selector)
        else:
            rc, out, err = _browser(self.session, "extract")
        if rc == _EC_EMPTY:
            return "[]"
        if rc != 0:
            return self._err(rc, out, err)
        return out

    def get(self, selector: str) -> str:
        """Get text content of a CSS-selected element."""
        rc, out, err = _browser(self.session, "get", "text", selector)
        if rc == _EC_EMPTY:
            return "null"
        if rc != 0:
            return self._err(rc, out, err)
        return out

    def get_title(self) -> str:
        rc, out, err = _browser(self.session, "get", "title")
        if rc != 0:
            return self._err(rc, out, err)
        return out

    def get_url(self) -> str:
        rc, out, err = _browser(self.session, "get", "url")
        if rc != 0:
            return self._err(rc, out, err)
        return out

    def get_html(self) -> str:
        rc, out, err = _browser(self.session, "get", "html")
        if rc != 0:
            return self._err(rc, out, err)
        return out

    def get_value(self, selector: str) -> str:
        rc, out, err = _browser(self.session, "get", "value", selector)
        if rc == _EC_EMPTY:
            return "null"
        if rc != 0:
            return self._err(rc, out, err)
        return out

    def find(self, selector: str) -> str:
        """Find elements by CSS selector using --css flag."""
        rc, out, err = _browser(self.session, "find", "--css", selector)
        if rc == _EC_EMPTY:
            return "[]"
        if rc != 0:
            return self._err(rc, out, err)
        return out

    # --- Screenshot ---

    def screenshot(self) -> str:
        """Capture screenshot. opencli outputs raw PNG bytes; save to temp file."""
        tmp = Path(tempfile.mkdtemp()) / "screenshot.png"
        try:
            r = subprocess.run(
                [_cli(), "browser", self.session, "screenshot"],
                capture_output=True,
                timeout=30,
            )
            if r.returncode != 0:
                return self._err(r.returncode, r.stdout.decode(errors="replace"), r.stderr.decode(errors="replace"))
            tmp.write_bytes(r.stdout)
            return str(tmp)
        except Exception as e:
            return f"[ERROR] screenshot failed: {e}"

    # --- Download ---

    def download(self, url: str, output_dir: str = ".") -> str:
        rc, out, err = _browser(self.session, "open", url, timeout=60)
        if rc != 0:
            return self._err(rc, out, err)
        return out or f"opened {url}"

    # --- Tab management ---

    def tab_list(self) -> str:
        rc, out, err = _browser(self.session, "tab", "list")
        if rc != 0:
            return self._err(rc, out, err)
        return out

    def tab_new(self, url: str = "") -> str:
        args = ["tab", "new"]
        if url:
            args.append(url)
        rc, out, err = _browser(self.session, *args)
        if rc != 0:
            return self._err(rc, out, err)
        return out

    def tab_select(self, target_id: str) -> str:
        rc, out, err = _browser(self.session, "tab", "select", target_id)
        if rc != 0:
            return self._err(rc, out, err)
        return out or "tab selected"

    # --- Error helper ---

    def _err(self, rc: int, out: str, err: str) -> str:
        msg = err or out or "unknown error"
        hints = {
            _EC_BROWSER_DOWN: "Browser Bridge not connected — is Chrome open with the extension?",
            _EC_TIMEOUT: "Timed out — set OPENCLI_BROWSER_COMMAND_TIMEOUT to increase limit.",
            _EC_AUTH: "Authentication required — log in to the site first.",
        }
        hint = hints.get(rc, "")
        suffix = f" ({hint})" if hint else ""
        return f"[ERROR {rc}] {msg}{suffix}"

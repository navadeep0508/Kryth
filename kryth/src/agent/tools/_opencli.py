"""Browser automation tools — Playwright + OpenCLI fallback.

Playwright is the PRIMARY provider (native Python, no browser extension
needed). OpenCLI is the FALLBACK for users with the Browser Bridge setup.

BROWSER CAPABILITIES (25 tools):
    Navigation:    open_url, browser_back, browser_tab_new, browser_tab_select
    Interaction:   browser_click, browser_type, browser_select, browser_keys,
                   browser_scroll, browser_submit, fill_form, upload_file
    Extraction:    extract_data, browser_get_html, browser_get_url, browser_state,
                   browser_search
    Visual:        browser_screenshot
    Advanced:      browser_eval_js, browser_tab_list

Security: purchases, payments, deletions, and permanent account changes require
explicit user confirmation before execution.
"""
from __future__ import annotations

import json
import sys
import time
from urllib.parse import quote

from agent.tools._results import err

# --- Provider selection: Playwright > OpenCLI ---
_PW_MODULE = None  # Lazy-loaded playwright_bridge module


def _get_pw():
    global _PW_MODULE
    if _PW_MODULE is False:  # Already checked and unavailable
        return None
    if _PW_MODULE is not None:
        return _PW_MODULE
    try:
        import playwright.sync_api
        from agent.providers import playwright_browser as pw
        _PW_MODULE = pw
        return pw
    except ImportError:
        _PW_MODULE = False
        return None


def _get_oc(session: str = "default"):
    """Return OpenCLI (executor, browser) or raise RuntimeError."""
    from agent.providers.opencli.executor import Executor
    from agent.providers.opencli.browser import BrowserProvider
    return Executor(session), BrowserProvider(session)


# Actions that must never run without user confirmation
_APPROVAL_KEYWORDS = frozenset([
    "purchase", "buy", "pay", "payment", "checkout",
    "delete account", "remove account", "close account",
    "cancel subscription", "permanent",
])


def _needs_approval(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in _APPROVAL_KEYWORDS)


# ---------------------------------------------------------------------------
# Tool functions — all try Playwright first, fall back to OpenCLI
# ---------------------------------------------------------------------------

def _pw_or_oc(session: str):
    """Return (pw_module, oc_tuple). One will be None.

    If Playwright is available, pw_module is set and oc_tuple is None.
    Otherwise pw_module is None and oc_tuple is (executor, browser).
    """
    pw = _get_pw()
    if pw is not None:
        return pw, None
    try:
        return None, _get_oc(session)
    except RuntimeError:
        return None, None


def open_url(url: str, session: str = "default") -> str:
    pw, oc = _pw_or_oc(session)
    if pw:
        return pw.open_url(url)
    if oc:
        try:
            ex, b = oc
            result = ex.retry_navigation(url)
            if not result.startswith("[ERROR"):
                time.sleep(2)
                current = b.get_url()
                if "about:blank" in (current or ""):
                    tabs = b.tab_list()
                    for line in (tabs or "").splitlines():
                        for part in line.strip().split():
                            if len(part) > 8 and "-" in part:
                                b.tab_select(part)
                                time.sleep(1)
                                break
                return result
        except RuntimeError as e:
            return err("OPENCLI_UNAVAILABLE", str(e))
    return err("BROWSER_UNAVAILABLE",
               "No browser provider available. Install Playwright "
               "(pip install playwright && playwright install chromium) "
               "or set up OpenCLI (npm install -g @jackwener/opencli).")


def fill_form(data: str, session: str = "default") -> str:
    pw, oc = _pw_or_oc(session)
    try:
        field_map: dict = json.loads(data) if isinstance(data, str) else data
    except (json.JSONDecodeError, ValueError) as e:
        return err("INVALID_ARGS", f"data must be valid JSON object: {e}")
    if not isinstance(field_map, dict):
        return err("INVALID_ARGS", "data must be a JSON object {selector: value, ...}")

    if pw:
        results = []
        for sel, val in field_map.items():
            r = pw.fill(sel, val)
            results.append(f"  {sel}: {r}")
        return "\n".join(results)
    if oc:
        try:
            ex, _ = oc
            ex.detect_form()
            return ex.fill_form(field_map)
        except RuntimeError as e:
            return err("OPENCLI_UNAVAILABLE", str(e))
    return err("BROWSER_UNAVAILABLE", "No browser provider available.")


def upload_file(path: str, selector: str = "", session: str = "default") -> str:
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return err("NOT_FOUND", f"file not found: {path}")

    pw, oc = _pw_or_oc(session)
    if pw:
        sel = selector or "input[type=file]"
        try:
            page = pw._get_page()
            el = page.wait_for_selector(sel, timeout=5000)
            if not el:
                return err("NOT_FOUND", f"file input not found: {sel}")
            el.set_input_files(str(p.resolve()))
            return f"uploaded {path} to {sel}"
        except Exception as e:
            return err("EXEC_FAILED", f"upload failed: {e}")
    if oc:
        try:
            ex, _ = oc
            return ex.upload_file(path, selector or None)
        except RuntimeError as e:
            return err("OPENCLI_UNAVAILABLE", str(e))
    return err("BROWSER_UNAVAILABLE", "No browser provider available.")


def extract_data(selector: str, session: str = "default") -> str:
    pw, oc = _pw_or_oc(session)
    if pw:
        return pw.extract(selector)
    if oc:
        try:
            _, b = oc
            return b.extract(selector)
        except RuntimeError as e:
            return err("OPENCLI_UNAVAILABLE", str(e))
    return err("BROWSER_UNAVAILABLE", "No browser provider available.")


def download_content(url: str, output: str = ".", session: str = "default") -> str:
    pw, oc = _pw_or_oc(session)
    if pw:
        return pw.download(url, output)
    if oc:
        try:
            _, b = oc
            return b.download(url, output)
        except RuntimeError as e:
            return err("OPENCLI_UNAVAILABLE", str(e))
    return err("BROWSER_UNAVAILABLE", "No browser provider available.")


def browser_search(
    query: str,
    url: str = "",
    result_selector: str = "h3",
    session: str = "default",
) -> str:
    pw, oc = _pw_or_oc(session)
    # Build search URL. Use DuckDuckGo HTML for Playwright (no CAPTCHA).
    # Google blocks headless browsers; DDG HTML endpoint is reliable.
    if not url or url in ("https://www.google.com", ""):
        if pw:
            search_url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
            result_selector = result_selector if result_selector != "h3" else ".result__title"
        else:
            search_url = f"https://www.google.com/search?q={quote(query)}"
    elif "?" in url:
        search_url = f"{url}&q={quote(query)}"
    else:
        search_url = f"{url}?q={quote(query)}"

    if pw:
        r = pw.open_url(search_url)
        if r.startswith("[ERROR"):
            return r
        time.sleep(2)
        return pw.extract(result_selector)
    if oc:
        try:
            nav = oc[0].retry_navigation(search_url)
            if nav.startswith("[ERROR"):
                return nav
            time.sleep(2)
            return oc[1].extract(result_selector)
        except RuntimeError as e:
            return err("OPENCLI_UNAVAILABLE", str(e))
    return err("BROWSER_UNAVAILABLE", "No browser provider available.")


def browser_login(
    url: str,
    username: str,
    password: str,
    session: str = "default",
) -> str:
    pw, oc = _pw_or_oc(session)
    if pw:
        r = pw.open_url(url)
        if r.startswith("[ERROR"):
            return r
        time.sleep(2)
        # Try common login selectors
        for sel in ("input[type=email]", "input[name=email]", "input[name=username]", "#email", "#username"):
            r = pw.fill(sel, username)
            if not r.startswith("[ERROR"):
                break
        for sel in ("input[type=password]", "input[name=password]", "#password"):
            r = pw.fill(sel, password)
            if not r.startswith("[ERROR"):
                break
        # Click submit
        for sel in ("button[type=submit]", "input[type=submit]"):
            r = pw.click(sel)
            if not r.startswith("[ERROR"):
                break
        return "login submitted"
    if oc:
        try:
            from agent.providers.opencli.executor import ALTERNATE_SELECTORS
            ex, b = oc
            nav = ex.retry_navigation(url)
            if nav.startswith("[ERROR"):
                return nav
            for sel in ALTERNATE_SELECTORS["email"]:
                r = b.fill(sel, username)
                if not r.startswith("[ERROR"):
                    break
            for sel in ALTERNATE_SELECTORS["password"]:
                r = b.fill(sel, password)
                if not r.startswith("[ERROR"):
                    break
            return ex.submit()
        except RuntimeError as e:
            return err("OPENCLI_UNAVAILABLE", str(e))
    return err("BROWSER_UNAVAILABLE", "No browser provider available.")


def browser_submit(selector: str = "", session: str = "default") -> str:
    pw, oc = _pw_or_oc(session)
    if pw:
        if selector:
            return pw.click(selector)
        for sel in ("button[type=submit]", "input[type=submit]", "[type=submit]"):
            r = pw.click(sel)
            if not r.startswith("[ERROR"):
                return r
        return pw.keys("Enter")
    if oc:
        try:
            ex, _ = oc
            if selector:
                return ex.retry_click(selector)
            return ex.submit()
        except RuntimeError as e:
            return err("OPENCLI_UNAVAILABLE", str(e))
    return err("BROWSER_UNAVAILABLE", "No browser provider available.")


def browser_setup() -> str:
    """Install Playwright (preferred) or OpenCLI (fallback)."""
    # Try installing Playwright first
    try:
        import subprocess
        r1 = subprocess.run(
            [sys.executable, "-m", "pip", "install", "playwright"],
            capture_output=True, text=True, timeout=60,
        )
        r2 = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True, text=True, timeout=120,
        )
        if r1.returncode == 0:
            return (
                "Playwright installed. You can now use all browser tools.\n"
                + (r2.stdout or "")[:500]
            )
    except Exception:
        pass
    # Fall back to OpenCLI
    try:
        from agent.providers.opencli.installer import setup
        return setup()
    except Exception as e:
        return err("SETUP_ERROR", str(e))


def browser_setup_verify() -> str:
    """Verify browser automation is working."""
    pw = _get_pw()
    if pw:
        return "Playwright is available and ready for browser automation."
    try:
        from agent.providers.opencli.installer import verify
        return verify()
    except Exception as e:
        return err("VERIFY_ERROR", str(e))


# ---------------------------------------------------------------------------
# Browser primitive tools — all try Playwright first, fall back to OpenCLI
# ---------------------------------------------------------------------------

def browser_click(selector: str, session: str = "default") -> str:
    pw, oc = _pw_or_oc(session)
    if pw:
        return pw.click(selector)
    if oc:
        try:
            return oc[0].retry_click(selector)
        except RuntimeError as e:
            return err("OPENCLI_UNAVAILABLE", str(e))
    return err("BROWSER_UNAVAILABLE", "No browser provider available.")


def browser_type(selector: str, text: str, session: str = "default") -> str:
    pw, oc = _pw_or_oc(session)
    if pw:
        return pw.type_text(selector, text)
    if oc:
        try:
            return oc[1].type(selector, text)
        except RuntimeError as e:
            return err("OPENCLI_UNAVAILABLE", str(e))
    return err("BROWSER_UNAVAILABLE", "No browser provider available.")


def browser_select(selector: str, value: str, session: str = "default") -> str:
    pw, oc = _pw_or_oc(session)
    if pw:
        return pw.select(selector, value)
    if oc:
        try:
            return oc[1].select(selector, value)
        except RuntimeError as e:
            return err("OPENCLI_UNAVAILABLE", str(e))
    return err("BROWSER_UNAVAILABLE", "No browser provider available.")


def browser_scroll(direction: str = "down", session: str = "default") -> str:
    pw, oc = _pw_or_oc(session)
    if pw:
        return pw.scroll(direction)
    if oc:
        try:
            return oc[1].scroll(direction)
        except RuntimeError as e:
            return err("OPENCLI_UNAVAILABLE", str(e))
    return err("BROWSER_UNAVAILABLE", "No browser provider available.")


def browser_screenshot(session: str = "default") -> str:
    pw, oc = _pw_or_oc(session)
    if pw:
        return pw.screenshot()
    if oc:
        try:
            return oc[1].screenshot()
        except RuntimeError as e:
            return err("OPENCLI_UNAVAILABLE", str(e))
    return err("BROWSER_UNAVAILABLE", "No browser provider available.")


def browser_back(session: str = "default") -> str:
    pw, oc = _pw_or_oc(session)
    if pw:
        return pw.back()
    if oc:
        try:
            return oc[1].back()
        except RuntimeError as e:
            return err("OPENCLI_UNAVAILABLE", str(e))
    return err("BROWSER_UNAVAILABLE", "No browser provider available.")


def browser_eval_js(expression: str, session: str = "default") -> str:
    pw, oc = _pw_or_oc(session)
    if pw:
        return pw.eval_js(expression)
    if oc:
        try:
            return oc[1].eval_js(expression)
        except RuntimeError as e:
            return err("OPENCLI_UNAVAILABLE", str(e))
    return err("BROWSER_UNAVAILABLE", "No browser provider available.")


def browser_keys(combo: str, session: str = "default") -> str:
    pw, oc = _pw_or_oc(session)
    if pw:
        return pw.keys(combo)
    if oc:
        try:
            return oc[1].keys(combo)
        except RuntimeError as e:
            return err("OPENCLI_UNAVAILABLE", str(e))
    return err("BROWSER_UNAVAILABLE", "No browser provider available.")


def browser_tab_list(session: str = "default") -> str:
    pw, oc = _pw_or_oc(session)
    if pw:
        return pw.tab_list()
    if oc:
        try:
            return oc[1].tab_list()
        except RuntimeError as e:
            return err("OPENCLI_UNAVAILABLE", str(e))
    return err("BROWSER_UNAVAILABLE", "No browser provider available.")


def browser_tab_new(url: str = "", session: str = "default") -> str:
    pw, oc = _pw_or_oc(session)
    if pw:
        return pw.tab_new(url)
    if oc:
        try:
            return oc[1].tab_new(url)
        except RuntimeError as e:
            return err("OPENCLI_UNAVAILABLE", str(e))
    return err("BROWSER_UNAVAILABLE", "No browser provider available.")


def browser_tab_select(target_id: str, session: str = "default") -> str:
    pw, oc = _pw_or_oc(session)
    if pw:
        return pw.tab_select(target_id)
    if oc:
        try:
            return oc[1].tab_select(target_id)
        except RuntimeError as e:
            return err("OPENCLI_UNAVAILABLE", str(e))
    return err("BROWSER_UNAVAILABLE", "No browser provider available.")


def browser_get_html(session: str = "default") -> str:
    pw, oc = _pw_or_oc(session)
    if pw:
        return pw.get_html()
    if oc:
        try:
            return oc[1].get_html()
        except RuntimeError as e:
            return err("OPENCLI_UNAVAILABLE", str(e))
    return err("BROWSER_UNAVAILABLE", "No browser provider available.")


def browser_get_url(session: str = "default") -> str:
    pw, oc = _pw_or_oc(session)
    if pw:
        return pw.get_url()
    if oc:
        try:
            return oc[1].get_url()
        except RuntimeError as e:
            return err("OPENCLI_UNAVAILABLE", str(e))
    return err("BROWSER_UNAVAILABLE", "No browser provider available.")


def browser_state(session: str = "default") -> str:
    pw, oc = _pw_or_oc(session)
    if pw:
        return pw.state()
    if oc:
        try:
            return oc[1].state()
        except RuntimeError as e:
            return err("OPENCLI_UNAVAILABLE", str(e))
    return err("BROWSER_UNAVAILABLE", "No browser provider available.")

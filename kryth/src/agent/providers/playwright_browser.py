"""Playwright browser provider — thread-safe, no asyncio conflicts.

All Playwright operations run in a dedicated background thread (kryth-browser)
isolated from prompt_toolkit's asyncio event loop. Calls are dispatched via
a thread-safe queue so the main thread never touches Playwright directly.

Install: pip install playwright && playwright install chromium
"""

from __future__ import annotations
import json, os, tempfile, time
from pathlib import Path
from agent.browser._human import is_human_like


def _err(msg: str) -> str:
    return f"[ERROR] {msg}"


def _call(fn):
    """Dispatch fn(worker) to the browser thread and return the result."""
    from agent.browser.browser_manager import _browser_call
    return _browser_call(fn)


def ensure_available() -> str | None:
    try:
        import playwright.sync_api  # noqa: F401
        return None
    except ImportError:
        return ("Playwright not installed. Run: "
                "pip install playwright && playwright install chromium")


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

def open_url(url: str) -> str:
    e = ensure_available()
    if e: return e
    try:
        def _fn(w):
            p = w._ensure_page()
            p.goto(url, wait_until="domcontentloaded", timeout=30000)
            try: p.wait_for_load_state("networkidle", timeout=5000)
            except Exception: pass
            return f"navigated to {url}"
        return _call(_fn)
    except Exception as e: return _err(f"open failed: {e}")


def get_url() -> str:
    e = ensure_available()
    if e: return e
    try: return _call(lambda w: w._ensure_page().url)
    except Exception as e: return _err(f"get_url failed: {e}")


def get_title() -> str:
    e = ensure_available()
    if e: return e
    try: return _call(lambda w: w._ensure_page().title())
    except Exception as e: return _err(f"get_title failed: {e}")


def get_html() -> str:
    e = ensure_available()
    if e: return e
    try: return _call(lambda w: w._ensure_page().content())
    except Exception as e: return _err(f"get_html failed: {e}")


def state() -> str:
    e = ensure_available()
    if e: return e
    try:
        def _fn(w):
            p = w._ensure_page()
            return f"URL: {p.url}\nTitle: {p.title()}\nViewport: {p.viewport_size}\n"
        return _call(_fn)
    except Exception as e: return _err(f"state failed: {e}")


def back() -> str:
    e = ensure_available()
    if e: return e
    try:
        def _fn(w): w._ensure_page().go_back(); return "navigated back"
        return _call(_fn)
    except Exception as e: return _err(f"back failed: {e}")


# ---------------------------------------------------------------------------
# Interaction
# ---------------------------------------------------------------------------

def click(selector: str) -> str:
    e = ensure_available()
    if e: return e
    try:
        def _fn(w):
            p = w._ensure_page()
            # Try the given selector, then common fallbacks for submit buttons
            candidates = [selector]
            if selector in ("button[type=submit]", "submit", "button"):
                candidates += ["input[type=submit]", "button:last-of-type"]
            for sel in candidates:
                try:
                    el = p.wait_for_selector(sel, timeout=3000)
                    if el:
                        if is_human_like(): el.hover(); time.sleep(0.1)
                        el.click()
                        return f"clicked {sel}"
                except Exception: continue
            return _err(f"selector '{selector}' matched 0 elements")
        return _call(_fn)
    except Exception as e: return _err(f"click failed: {e}")


def type_text(selector: str, text: str) -> str:
    e = ensure_available()
    if e: return e
    try:
        def _fn(w):
            p = w._ensure_page()
            el = p.wait_for_selector(selector, timeout=5000)
            if not el: return _err(f"'{selector}' not found")
            if is_human_like(): el.click(); time.sleep(0.1); el.type(text, delay=50)
            else: el.type(text)
            return f"typed into {selector}"
        return _call(_fn)
    except Exception as e: return _err(f"type failed: {e}")


def fill(selector: str, value: str) -> str:
    e = ensure_available()
    if e: return e
    try:
        def _fn(w):
            p = w._ensure_page()
            el = p.wait_for_selector(selector, timeout=5000)
            if not el: return _err(f"'{selector}' not found")
            el.fill(value)
            return f"filled {selector}"
        return _call(_fn)
    except Exception as e: return _err(f"fill failed: {e}")


def select(selector: str, value: str) -> str:
    e = ensure_available()
    if e: return e
    try:
        def _fn(w):
            p = w._ensure_page()
            el = p.wait_for_selector(selector, timeout=5000)
            if not el: return _err(f"'{selector}' not found")
            el.select_option(value)
            return f"selected {value} in {selector}"
        return _call(_fn)
    except Exception as e: return _err(f"select failed: {e}")


def keys(combo: str) -> str:
    e = ensure_available()
    if e: return e
    try:
        def _fn(w): w._ensure_page().keyboard.press(combo); return f"sent {combo}"
        return _call(_fn)
    except Exception as e: return _err(f"keys failed: {e}")


def scroll(direction: str = "down") -> str:
    e = ensure_available()
    if e: return e
    try:
        def _fn(w):
            dx, dy = {"down":(0,800),"up":(0,-800),"right":(800,0),"left":(-800,0)}.get(direction,(0,800))
            w._ensure_page().evaluate(f"window.scrollBy({dx},{dy})")
            return f"scrolled {direction}"
        return _call(_fn)
    except Exception as e: return _err(f"scroll failed: {e}")


def eval_js(expression: str) -> str:
    e = ensure_available()
    if e: return e
    try:
        def _fn(w): return str(w._ensure_page().evaluate(expression))
        return _call(_fn)
    except Exception as e: return _err(f"eval_js failed: {e}")


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract(selector: str = "") -> str:
    e = ensure_available()
    if e: return e
    try:
        def _fn(w):
            p = w._ensure_page()
            if not selector or selector in ("body", "*"):
                body = p.query_selector("body")
                return (body.inner_text() if body else p.content())[:10000]
            els = p.query_selector_all(selector)
            if not els: return "[]"
            return json.dumps([el.inner_text().strip() for el in els if el], ensure_ascii=False)
        return _call(_fn)
    except Exception as e: return _err(f"extract failed: {e}")


def find(selector: str) -> str:
    e = ensure_available()
    if e: return e
    try:
        def _fn(w):
            p = w._ensure_page()
            els = p.query_selector_all(selector)
            if not els: return "[]"
            results = []
            for el in els[:20]:
                try:
                    tag = el.evaluate("el=>el.tagName.toLowerCase()")
                    results.append({"tag": tag, "text": el.inner_text().strip()[:80]})
                except Exception: continue
            return json.dumps(results, ensure_ascii=False)
        return _call(_fn)
    except Exception as e: return _err(f"find failed: {e}")


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------

def screenshot() -> str:
    e = ensure_available()
    if e: return e
    try:
        def _fn(w):
            tmp = Path(tempfile.mkdtemp()) / "screenshot.png"
            w._ensure_page().screenshot(path=str(tmp), full_page=False)
            return str(tmp)
        return _call(_fn)
    except Exception as e: return _err(f"screenshot failed: {e}")


# ---------------------------------------------------------------------------
# Tab management
# ---------------------------------------------------------------------------

def tab_list() -> str:
    e = ensure_available()
    if e: return e
    try:
        def _fn(w):
            if not w._browser: return "[]"
            tabs = []
            for ctx in w._browser.contexts:
                for i, p in enumerate(ctx.pages):
                    try: tabs.append(f"[{i}] {p.title()} - {p.url}")
                    except Exception: tabs.append(f"[{i}] (closed)")
            return "\n".join(tabs) if tabs else "[]"
        return _call(_fn)
    except Exception as e: return _err(f"tab_list failed: {e}")


def tab_new(url: str = "") -> str:
    e = ensure_available()
    if e: return e
    try:
        def _fn(w):
            page = w._context.new_page()
            w._page = page
            if url:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                return f"opened new tab with {url}"
            return "opened new tab"
        return _call(_fn)
    except Exception as e: return _err(f"tab_new failed: {e}")


def tab_select(target_id: str) -> str:
    e = ensure_available()
    if e: return e
    try:
        def _fn(w):
            if not w._browser: return _err("no browser")
            all_pages = [p for ctx in w._browser.contexts for p in ctx.pages]
            if target_id.isdigit():
                idx = int(target_id)
                if 0 <= idx < len(all_pages):
                    w._page = all_pages[idx]
                    try: w._page.bring_to_front()
                    except Exception: pass
                    return f"switched to tab [{idx}]"
            for p in all_pages:
                try:
                    if target_id in p.url or target_id in p.title():
                        w._page = p
                        try: p.bring_to_front()
                        except Exception: pass
                        return f"switched to tab matching '{target_id}'"
                except Exception: continue
            return _err(f"tab '{target_id}' not found")
        return _call(_fn)
    except Exception as e: return _err(f"tab_select failed: {e}")


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download(url: str, output_dir: str = ".") -> str:
    e = ensure_available()
    if e: return e
    try:
        def _fn(w):
            os.makedirs(output_dir, exist_ok=True)
            p = w._ensure_page()
            with p.expect_download(timeout=30000) as dl_info:
                p.goto(url, wait_until="domcontentloaded", timeout=30000)
            dl = dl_info.value
            dest = os.path.join(output_dir, dl.suggested_filename)
            dl.save_as(dest)
            return f"downloaded to {dest}"
        return _call(_fn)
    except Exception as e: return _err(f"download failed: {e}")


def _get_page():
    """Compatibility shim — page lives in worker thread, not accessible directly."""
    return None

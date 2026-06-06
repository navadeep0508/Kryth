"""Browser console error checker using browser_agent (browser-use).

Uses the same BrowserSession pattern as run_nvidia_agent.py, but headless,
dispatched through the shared browser worker event loop.
"""

from __future__ import annotations


def check_browser_errors(url: str, wait_seconds: int = 5) -> str:
    """Open a URL in a headless browser, collect console errors/warnings,
    and return them as a string so the agent can fix them.

    Parameters
    ----------
    url:
        The URL to open (e.g. ``http://localhost:5173``).
    wait_seconds:
        How long to wait for the page to load and scripts to run (default 5).
    """
    import asyncio
    from agent.providers.browser_use_provider import ensure_available, _run, _ensure_path

    e = ensure_available()
    if e:
        return e

    _ensure_path()

    errors: list[str] = []
    warnings: list[str] = []

    async def _check():
        from browser_agent import BrowserSession

        # Headless session, isolated from the persistent browser session
        browser = BrowserSession(headless=True)
        try:
            await browser.start()
            page = await browser.get_current_page()
            if page is None:
                await browser.new_page()
                page = await browser.get_current_page()
            if page is None:
                return "[ERROR] Could not create browser page"

            # Attach console capture before navigating
            await page.evaluate(
                "() => {"
                "  window.__kryth_errors = [];"
                "  window.__kryth_warnings = [];"
                "  const _e = console.error;"
                "  console.error = (...a) => { window.__kryth_errors.push(a.join(' ')); _e(...a); };"
                "  const _w = console.warn;"
                "  console.warn = (...a) => { window.__kryth_warnings.push(a.join(' ')); _w(...a); };"
                "}"
            )

            await browser.navigate_to(url)

            # Wait for JS to settle
            await asyncio.sleep(wait_seconds)

            raw_errors = await page.evaluate("() => window.__kryth_errors || []")
            raw_warnings = await page.evaluate("() => window.__kryth_warnings || []")

            import json as _json
            for raw, dest in ((raw_errors, errors), (raw_warnings, warnings)):
                if isinstance(raw, str):
                    try:
                        raw = _json.loads(raw)
                    except Exception:
                        raw = []
                dest.extend(raw or [])

            return None
        except Exception as ex:
            return f"[ERROR] Browser check failed: {ex}"
        finally:
            try:
                await browser.close()   # matches run_nvidia_agent.py pattern
            except Exception:
                pass

    err_msg = _run(_check(), timeout=max(wait_seconds + 15, 30))
    if err_msg:
        return err_msg

    lines: list[str] = []
    if errors:
        lines.append(f"CONSOLE ERRORS ({len(errors)}):")
        for e in errors:
            lines.append(f"  [error] {e}")
    if warnings:
        lines.append(f"CONSOLE WARNINGS ({len(warnings)}):")
        for w in warnings:
            lines.append(f"  [warn]  {w}")

    if not lines:
        return f"No console errors or network failures found at {url}"
    return "\n".join(lines)

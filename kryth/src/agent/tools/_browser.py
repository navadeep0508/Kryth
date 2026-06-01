"""Browser console error checker using Playwright."""

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
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return (
            "[ERROR] Playwright is not installed. "
            "Run: pip install 'kryth[bridge]' && playwright install chromium"
        )

    errors: list[str] = []
    warnings: list[str] = []
    network_errors: list[str] = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            def on_console(msg):
                t = msg.type
                text = msg.text
                if t == "error":
                    errors.append(text)
                elif t == "warning":
                    warnings.append(text)

            def on_request_failed(req):
                network_errors.append(
                    f"{req.method} {req.url} — {req.failure}"
                )

            page.on("console", on_console)
            page.on("requestfailed", on_request_failed)

            try:
                page.goto(url, timeout=max(wait_seconds, 10) * 1000, wait_until="domcontentloaded")
            except Exception as e:
                browser.close()
                return f"[ERROR] Could not load {url}: {e}"

            # Wait for JS to settle
            try:
                page.wait_for_timeout(wait_seconds * 1000)
            except Exception:
                pass

            browser.close()

    except Exception as e:
        return f"[ERROR] Browser check failed: {e}"

    lines: list[str] = []
    if errors:
        lines.append(f"CONSOLE ERRORS ({len(errors)}):")
        for e in errors:
            lines.append(f"  [error] {e}")
    if warnings:
        lines.append(f"CONSOLE WARNINGS ({len(warnings)}):")
        for w in warnings:
            lines.append(f"  [warn]  {w}")
    if network_errors:
        lines.append(f"NETWORK FAILURES ({len(network_errors)}):")
        for n in network_errors:
            lines.append(f"  [net]   {n}")

    if not lines:
        return f"No console errors or network failures found at {url}"

    return "\n".join(lines)

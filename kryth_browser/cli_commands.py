"""CLI commands for ``kryth browser`` group.

Registered on the main click group in main.py as the ``browser`` command
group with subcommands: setup, doctor, status, tabs, reconnect, uninstall.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import click

from config.loader import load_config
from ui.console import get_console  # type: ignore[attr-defined]

console = get_console()

from .manager import BrowserManager, DEFAULT_PORT
from .relay_client import read_relay_token
from .setup import BrowserSetupWizard


# ------------------------------------------------------------------ helpers

def _load_config_sync():
    try:
        return load_config(cwd=None)
    except Exception as exc:
        raise click.ClickException(f"Could not load KRYTH configuration: {exc}") from exc


def _async(coro):
    """Run an async coroutine from a sync click command context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already inside an event loop (e.g. REPL); run in a new thread.
            import concurrent.futures

            def _run():
                return asyncio.run(coro)

            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(_run).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


# ------------------------------------------------------------------ async entry points (for REPL / programmatic use)

async def run_browser_setup(console, args: list[str]) -> None:
    from .setup import BrowserSetupWizard
    config = _load_config_sync()
    browser_cfg = getattr(config, "kryth_browser", None)
    if not (browser_cfg and browser_cfg.enabled):
        console.print("[error]Browser integration is disabled.[/error]")
        return
    wizard = BrowserSetupWizard(config)
    result = await wizard.run()
    if result.success:
        console.print(f"[success]{result.output}[/success]")
    else:
        console.print(f"[error]{result.error or 'setup failed'}[/error]")
    await wizard.manager.shutdown()


async def run_browser_doctor(console, args: list[str]) -> None:
    config = _load_config_sync()
    console.print("KRYTH Browser setup check", style="bold primary")
    browser_cfg = getattr(config, "kryth_browser", None)
    enabled = bool(browser_cfg and browser_cfg.enabled)
    console.print(f"Browser integration: {'enabled' if enabled else 'disabled'}")
    if config.allowed_tools and not {
        "browser", "browser_connect", "browser_disconnect", "browser_status",
    }.intersection(config.allowed_tools):
        console.print("Browser tools: excluded by allowed_tools")
    if not enabled:
        console.print("Next: set [kryth_browser] enabled = true in your KRYTH config")
        return
    manager = BrowserManager(config)
    from .setup import BrowserSetupWizard
    wizard = BrowserSetupWizard(config)
    browser_ok = await wizard.check_browser_installed()
    console.print(f"Chrome/Edge: {'found' if browser_ok else 'not found'}")
    binary_ok = manager.relay_binary_path() is not None
    binary_path = manager.relay_binary_path()
    console.print(f"Relay binary: {'found' if binary_ok else 'not built'} {binary_path or ''}")
    running = BrowserManager.probe(DEFAULT_PORT)
    console.print(f"Relay server: {'running' if running else 'not running'} (port {DEFAULT_PORT})")
    token = read_relay_token()
    console.print(f"Relay token: {'present' if token else 'missing'}")
    if running and token:
        console.print("KRYTH Browser is ready. You can ask KRYTH to use the browser.")
    else:
        console.print("Next: run `kryth browser setup` to finish first-run setup.")
    await manager.shutdown()


async def run_browser_status(console, args: list[str]) -> None:
    config = _load_config_sync()
    manager = BrowserManager(config)
    client = manager.get_client()
    if client is None:
        started = await manager.start_relay()
        if not started:
            console.print("KRYTH Browser: not connected. Relay is not running.")
            console.print("Run `kryth browser setup` to connect.")
            return
        client = manager.get_client()
    try:
        version = await client.get_version()
        status = await client.extension_status(waiting=False) or {}
        console.print("KRYTH Browser: connected")
        console.print(f"  Relay version: {version.get('version', '?')}")
        console.print(f"  Relay port: {client.port}")
        console.print(f"  Extension: {status.get('connectionState', 'unknown')}")
    finally:
        await manager.shutdown()


async def run_browser_tabs(console, args: list[str]) -> None:
    config = _load_config_sync()
    manager = BrowserManager(config)
    action = args[0] if args else "list"
    url = None
    tab = None
    for i, a in enumerate(args):
        if a == "--url" and i + 1 < len(args):
            url = args[i + 1]
        if a == "--tab" and i + 1 < len(args):
            tab = args[i + 1]

    async def run():
        client = manager.get_client()
        if client is None:
            if not await manager.start_relay():
                console.print("Could not start the relay.")
                return
            client = manager.get_client()
        if action == "list":
            result = await client.call_tool("tabs", {"action": "list"})
            tabs = result.get("tabs", []) if isinstance(result, dict) else []
            console.print(f"Open tabs: {len(tabs)}")
            for t in tabs:
                console.print(f"  {t.get('id', '?')}  {t.get('url', '')}  {t.get('title', '')}")
        elif action == "new":
            result = await client.call_tool("tabs", {"action": "new", "url": url or "about:blank"})
            console.print(f"Opened tab: {result}")
        elif action == "close":
            result = await client.call_tool("tabs", {"action": "close", "tab": tab})
            console.print(f"Closed tab: {result}")
        await manager.shutdown()

    await run()


async def run_browser_reconnect(console, args: list[str]) -> None:
    config = _load_config_sync()
    manager = BrowserManager(config)
    await manager.stop_relay()
    ok = await manager.start_relay()
    if ok:
        console.print("KRYTH Browser relay reconnected.")
    else:
        console.print("[error]Could not restart the KRYTH Browser relay.[/error]")


async def run_browser_uninstall(console, args: list[str]) -> None:
    config = _load_config_sync()
    manager = BrowserManager(config)
    await manager.stop_relay()
    console.print("KRYTH Browser uninstalled (relay stopped).")


# ------------------------------------------------------------------ group

@click.group("browser", invoke_without_command=True)
@click.pass_context
def browser(ctx: click.Context):
    """Set up and diagnose the KRYTH Browser (vendored anomalyco/browse relay)."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.parent.get_help() if ctx.parent else ctx.get_help())


# ------------------------------------------------------------------ doctor

@browser.command("doctor")
def browser_doctor():
    """Check browser, relay binary, and relay-server readiness."""
    console.print("KRYTH Browser setup check", style="bold primary")
    try:
        config = _load_config_sync()
    except Exception as exc:
        raise click.ClickException(str(exc))

    browser_cfg = getattr(config, "kryth_browser", None)
    enabled = bool(browser_cfg and browser_cfg.enabled)
    console.print(f"Browser integration: {'enabled' if enabled else 'disabled'}")
    if config.allowed_tools and not {
        "browser", "browser_connect", "browser_disconnect", "browser_status",
    }.intersection(config.allowed_tools):
        console.print("Browser tools: excluded by allowed_tools")

    if not enabled:
        console.print("Next: set [kryth_browser] enabled = true in your KRYTH config")
        return

    manager = BrowserManager(config)

    # 1. Chrome/Edge
    from .setup import BrowserSetupWizard
    wizard = BrowserSetupWizard(config)
    browser_ok = _async(wizard.check_browser_installed())
    console.print(f"Chrome/Edge: {'found' if browser_ok else 'not found'}")

    # 2. Relay binary
    binary_ok = manager.relay_binary_path() is not None
    binary_path = manager.relay_binary_path()
    console.print(f"Relay binary: {'found' if binary_ok else 'not built'} {binary_path or ''}")
    if not binary_ok:
        console.print("Next: run `cargo build --release` in kryth_browser/vendor/rust/")

    # 3. Relay running
    running = BrowserManager.probe(DEFAULT_PORT)
    console.print(f"Relay server: {'running' if running else 'not running'} (port {DEFAULT_PORT})")

    # 4. Token
    token = read_relay_token()
    console.print(f"Relay token: {'present' if token else 'missing'}")

    if running and token:
        console.print("KRYTH Browser is ready. You can ask KRYTH to use the browser.")
    else:
        console.print("Next: run `kryth browser setup` to finish first-run setup.")
    _async(manager.shutdown())


# ------------------------------------------------------------------ setup

@browser.command("setup")
def browser_setup():
    """Run the KRYTH Browser first-run setup wizard."""
    config = _load_config_sync()
    browser_cfg = getattr(config, "kryth_browser", None)
    if not (browser_cfg and browser_cfg.enabled):
        raise click.ClickException(
            "Browser integration is disabled. Set [kryth_browser] enabled = true in your KRYTH config, then retry."
        )
    if config.allowed_tools and not {
        "browser", "browser_connect", "browser_disconnect", "browser_status",
    }.intersection(config.allowed_tools):
        raise click.ClickException("Browser tools are excluded by allowed_tools in your KRYTH config.")

    wizard = BrowserSetupWizard(config)
    result = _async(wizard.run())
    if result.success:
        console.print(f"[success]{result.output}[/success]")
    else:
        raise click.ClickException(result.error or "KRYTH Browser setup failed")
    _async(wizard.manager.shutdown())


# ------------------------------------------------------------------ status

@browser.command("status")
def browser_status():
    """Print current KRYTH Browser relay and connection status."""
    config = _load_config_sync()
    manager = BrowserManager(config)
    client = manager.get_client()
    if client is None:
        started = _async(manager.start_relay())
        if not started:
            console.print("KRYTH Browser: not connected. Relay is not running.")
            console.print("Run `kryth browser setup` to connect.")
            return
        client = manager.get_client()

    try:
        version = _async(client.get_version())
        status = _async(client.extension_status(waiting=False)) or {}
        console.print(f"KRYTH Browser: connected")
        console.print(f"  Relay version: {version.get('version', '?')}")
        console.print(f"  Relay port: {client.port}")
        console.print(f"  Extension: {status.get('connectionState', 'unknown')}")
    finally:
        _async(manager.shutdown())


# ------------------------------------------------------------------ tabs

@browser.command("tabs")
@click.argument("action", type=click.Choice(["list", "new", "close"]), default="list")
@click.option("--url", default=None, help="URL when opening a new tab")
@click.option("--tab", default=None, help="Tab id for close/switch")
def browser_tabs(action: str, url: str | None, tab: str | None):
    """List, open, or close browser tabs via the relay."""
    config = _load_config_sync()
    manager = BrowserManager(config)

    async def run():
        client = manager.get_client()
        if client is None:
            if not await manager.start_relay():
                console.print("Could not start the relay.")
                return
            client = manager.get_client()
        if action == "list":
            result = await client.call_tool("tabs", {"action": "list"})
            tabs = result.get("tabs", []) if isinstance(result, dict) else []
            console.print(f"Open tabs: {len(tabs)}")
            for t in tabs:
                console.print(f"  {t.get('id', '?')}  {t.get('url', '')}  {t.get('title', '')}")
        elif action == "new":
            result = await client.call_tool("tabs", {"action": "new", "url": url or "about:blank"})
            console.print(f"Opened tab: {result}")
        elif action == "close":
            result = await client.call_tool("tabs", {"action": "close", "tab": tab})
            console.print(f"Closed tab: {result}")
        await manager.shutdown()

    _async(run())


# ------------------------------------------------------------------ reconnect

@browser.command("reconnect")
def browser_reconnect():
    """Stop and restart the KRYTH Browser relay."""
    config = _load_config_sync()
    manager = BrowserManager(config)
    _async(manager.stop_relay())
    ok = _async(manager.start_relay())
    if ok:
        console.print("KRYTH Browser relay reconnected.")
    else:
        raise click.ClickException("Could not restart the KRYTH Browser relay.")


# ------------------------------------------------------------------ uninstall

@browser.command("uninstall")
def browser_uninstall():
    """Remove KRYTH Browser integration: stop relay, clear state files."""
    config = _load_config_sync()
    manager = BrowserManager(config)
    _async(manager.stop_relay())
    console.print("KRYTH Browser uninstalled (relay stopped).")

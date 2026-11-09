from pathlib import Path
import sys

# Ensure the project root is on sys.path so all sub-packages resolve
# correctly regardless of the working directory or shadowing namespace packages.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.agent import Agent
from agent.store import SessionStore
from config.config import Config
from config.loader import load_config
from config.credentials import (
    clear_credentials,
    get_credentials_path,
    load_credentials,
    save_credentials,
)
from config.oauth import OAuthError, login_with_oauth
from config.setup_wizard import SetupWizard
from ui import TUI, Repl, get_console, stream_turn
from ui.components.logo import KRYTH_VERSION
from ui.setup_tui import SetupTUI
import asyncio
import click
import json
import subprocess
from importlib.metadata import PackageNotFoundError, version as installed_version
from urllib.error import URLError
from urllib.request import urlopen

from packaging.version import InvalidVersion, Version

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

console = get_console()


def _latest_version() -> Version:
    with urlopen("https://pypi.org/pypi/kryth/json", timeout=5) as response:
        data = json.load(response)
    return Version(data["info"]["version"])


def _upgrade_notice() -> str | None:
    try:
        current = Version(installed_version("kryth"))
        latest = _latest_version()
    except (
        PackageNotFoundError,
        InvalidVersion,
        KeyError,
        TypeError,
        URLError,
        TimeoutError,
        OSError,
        ValueError,
    ):
        return None

    if latest > current:
        return f"KRYTH {latest} is available (you have {current}). Use `kryth upgrade` to update."
    return None

async def run_once(config: Config, message: str, resume: str | None = None) -> str | None:
    tui = TUI(config)
    async with Agent(
        config, confirmation_callback=tui.confirm_tool, progress_callback=tui.tool_progress, resume=resume
    ) as agent:
        tui.render_approval_mode()
        if resume and agent.resumed is None:
            console.print(f"[warning]No saved session matched '{resume}'.[/warning]")
        return await stream_turn(tui, agent, message)


def _resolve_resume(
    config: Config,
    resume: str | None,
    continue_last: bool,
) -> str | None:
    """Turn --resume / --continue into a session id, or None to start fresh."""

    if resume:
        return resume

    if not continue_last:
        return None

    latest = SessionStore().latest(config.cwd)
    if latest is None:
        console.print("[warning]No saved session for this directory, starting a new one.[/warning]")
        return None

    return latest.session_id


class DefaultGroup(click.Group):

    def resolve_command(self, ctx, args):
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            return "run", self.get_command(ctx, "run"), args


@click.group(
    cls=DefaultGroup,
    invoke_without_command=True,
    epilog="""\b
Examples:
  kryth                    Start the interactive TUI
  kryth "fix the bug"      Run a single prompt and exit
  kryth --setup            Run the setup wizard
  kryth --continue         Resume the last session in this directory
  kryth --resume 123456    Resume a specific session by ID
"""
)
@click.version_option(version=KRYTH_VERSION, prog_name="kryth")
@click.option(
    '--cwd',
    '-c',
    type=click.Path(
        exists=True,
        file_okay=False,
        path_type=Path,
    ),
    help='Current Working Directory'
)
@click.option(
    "--setup",
    "-s",
    "setup",
    is_flag=True,
    help="Run the first-launch setup wizard.",
)
@click.option(
    "--resume",
    "-r",
    "resume",
    default=None,
    metavar="SESSION",
    help="Resume a saved session by id (a prefix is enough).",
)
@click.option(
    "--continue",
    "-C",
    "continue_last",
    is_flag=True,
    help="Resume the most recent session for this directory.",
)
@click.pass_context
def main(ctx: click.Context, cwd: Path | None, setup: bool, resume: str | None, continue_last: bool):
    ctx.obj = {"cwd": cwd, "resume": resume, "continue": continue_last, "setup": setup}
    # Bare `kryth` with no subcommand launches the interactive TUI.
    if ctx.invoked_subcommand is None:
        ctx.invoke(run, prompt=None, setup=setup)


@main.command(
    epilog="""\b
Examples:
  kryth run                       Launch the interactive TUI (same as just `kryth`)
  kryth run "write a hello world" Run a one-shot prompt
"""
)
@click.argument("prompt", required=False)
@click.pass_context
def run(ctx: click.Context, prompt: str | None, setup: bool = False):
    """Run a one-shot prompt, or launch the TUI when no prompt is given."""
    cwd = ctx.obj.get("cwd") if ctx.obj else None

    try:
        config = load_config(cwd=cwd)
    except Exception as e:
        console.print(f"[error]Config Error: {e}[/error]")
        sys.exit(1)

    # First-launch detection: run setup wizard when no credentials exist.
    wizard = SetupWizard(config)
    if setup or wizard.is_first_launch():
        result = asyncio.run(SetupTUI(config, console).run())
        if not result.success:
            console.print(f"[warning]Setup cancelled: {result.message}[/warning]")
            if wizard.is_first_launch():
                console.print("[warning]No provider configured. Some features will be unavailable.[/warning]")
            else:
                return

    errors = config.validate()

    if errors:
        # Auto-launch setup when no API key is configured, instead of just erroring out.
        if any("No API key" in e for e in errors):
            console.print()
            console.print("[info]No provider configured. Launching setup...[/info]")
            result = asyncio.run(SetupTUI(config, console).run())
            if not result.success:
                console.print(f"[warning]Setup cancelled: {result.message}[/warning]")
                sys.exit(1)
        else:
            for error in errors:
                console.print(f'[error]Config Error: {error}[/error]')
            sys.exit(1)

    resume = _resolve_resume(
        config,
        ctx.obj.get("resume") if ctx.obj else None,
        bool(ctx.obj.get("continue")) if ctx.obj else False,
    )

    if prompt:
        result = asyncio.run(run_once(config, prompt, resume=resume))
        if result is None:
            sys.exit(1)
    else:
        asyncio.run(Repl(config, resume=resume, upgrade_notice=_upgrade_notice()).run())


@main.command(
    epilog="""\b
Examples:
  kryth setup          Run the interactive setup wizard
  kryth setup --paste  Paste API key directly instead of interactive prompts
"""
)
@click.option(
    "--paste",
    is_flag=True,
    help="Paste an API key directly instead of the full interactive wizard.",
)
def setup(paste: bool):
    """Run the interactive setup wizard to configure a provider."""
    try:
        config = load_config(cwd=None)
    except Exception as e:
        console.print(f"[error]Config Error: {e}[/error]")
        sys.exit(1)

    from config.setup_wizard import SetupWizard

    wizard = SetupWizard(config)

    if paste:
        # Quick paste mode: just ask for provider and API key
        from prompt_toolkit import PromptSession
        session = PromptSession()

        console.print("KRYTH Quick Setup", style="bold #E8FF3A")
        console.print()

        provider = session.prompt("Provider name (e.g., openrouter, nvidia, groq) > ").strip()
        if not provider:
            console.print("[error]No provider specified[/error]")
            sys.exit(1)

        defaults = wizard.select_provider_defaults(provider.lower().replace(" ", "-"))
        base_url = session.prompt(
            f"Base URL > ",
            default=defaults.get("base_url", ""),
        ).strip()

        model = session.prompt("Model ID > ").strip()
        if not model:
            console.print("[error]No model specified[/error]")
            sys.exit(1)

        api_key = session.prompt("API key > ", hide_input=True).strip()
        if not api_key:
            console.print("[error]No API key entered[/error]")
            sys.exit(1)

        ok, message, latency = asyncio.run(
            wizard.test_connection(provider, base_url, api_key, model)
        )
        if not ok:
            console.print(f"[error]Connection test failed: {message}[/error]")
            sys.exit(1)

        console.print(f"[ok] {message}", style="bold green")
        wizard.save_setup(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            model=model,
        )
        console.print("[success]Setup complete![/success]")
        return

    result = asyncio.run(SetupTUI(config, console).run())
    if not result.success:
        console.print(f"[warning]Setup cancelled: {result.message}[/warning]")
        sys.exit(1)


@main.command(
    epilog="""\b
Examples:
  kryth login          Open your browser to securely authorize with OpenRouter
  kryth login --paste  Paste an API key directly in the terminal
"""
)
@click.option(
    "--base-url",
    default=None,
    help=f"API base URL to save (default: {DEFAULT_BASE_URL})",
)
@click.option(
    "--paste",
    is_flag=True,
    help="Paste an API key manually instead of authorizing in the browser.",
)
def login(base_url: str | None, paste: bool):
    """Log in to OpenRouter via your browser (or --paste an API key)."""
    if load_credentials().get("api_key"):
        if not click.confirm("You're already logged in. Overwrite the saved key?", default=False):
            console.print("[warning]Login cancelled.[/warning]")
            return

    resolved_base_url = base_url or DEFAULT_BASE_URL

    if paste:
        api_key = click.prompt("Paste your OpenRouter API key", hide_input=True).strip()
        if not api_key:
            console.print("[error]No key entered, nothing saved.[/error]")
            sys.exit(1)
    else:
        console.print("Opening your browser to authorize kryth with OpenRouter...")
        try:
            api_key = login_with_oauth(resolved_base_url)
        except OAuthError as e:
            console.print(f"[error]Login failed:[/error] {e}")
            console.print("[warning]You can retry, or run `kryth login --paste` to enter a key manually.[/warning]")
            sys.exit(1)

    path = save_credentials(api_key, resolved_base_url)
    console.print(f"[success]Logged in.[/success] Key saved to {path}")
    console.print("[warning]The API_KEY environment variable, if set, still takes precedence.[/warning]")


@main.group(
    invoke_without_command=True,
    epilog="""\b
Examples:
  kryth sessions        List all saved sessions for the current directory
  kryth sessions --all  List all saved sessions across all directories
"""
)
@click.option(
    "--all",
    "all_projects",
    is_flag=True,
    help="List sessions from every directory, not just this one.",
)
@click.pass_context
def sessions(ctx: click.Context, all_projects: bool):
    """List saved sessions."""

    if ctx.invoked_subcommand is not None:
        return

    cwd = (ctx.obj.get("cwd") if ctx.obj else None) or Path.cwd()
    saved = SessionStore().list(None if all_projects else cwd)

    if not saved:
        scope = "" if all_projects else f" for {cwd}"
        console.print(f"[warning]No saved sessions{scope}.[/warning]")
        return

    for meta in saved:
        turns = f"{meta.turns} turn{'s' if meta.turns != 1 else ''}"
        console.print(
            f"[info]{meta.short_id}[/info]  "
            f"{meta.updated_at:%Y-%m-%d %H:%M}  "
            f"{turns:>9}  {meta.title}"
        )
        if all_projects:
            console.print(f"[muted]{' ' * 10}{meta.cwd}[/muted]")

    console.print()
    console.print("[muted]Resume one with kryth --resume <id>[/muted]")


@sessions.command(
    "rm",
    epilog="""\b
Examples:
  kryth sessions rm 123456    Delete the session matching the ID prefix
"""
)
@click.argument("session_id")
def sessions_rm(session_id: str):
    """Delete a saved session."""

    store = SessionStore()
    resolved = store.resolve(session_id)

    if resolved is None:
        console.print(f"[error]No saved session matching '{session_id}'.[/error]")
        sys.exit(1)

    meta = store.read_meta(resolved)
    if store.delete(resolved):
        title = f" - {meta.title}" if meta else ""
        console.print(f"[success]Deleted session {resolved[:8]}[/success]{title}")
    else:
        console.print(f"[error]Could not delete {resolved[:8]}.[/error]")
        sys.exit(1)


@main.command()
def logout():
    """Remove the saved OpenRouter API key."""
    if clear_credentials():
        console.print(f"[success]Logged out.[/success] Removed {get_credentials_path()}")
    else:
        console.print("[warning]No saved credentials to remove.[/warning]")


@main.command()
def upgrade():
    try:
        current = Version(installed_version("kryth"))
    except PackageNotFoundError as error:
        raise click.ClickException(
            "Could not determine the installed kryth version; install kryth before upgrading."
        ) from error

    try:
        latest = _latest_version()
    except (InvalidVersion, KeyError, TypeError, URLError, TimeoutError, OSError, ValueError) as error:
        raise click.ClickException(f"Could not check for updates: {error}") from error

    if latest <= current:
        console.print(f"[success]Kryth is already up to date ({current}).[/success]")
        return

    console.print(f"Updating kryth from {current} to {latest}...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "kryth"],
        check=False,
    )
    if result.returncode:
        raise click.ClickException("kryth upgrade failed")
    console.print(f"[success]Kryth upgraded to {latest}.[/success]")


@main.command("/help")
@click.pass_context
def cli_help(ctx: click.Context):
    """Alias for --help."""
    click.echo(ctx.parent.get_help())


if __name__ == "__main__": # better than just main() ngl
    main()

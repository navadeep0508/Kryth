"""REPL entrypoint for KRYTH AI Coder.

Thin loop: read input through Prompt Toolkit, route to a REPL command,
a /skill invocation, or the agent. Every byte of user-facing output
flows through ``agent.ui``.
"""

from __future__ import annotations

from agent import ui
from agent.agent_loop import run_agent
from agent.session import get_session, reset_session
from agent.skills import list_skills, parse_slash
from agent.ui.input import PromptUI


REPL_COMMANDS = {
    "/clear", "/todos", "/tokens", "/plan", "/skills", "/help", "/diag",
    "/log", "/resume", "/memory", "/profile", "/config", "/bridge",
    "/models", "/tools", "/status", "/session",
}


def _slash_names() -> list[str]:
    """All commands that should appear in the completer.

    REPL commands without the leading slash + every loadable skill.
    """
    base = [c[1:] for c in REPL_COMMANDS]
    return sorted(set(base) | set(list_skills()))


def _toolbar_data() -> dict:
    """Live session state for the Prompt Toolkit bottom toolbar."""
    try:
        from agent.llm import MAIN_MODEL
        model = MAIN_MODEL
    except Exception:
        model = "?"
    try:
        s = get_session()
        tokens = s.cumulative_in_tokens + s.cumulative_out_tokens
        return {
            "model": model,
            "mode": s.mode,
            "profile": s.profile,
            "tokens": tokens,
            "depth": s.depth,
        }
    except Exception:
        return {"model": model, "mode": "default"}


# ---------------------------------------------------------------------------
# REPL commands
# ---------------------------------------------------------------------------

def _cmd_clear(_args: str = "") -> None:
    reset_session()
    ui.session_reset()


def _cmd_todos(_args: str = "") -> None:
    s = get_session()
    ui.todos(s.todos or [])


def _cmd_tokens(_args: str = "") -> None:
    s = get_session()
    ui.status(
        model="(idle)",
        mode=s.mode,
        tokens_in=s.cumulative_in_tokens,
        tokens_out=s.cumulative_out_tokens,
    )
    ui.muted(
        f"messages {len(s.messages)}   "
        f"context~{s.total_tokens():,}   "
        f"tool calls {s.tool_call_count}"
    )


def _cmd_plan(_args: str = "") -> None:
    s = get_session()
    s.mode = "plan" if s.mode != "plan" else "default"
    if s.mode == "plan":
        ui.plan_mode_active()
    else:
        ui.muted("mode → default")


def _cmd_skills(_args: str = "") -> None:
    names = list_skills()
    ui.muted("available skills: " + ", ".join(f"/{n}" for n in names))


def _cmd_diag(_args: str = "") -> None:
    import os
    from agent.llm import (
        BASE_URL,
        MAIN_MODEL,
        PLANNER_MODEL,
        SUMMARIZER_MODEL,
        health_check,
    )

    ui.muted("running diagnostics…")
    ui.muted(f"base_url    {BASE_URL}")
    ui.muted(f"main        {MAIN_MODEL}")
    ui.muted(f"planner     {PLANNER_MODEL}")
    ui.muted(f"summarizer  {SUMMARIZER_MODEL}")

    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        ui.error("OPENAI_API_KEY is not set. Add it to .env in the project root.")
    else:
        mask = key[:6] + "…" + key[-4:] if len(key) > 12 else "***"
        ui.muted(f"OPENAI_API_KEY  {mask}   (len={len(key)})")

    results = health_check()
    for label, status in results.items():
        if status == "ok":
            ui.success(f"  ok    {label}")
        else:
            ui.error(f"  fail  {label}  ·  {status}")


def _cmd_log(args: str = "") -> None:
    """``/log``           tail last 50 lines of the debug log
    ``/log N``         tail last N lines (capped at 1000)
    ``/log path``      print the absolute path of the log file
    ``/log clear``     truncate the active log file
    """
    from agent.ui.logger import log_file_path

    path = log_file_path()
    if path is None:
        ui.error("debug log unavailable (could not initialize file handler)")
        return

    args = (args or "").strip()

    if args == "path":
        ui.muted(str(path))
        return

    if args == "clear":
        try:
            path.write_text("", encoding="utf-8")
        except OSError as e:
            ui.error(f"could not truncate {path}: {e}")
            return
        ui.muted(f"truncated {path}")
        return

    try:
        n = max(1, min(int(args), 1000)) if args else 50
    except ValueError:
        ui.error(
            "usage: /log [N|path|clear]   "
            "(N = number of trailing lines to show)"
        )
        return

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        ui.error(f"could not read {path}: {e}")
        return

    if not lines:
        ui.muted(f"debug log empty · {path}")
        return

    tail = lines[-n:]
    from rich.panel import Panel
    from rich.text import Text
    from agent.ui.console import console

    body = Text("".join(tail).rstrip(), no_wrap=False, style="muted")
    title = (
        f"[muted]debug log · last {len(tail)} of "
        f"{len(lines):,} lines[/muted]"
    )
    panel = Panel(
        body,
        title=title,
        title_align="left",
        subtitle=f"[muted]{path}[/muted]",
        subtitle_align="right",
        border_style="divider",
        padding=(0, 1),
        expand=False,
    )
    console.print()
    console.print(panel)


def _cmd_help(_args: str = "") -> None:
    """Premium help screen with clear command categories."""
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from agent.ui.console import console
    from agent.ui.theme import CORE, DOT

    table = Table.grid(padding=(0, 2), expand=False)
    table.add_column(no_wrap=True, style="kryth.core", min_width=12)
    table.add_column(overflow="fold", style="muted")

    # Core commands
    commands = [
        ("/help", "Show this command list"),
        ("/status", "Show session status and metrics"),
        ("/models", "Show configured model routing"),
        ("/tools", "Show available tools"),
        ("/config", "Edit model, endpoint, and API key"),
        ("/profile", "View or change permission profile"),
        ("/memory", "Inspect project/user memory"),
        ("/session", "List or resume sessions"),
        ("/diag", "Ping configured models"),
        ("/bridge", "Manage browser provider bridge"),
    ]

    for cmd, desc in commands:
        table.add_row(
            Text.assemble((CORE, "kryth.core"), (" " + cmd, "title")),
            desc
        )

    console.print()
    console.print(Panel(
        table,
        title=Text.assemble((CORE, "kryth.core"), (" KRYTH Commands", "title")),
        title_align="left",
        border_style="hud.border",
        padding=(1, 2),
        expand=False,
    ))
    console.print()
    ui.muted(f"{CORE} enter submits  {DOT}  \\ enter inserts newline  {DOT}  type / to discover commands")


def _cmd_models(_args: str = "") -> None:
    """Premium model configuration display."""
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from agent.ui.console import console
    from agent.ui.theme import CORE
    from agent.llm import BASE_URL, MAIN_MODEL, PLANNER_MODEL, SUMMARIZER_MODEL
    from agent.model_router import describe_routing

    routing = describe_routing()
    table = Table.grid(padding=(0, 2), expand=False)
    table.add_column(no_wrap=True, style="muted", min_width=12)
    table.add_column(overflow="fold", style="title")

    table.add_row(
        Text.assemble((CORE, "kryth.core"), (" MAIN", "muted")),
        MAIN_MODEL
    )
    table.add_row(
        Text.assemble((CORE, "kryth.core"), (" PLANNER", "muted")),
        PLANNER_MODEL
    )
    table.add_row(
        Text.assemble((CORE, "kryth.core"), (" SUMMARIZER", "muted")),
        SUMMARIZER_MODEL
    )
    table.add_row(
        Text.assemble((CORE, "kryth.core"), (" ENDPOINT", "muted")),
        BASE_URL
    )
    table.add_row(
        Text.assemble((CORE, "kryth.core"), (" AUTO ROUTE", "muted")),
        str(routing.get("auto_route"))
    )

    console.print()
    console.print(Panel(
        table,
        title=Text.assemble((CORE, "kryth.core"), (" Model Configuration", "title")),
        title_align="left",
        border_style="hud.border",
        padding=(1, 2),
        expand=False,
    ))


def _cmd_tools(_args: str = "") -> None:
    """Premium tools display with grid layout."""
    from rich.columns import Columns
    from rich.panel import Panel
    from rich.text import Text
    from agent.tools import TOOLS
    from agent.ui.console import console
    from agent.ui.theme import CORE

    # Create tool items with diamond bullets
    names = [
        Text.assemble((CORE + " ", "kryth.core"), (name, "title"))
        for name in sorted(TOOLS)
    ]

    console.print()
    console.print(Panel(
        Columns(names, equal=True, expand=False, padding=(0, 2)),
        title=Text.assemble((CORE, "kryth.core"), (" Available Tools", "title")),
        subtitle=Text.assemble(("[", "muted"), (str(len(TOOLS)), "title"), (" loaded]", "muted")),
        title_align="left",
        subtitle_align="right",
        border_style="hud.border",
        padding=(1, 2),
        expand=False,
    ))


def _cmd_status(_args: str = "") -> None:
    """Premium session status display."""
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from agent.ui.console import console
    from agent.ui.theme import CORE, DOT

    s = get_session()
    model = _toolbar_data().get("model", "?")
    total_tokens = s.cumulative_in_tokens + s.cumulative_out_tokens

    table = Table.grid(padding=(0, 2), expand=False)
    table.add_column(no_wrap=True, style="muted", min_width=12)
    table.add_column(overflow="fold", style="title")

    table.add_row(
        Text.assemble((CORE, "kryth.core"), (" MODEL", "muted")),
        model
    )
    table.add_row(
        Text.assemble((CORE, "kryth.core"), (" MODE", "muted")),
        s.mode
    )
    table.add_row(
        Text.assemble((CORE, "kryth.core"), (" PROFILE", "muted")),
        s.profile
    )
    table.add_row(
        Text.assemble((CORE, "kryth.core"), (" MESSAGES", "muted")),
        str(len(s.messages))
    )
    table.add_row(
        Text.assemble((CORE, "kryth.core"), (" TOKENS", "muted")),
        f"{total_tokens:,}  [muted](in {s.cumulative_in_tokens:,} / out {s.cumulative_out_tokens:,})[/muted]"
    )
    table.add_row(
        Text.assemble((CORE, "kryth.core"), (" TOOL CALLS", "muted")),
        str(s.tool_call_count)
    )
    table.add_row(
        Text.assemble((CORE, "kryth.core"), (" DEPTH", "muted")),
        str(s.depth)
    )

    console.print()
    console.print(Panel(
        table,
        title=Text.assemble((CORE, "kryth.core"), (" Session Status", "title")),
        title_align="left",
        border_style="hud.border",
        padding=(1, 2),
        expand=False,
    ))


def _cmd_session(args: str = "") -> None:
    arg = (args or "").strip()
    _cmd_resume(arg if arg else "list")


def _cmd_resume(args: str = "") -> None:
    """``/resume``           list recent sessions for this project and pick one
    ``/resume latest``     restore the most recent session for this project
    ``/resume <id-prefix>`` restore a specific session (id prefix is enough)
    """
    from agent.persistence import (
        session_store,
        list_recent,
        load_session,
        latest_session_id,
    )
    from agent.session import get_session

    metas = list_recent(limit=20)
    if not metas:
        ui.muted("no saved sessions for this project")
        return

    arg = (args or "").strip().lower()

    # Resolve which session to load.
    target_id: str | None = None
    if arg in ("", "list", "ls"):
        _render_session_list(metas)
        if arg in ("list", "ls"):
            return
        # Interactive pick — fall through to the prompt below.
    elif arg in ("latest", "last", "-1"):
        target_id = latest_session_id()
    else:
        # Prefix match against session ids.
        candidates = [m for m in metas if m.session_id.startswith(arg)]
        if not candidates:
            ui.error(f"no session id starts with {arg!r}")
            return
        if len(candidates) > 1:
            ui.error(
                f"{arg!r} is ambiguous; matches "
                + ", ".join(c.session_id[:8] for c in candidates[:5])
            )
            return
        target_id = candidates[0].session_id

    if target_id is None:
        # Need to prompt for a pick. We're inside the REPL loop already,
        # so we just print a hint and ask the user to call the command
        # again with a chosen id. (A full inline picker requires a
        # nested Prompt-Toolkit session.)
        ui.muted(
            "pick a session: re-run /resume <id-prefix> "
            "with one of the IDs above, or /resume latest"
        )
        return

    loaded = load_session(target_id)
    if loaded is None:
        ui.error(f"could not load session {target_id}")
        return

    meta, messages = loaded
    session = get_session()
    session.messages = list(messages)
    session.cumulative_in_tokens = meta.cumulative_in_tokens
    session.cumulative_out_tokens = meta.cumulative_out_tokens
    session.mode = meta.mode
    session.profile = meta.profile or "default"
    session.ensure_system()

    # Attach the persisted file so further turns append to the same log.
    session_store().attach(meta.session_id, meta.project_hash)

    ui.success(
        f"resumed session {meta.session_id[:8]}  "
        f"({meta.message_count} messages, "
        f"{meta.cumulative_in_tokens + meta.cumulative_out_tokens:,} tokens, "
        f"profile={session.profile})"
    )
    if meta.first_user_preview:
        ui.muted(f"first turn: {meta.first_user_preview}")


def _render_session_list(metas) -> None:
    """Render the recent-sessions table for /resume."""
    import time as _time
    from rich.table import Table
    from agent.ui.console import console

    table = Table(
        title=f"[muted]recent sessions ({len(metas)})[/muted]",
        title_justify="left",
        title_style="muted",
        show_header=True,
        header_style="muted",
        border_style="divider",
        expand=False,
    )
    table.add_column("id", style="accent", no_wrap=True)
    table.add_column("when", style="muted", no_wrap=True)
    table.add_column("msgs", style="muted", justify="right", no_wrap=True)
    table.add_column("tokens", style="muted", justify="right", no_wrap=True)
    table.add_column("first turn", overflow="fold")

    now = _time.time()
    for m in metas:
        age_s = max(0.0, now - m.last_updated)
        when = _short_age(age_s)
        tokens = m.cumulative_in_tokens + m.cumulative_out_tokens
        preview = m.first_user_preview or "(no preview)"
        table.add_row(
            m.session_id[:8],
            when,
            f"{m.message_count:,}",
            f"{tokens:,}",
            preview,
        )

    console.print()
    console.print(table)


def _short_age(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


_HANDLERS = {
    "/clear":   _cmd_clear,
    "/todos":   _cmd_todos,
    "/tokens":  _cmd_tokens,
    "/plan":    _cmd_plan,
    "/skills":  _cmd_skills,
    "/diag":    _cmd_diag,
    "/help":    _cmd_help,
    "/models":  _cmd_models,
    "/tools":   _cmd_tools,
    "/status":  _cmd_status,
    "/session": _cmd_session,
    "/log":     _cmd_log,
    "/resume":  _cmd_resume,
    "/memory":  None,  # bound below once _cmd_memory is defined
}


def _cmd_memory(args: str = "") -> None:
    """``/memory``           list the loaded memory layers + sizes
    ``/memory show``      print each layer's content in full
    ``/memory edit``      open the project-scope memory file in $EDITOR
    ``/memory path``      print the absolute paths of each layer
    """
    from agent.project_context import (
        load_memory_layers,
        project_root,
        PROJECT_MEMORY_NAMES,
        USER_MEMORY_NAMES,
    )
    import os as _os
    import subprocess as _sp

    arg = (args or "").strip().lower()
    layers = load_memory_layers(".")

    if arg in ("", "list", "ls"):
        if not layers:
            ui.muted(
                "no memory layers loaded · "
                f"project root looks for {', '.join(PROJECT_MEMORY_NAMES[:3])}, "
                f"user-global at ~/.ai-coder/{USER_MEMORY_NAMES[0]}"
            )
            return
        from rich.table import Table
        from agent.ui.console import console
        table = Table(
            title=f"[muted]memory layers ({len(layers)})[/muted]",
            title_justify="left",
            title_style="muted",
            show_header=True,
            header_style="muted",
            border_style="divider",
            expand=False,
        )
        table.add_column("scope", style="accent", no_wrap=True)
        table.add_column("path", overflow="fold")
        table.add_column("chars", style="muted", justify="right", no_wrap=True)
        for layer in layers:
            table.add_row(layer.scope, str(layer.path), f"{len(layer.content):,}")
        console.print()
        console.print(table)
        return

    if arg == "path":
        if not layers:
            ui.muted("no memory layers loaded")
            return
        for layer in layers:
            ui.muted(f"{layer.scope:8s} {layer.path}")
        return

    if arg == "show":
        if not layers:
            ui.muted("no memory layers loaded")
            return
        from rich.panel import Panel
        from rich.markdown import Markdown
        from agent.ui.console import console
        for layer in layers:
            console.print()
            console.print(Panel(
                Markdown(layer.content.strip()) if "#" in layer.content else
                    layer.content.strip() or "(empty)",
                title=f"[accent]{layer.scope}[/accent]  [muted]{layer.path}[/muted]",
                title_align="left",
                border_style="divider",
                padding=(0, 1),
                expand=False,
            ))
        return

    if arg == "edit":
        # Open the project-scope memory file. If none exists, create
        # an empty AGENTS.md in the project root so the editor has
        # something to open.
        target = None
        for layer in layers:
            if layer.scope == "project":
                target = layer.path
                break
        if target is None:
            target = project_root(".") / "AGENTS.md"
            try:
                target.touch(exist_ok=True)
            except OSError as e:
                ui.error(f"could not create {target}: {e}")
                return
        editor = _os.environ.get("EDITOR") or (
            "notepad" if _os.name == "nt" else "vi"
        )
        ui.muted(f"opening {target} in {editor}")
        try:
            _sp.run([editor, str(target)])
        except FileNotFoundError:
            ui.error(
                f"editor not found: {editor!r}. "
                f"Set the EDITOR environment variable to your preferred editor."
            )
        return

    ui.error(
        "usage: /memory [list|show|edit|path]   "
        "(default: list)"
    )


_HANDLERS["/memory"] = _cmd_memory


def _cmd_profile(args: str = "") -> None:
    """``/profile``           show available profiles + active one
    ``/profile show NAME``  print the resolved rules for a profile
    ``/profile set NAME``   switch the active profile (persists in session)
    """
    from agent.profiles import PROFILES, names as profile_names, get as get_profile
    from agent.session import get_session

    arg = (args or "").strip()
    parts = arg.split(None, 1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1].strip().lower() if len(parts) > 1 else ""
    s = get_session()

    if sub in ("", "list", "ls"):
        from rich.table import Table
        from agent.ui.console import console

        table = Table(
            title=f"[muted]permission profiles · active = "
                  f"[accent]{s.profile}[/accent][/muted]",
            title_justify="left",
            title_style="muted",
            show_header=True,
            header_style="muted",
            border_style="divider",
            expand=False,
        )
        table.add_column("name", style="accent", no_wrap=True)
        table.add_column("default", no_wrap=True)
        table.add_column("severity", no_wrap=True)
        table.add_column("description", overflow="fold")
        sev_style = {"info": "muted", "warn": "log.warn", "danger": "log.error"}
        for n in profile_names():
            p = PROFILES[n]
            marker = " *" if n == s.profile else ""
            style = sev_style.get(p.severity, "muted")
            table.add_row(
                f"{n}{marker}",
                p.default,
                f"[{style}]{p.severity}[/{style}]",
                p.description,
            )
        console.print()
        console.print(table)
        return

    if sub == "show":
        if not rest:
            ui.error("usage: /profile show <name>")
            return
        if rest not in PROFILES:
            ui.error(f"unknown profile: {rest!r}. Try /profile list.")
            return
        p = PROFILES[rest]
        from rich.panel import Panel
        from rich.text import Text
        from agent.ui.console import console
        body = Text()
        body.append(f"default  ", style="muted")
        body.append(p.default + "\n", style="accent")
        body.append("\nallow:\n", style="log.success")
        for r in p.allow or ["(none)"]:
            body.append(f"  {r}\n", style="muted")
        body.append("\nask:\n", style="log.warn")
        for r in p.ask or ["(none)"]:
            body.append(f"  {r}\n", style="muted")
        body.append("\ndeny:\n", style="log.error")
        for r in p.deny or ["(none)"]:
            body.append(f"  {r}\n", style="muted")
        console.print()
        console.print(Panel(
            body,
            title=f"[accent]profile · {p.name}[/accent]  "
                  f"[muted]{p.description}[/muted]",
            title_align="left",
            border_style="divider",
            padding=(0, 1),
            expand=False,
        ))
        return

    if sub == "set":
        if not rest:
            ui.error("usage: /profile set <" + "|".join(profile_names()) + ">")
            return
        if rest not in PROFILES:
            ui.error(f"unknown profile: {rest!r}. Try /profile list.")
            return
        prev = s.profile
        s.profile = rest
        # Clear remembered "always allow" decisions when tightening the
        # profile so the user doesn't carry over too-broad permissions.
        if PROFILES[rest].default == "deny" or rest in ("readonly", "safe"):
            s.remembered_permissions.clear()
        try:
            from agent.persistence import session_store
            store = session_store()
            store.update_meta(profile=rest)
            store.write_meta_marker()
        except Exception:
            pass
        sev = get_profile(rest).severity
        style = {"info": "log.success", "warn": "log.warn", "danger": "log.error"}.get(sev, "muted")
        ui.success(f"profile: {prev} → {rest}")
        if sev != "info":
            from agent.ui.console import console
            console.print(
                f"[{style}]heads-up: {rest} is a "
                f"{'high-autonomy' if sev == 'warn' else 'maximum-autonomy'} "
                f"profile. The agent will auto-execute more without "
                f"asking. /profile set default to revert.[/{style}]"
            )
        return

    ui.error("usage: /profile [list | show NAME | set NAME]")


_HANDLERS["/profile"] = _cmd_profile


def _cmd_config(args: str = "") -> None:
    """``/config``   open the interactive config editor (arrow keys + Enter)"""
    try:
        from kryth.config import open_config_tui, VALID_KEYS
    except ImportError:
        ui.error("KRYTH config module not found - reinstall KRYTH")
        return

    parts = (args or "").strip().split(None, 1)
    focus = parts[0].lower() if parts and parts[0].lower() in VALID_KEYS else None
    open_config_tui(focus_key=focus)


_HANDLERS["/config"] = _cmd_config


def _cmd_bridge(args: str = "") -> None:
    """Local provider bridge — use browser sessions instead of API keys.

    ``/bridge start [gemini|claude|openai] [--port N]``
    ``/bridge stop``
    ``/bridge status``
    ``/bridge auth <provider>``   re-run browser login
    ``/bridge sessions``          list saved sessions
    """
    from agent.ui.console import console
    from rich.table import Table

    parts = (args or "").strip().split()
    sub = parts[0].lower() if parts else "status"

    # ---- /bridge start ------------------------------------------------
    if sub == "start":
        provider = "gemini"
        port = 8765
        for i, p in enumerate(parts[1:], 1):
            if p in ("gemini", "claude", "openai"):
                provider = p
            elif p == "--port" and i + 1 < len(parts):
                try:
                    port = int(parts[i + 1])
                except ValueError:
                    pass

        try:
            from agent.bridge import start, is_running
        except ImportError:
            ui.error("Bridge requires: pip install fastapi uvicorn playwright")
            ui.muted("Then run: playwright install chromium")
            return

        if is_running():
            ui.muted("bridge is already running — use /bridge status")
            return

        ui.muted(f"starting bridge: provider={provider} port={port}")
        try:
            start(port=port, provider=provider)
            ui.success(f"bridge running on http://localhost:{port}/v1")
            ui.muted(f"provider: {provider}  ·  model routing → bridge")
        except Exception as e:
            ui.error(f"bridge failed to start: {e}")
        return

    # ---- /bridge stop -------------------------------------------------
    if sub == "stop":
        try:
            from agent.bridge import stop, is_running
            if not is_running():
                ui.muted("bridge is not running")
                return
            stop()
            ui.success("bridge stopped")
        except ImportError:
            ui.error("bridge module not available")
        return

    # ---- /bridge status -----------------------------------------------
    if sub == "status":
        try:
            from agent.bridge import status, is_running
            s = status()
        except ImportError:
            ui.muted("bridge not available (install fastapi uvicorn playwright)")
            return

        if s["running"]:
            ui.success(f"bridge running  ·  pid {s['pid']}  ·  {s['base_url']}")
        else:
            ui.muted("bridge not running  ·  use /bridge start [gemini|claude|openai]")
        return

    # ---- /bridge auth <provider> --------------------------------------
    if sub == "auth":
        provider = parts[1] if len(parts) > 1 else "gemini"
        ui.muted(f"re-authenticating {provider}...")
        try:
            import asyncio
            from agent.bridge.providers import get_provider
            from agent.bridge.session_store import clear_session
            clear_session(provider)
            ProviderClass = get_provider(provider)
            p = ProviderClass(headless=False)

            async def _auth():
                await p.setup()
                await p.authenticate()
                await p.teardown()

            asyncio.run(_auth())
            ui.success(f"{provider} session saved")
        except Exception as e:
            ui.error(f"auth failed: {e}")
        return

    # ---- /bridge sessions ---------------------------------------------
    if sub == "sessions":
        try:
            from agent.bridge.session_store import list_sessions
            sessions = list_sessions()
        except ImportError:
            ui.error("bridge module not available")
            return

        if not sessions:
            ui.muted("no saved sessions")
            return

        table = Table(
            title="[muted]bridge sessions[/muted]",
            title_justify="left",
            title_style="muted",
            show_header=True,
            header_style="muted",
            border_style="divider",
            expand=False,
        )
        table.add_column("provider", style="accent", no_wrap=True)
        table.add_column("status", no_wrap=True)
        table.add_column("last used", style="muted", no_wrap=True)

        import time as _time
        for s in sessions:
            status_str = "[log.success]✓ active[/log.success]" if s["authenticated"] \
                         else "[muted]needs login[/muted]"
            last = s.get("last_used")
            if last:
                age = _time.time() - last
                if age < 3600:
                    age_str = f"{int(age // 60)}m ago"
                elif age < 86400:
                    age_str = f"{int(age // 3600)}h ago"
                else:
                    age_str = f"{int(age // 86400)}d ago"
            else:
                age_str = "never"
            table.add_row(s["provider"], status_str, age_str)

        console.print()
        console.print(table)
        console.print()
        return

    ui.error(
        f"unknown subcommand '{sub}'.\n"
        "usage: /bridge [start [provider] | stop | status | auth <provider> | sessions]"
    )


_HANDLERS["/bridge"] = _cmd_bridge


def handle_repl_command(line: str) -> bool:
    parts = line.split(None, 1)
    cmd = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    handler = _HANDLERS.get(cmd)
    if handler is None:
        return False
    handler(rest)
    return True


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _maybe_offer_resume() -> bool:
    """Surface the most recent session for this project if it's young
    enough to be worth resuming. Returns True when a session was loaded
    so the caller can skip starting a fresh persisted log."""
    import sys as _sys
    import time as _time
    from agent.env import getenv_bool

    if getenv_bool("AICODER_NO_RESUME"):
        return False

    # Non-interactive stdin → can't prompt → skip.
    if not _sys.stdin.isatty():
        return False

    try:
        from agent.persistence import list_recent, load_session
    except Exception:
        return False

    metas = list_recent(limit=1)
    if not metas:
        return False
    meta = metas[0]

    # Only offer if the session is recent (24h) and substantive (>=2
    # messages so the resume actually buys something).
    age = _time.time() - meta.last_updated
    if age > 86400 or meta.message_count < 2:
        return False

    age_label = _short_age(age)
    preview = meta.first_user_preview or "(no preview)"
    ui.muted(
        f"recent session {meta.session_id[:8]} · {age_label} · "
        f"{meta.message_count} msgs · {preview[:80]}"
    )
    try:
        answer = input("resume it? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if answer not in ("y", "yes"):
        return False

    loaded = load_session(meta.session_id)
    if loaded is None:
        ui.error(f"could not load {meta.session_id}")
        return False

    from agent.session import get_session
    from agent.persistence import session_store

    meta2, messages = loaded
    s = get_session()
    s.messages = list(messages)
    s.cumulative_in_tokens = meta2.cumulative_in_tokens
    s.cumulative_out_tokens = meta2.cumulative_out_tokens
    s.mode = meta2.mode
    s.profile = meta2.profile or "default"
    s.ensure_system()
    session_store().attach(meta2.session_id, meta2.project_hash)
    ui.success(
        f"resumed session {meta2.session_id[:8]}  "
        f"({meta2.message_count} messages · profile={s.profile})"
    )
    return True


def main() -> None:
    ui.install()

    # Banner + first-time hint.
    from agent.llm import BASE_URL, MAIN_MODEL
    ui.banner(model=MAIN_MODEL, base_url=BASE_URL, skill_count=len(list_skills()))

    # Pick initial profile from AICODER_PROFILE (or legacy
    # AICODER_ASSUME_YES -> yolo). Set BEFORE offering /resume so the
    # operator's choice is the starting point if no session is loaded.
    try:
        from agent.profiles import from_environment, get as _get_profile
        initial = from_environment()
        s = get_session()
        s.profile = initial
        if initial != "default":
            sev = _get_profile(initial).severity
            style = {"info": "muted", "warn": "log.warn",
                     "danger": "log.error"}[sev]
            from agent.ui.console import console
            console.print(
                f"[{style}]starting in profile = "
                f"[bold]{initial}[/bold] (from environment)[/{style}]"
            )
    except Exception:
        pass

    # Offer to resume a recent session before opening a fresh log so we
    # don't litter the sessions directory with empty starts. /resume
    # also reinstates the previously-saved profile.
    resumed = _maybe_offer_resume()

    if not resumed:
        try:
            from agent.persistence import session_store
            session_store().start_new(".")
            # Carry the current profile into the new session's metadata.
            session_store().update_meta(profile=get_session().profile)
            session_store().write_meta_marker()
        except Exception:
            pass

    ui.muted(
        "type a request, or use a slash command (/help for the full list)"
    )

    prompt_ui = PromptUI(
        slash_names=_slash_names,
        toolbar_data=_toolbar_data,
    )

    while True:
        try:
            user_input = prompt_ui.read()
        except KeyboardInterrupt:
            # Empty Ctrl+C — return to a fresh prompt without exiting.
            continue
        except EOFError:
            from agent.ui.components import goodbye
            goodbye()
            try:
                from agent.persistence import session_store
                session_store().flush()
                session_store().close()
            except Exception:
                pass
            break

        cmd = user_input.strip()
        if not cmd:
            continue
        if cmd.lower() == "exit":
            from agent.ui.components import goodbye
            goodbye()
            try:
                from agent.persistence import session_store
                session_store().flush()
                session_store().close()
            except Exception:
                pass
            break

        first = cmd.split(None, 1)[0]
        if first in REPL_COMMANDS:
            if handle_repl_command(cmd):
                continue

        skill_name, skill_text, rest = parse_slash(cmd)
        if skill_name:
            ui.muted(f"invoking skill /{skill_name}")
            extra = f"[Skill: {skill_name}]\n{skill_text}"
            prompt_text = rest if rest else f"Run the /{skill_name} skill."
            try:
                run_agent(prompt_text, extra_system=extra)
            except KeyboardInterrupt:
                ui.turn_interrupted()
            continue

        try:
            run_agent(cmd)
        except KeyboardInterrupt:
            ui.turn_interrupted()


if __name__ == "__main__":
    main()

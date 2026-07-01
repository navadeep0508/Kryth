"""KRYTH CLI config — single model.

Storage: ~/.kryth/config.json  (mode 0600)

Keys
----
  api_key  -> API_KEY    (required)
  model    -> MODEL      (required)
  base_url -> BASE_URL   (required)
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

CONFIG_DIR  = Path.home() / ".kryth"
CONFIG_FILE = CONFIG_DIR / "config.json"

KEY_TO_ENV: dict[str, str] = {
    "api_key":  "API_KEY",
    "model":    "MODEL",
    "base_url": "BASE_URL",
}

VALID_KEYS = list(KEY_TO_ENV)

DEFAULTS: dict[str, str] = {
    "api_key":  "",
    "model":    "",
    "base_url": "",
}

KEY_LABELS: dict[str, str] = {
    "api_key":  "API key",
    "model":    "Model",
    "base_url": "Base URL",
}

SENSITIVE = {"api_key"}

LEGACY_ENV: dict[str, str] = {
    "MODEL": "KRYTH_MAIN_MODEL",
}


def _load() -> dict[str, str]:
    stored: dict[str, str] = {}
    for candidate in (CONFIG_FILE,):
        if candidate.exists():
            try:
                stored = json.loads(candidate.read_text(encoding="utf-8"))
                break
            except Exception:
                pass
    result = {k: stored.get(k, DEFAULTS[k]) for k in DEFAULTS}
    return result


def _save(cfg: dict[str, str]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:
        CONFIG_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass


def _mask(value: str, key: str) -> str:
    if key in SENSITIVE and len(value) > 8:
        return value[:6] + "..." + value[-4:]
    return value or "(not set)"


def apply_to_env(cfg: dict[str, str] | None = None) -> None:
    if cfg is None:
        cfg = _load()
    for key, env_var in KEY_TO_ENV.items():
        value = cfg.get(key, "").strip()
        if value and not os.environ.get(env_var, "").strip():
            os.environ[env_var] = value

    # Map single config keys to all legacy env vars so existing code works
    api_key = cfg.get("api_key", "").strip()
    if api_key:
        for legacy in ("NVIDIA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"):
            if not os.environ.get(legacy, "").strip():
                os.environ[legacy] = api_key

    model = cfg.get("model", "").strip()
    if model:
        for legacy in ("KRYTH_MAIN_MODEL", "KRYTH_PLANNER_MODEL", "KRYTH_SUMMARIZER_MODEL", "KRYTH_VISION_MODEL"):
            if not os.environ.get(legacy, "").strip():
                os.environ[legacy] = model

    base_url = cfg.get("base_url", "").strip()
    if base_url and not os.environ.get("KRYTH_BASE_URL", "").strip():
        os.environ["KRYTH_BASE_URL"] = base_url


# ---------------------------------------------------------------------------
# Simple helpers used by _repl_main
# ---------------------------------------------------------------------------

def cmd_write_env(path: str = ".env") -> None:
    cfg = _load()
    env_path = Path(path)
    existing: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k, _, v = stripped.partition("=")
                existing[k.strip()] = v.strip()
    for key, env_var in KEY_TO_ENV.items():
        value = cfg.get(key, "")
        if value:
            existing[env_var] = value
    env_path.write_text(
        "\n".join(f"{k}={v}" for k, v in existing.items()) + "\n",
        encoding="utf-8",
    )


def cmd_reset() -> None:
    if CONFIG_FILE.exists():
        CONFIG_FILE.unlink()


# ---------------------------------------------------------------------------
# Interactive TUI
# ---------------------------------------------------------------------------

def _read_line(prompt: str, default: str, *, secret: bool = False) -> str:
    """Inline line editor using prompt_toolkit for a single field."""
    try:
        from prompt_toolkit import prompt as pt_prompt
        from prompt_toolkit.formatted_text import HTML

        if secret:
            from prompt_toolkit.filters import is_done
            result = pt_prompt(
                HTML(f"<ansiyellow>{prompt}</ansiyellow> "),
                default=default,
                is_password=True,
            )
        else:
            result = pt_prompt(
                HTML(f"<ansiyellow>{prompt}</ansiyellow> "),
                default=default,
            )
        return result.strip()
    except (KeyboardInterrupt, EOFError):
        return default


def open_config_tui(focus_key: str | None = None) -> None:
    """Full-screen arrow-key config editor.

    Navigation:
      →' / ↓"   move between fields
      Enter   edit the selected field inline
      s       save & exit
      q / ESC quit without saving
    """
    from rich.live import Live
    from rich.table import Table
    from rich.text import Text
    from rich.panel import Panel
    from agent.ui.console import console
    from agent.ui.keyread import read_key

    cfg = _load()
    keys = VALID_KEYS
    cursor = keys.index(focus_key) if focus_key and focus_key in keys else 0
    saved = False
    dirty = False

    def _render(cfg: dict, cursor: int, dirty: bool) -> Panel:
        table = Table(
            show_header=True,
            header_style="bold",
            border_style="bright_black",
            expand=True,
            show_edge=True,
            padding=(0, 1),
        )
        table.add_column("  ", width=2, no_wrap=True)
        table.add_column("Setting",   style="muted",   no_wrap=True, min_width=18)
        table.add_column("Value",     overflow="fold", min_width=30)
        table.add_column("Env var",   style="muted",   no_wrap=True)

        for i, key in enumerate(keys):
            env_var   = KEY_TO_ENV[key]
            legacy_var = LEGACY_ENV.get(env_var, "")
            raw_val   = cfg.get(key, "")
            live_raw  = os.environ.get(env_var, "") or (os.environ.get(legacy_var, "") if legacy_var else "")
            display   = _mask(raw_val, key) if raw_val else "[muted](not set)[/muted]"
            live_note = ""
            if live_raw and live_raw != raw_val:
                live_note = f"  [dim](env: {_mask(live_raw, key)})[/dim]"

            if i == cursor:
                arrow   = "[kryth.core]◈[/kryth.core]"
                label   = f"[kryth.core]{KEY_LABELS[key]}[/kryth.core]"
                val_str = f"[bold white]{display}[/bold white]{live_note}"
            else:
                arrow   = "  "
                label   = KEY_LABELS[key]
                val_str = f"{display}{live_note}"

            table.add_row(arrow, label, val_str, f"[dim]{env_var}[/dim]")

        dirty_tag = ("  ◈ unsaved", "accent") if dirty else ("", "")
        footer = Text.assemble(
            ("  ↑↓", "accent"), (" navigate   ", "muted"),
            ("Enter", "accent"), (" edit   ", "muted"),
            ("s", "accent"), (" save & exit   ", "muted"),
            ("q", "accent"), (" quit", "muted"),
            dirty_tag,
        )
        return Panel(
            table,
            title="[kryth.core]◈[/kryth.core] [title]KRYTH config[/title]",
            subtitle=footer,
            border_style="bright_black",
            padding=(0, 0),
        )

    # Use a Live region for the menu, pause it to read inline input
    with Live(
        _render(cfg, cursor, dirty),
        console=console,
        refresh_per_second=20,
        transient=True,
    ) as live:
        while True:
            live.update(_render(cfg, cursor, dirty))
            key = read_key()

            if key == "UP":
                cursor = (cursor - 1) % len(keys)

            elif key == "DOWN":
                cursor = (cursor + 1) % len(keys)

            elif key == "ENTER":
                field_key = keys[cursor]
                current   = cfg.get(field_key, "")
                secret    = field_key in SENSITIVE
                label     = KEY_LABELS[field_key]

                # Pause Live so prompt_toolkit can own the terminal
                live.stop()
                console.print()
                new_val = _read_line(
                    f"  {label}:",
                    "" if secret else current,
                    secret=secret,
                )
                # Empty input on a secret field = keep existing
                if secret and not new_val:
                    new_val = current
                if new_val != current:
                    cfg[field_key] = new_val
                    dirty = True
                live.start()

            elif key in ("s", "S"):
                _save(cfg)
                # Hot-apply to current process
                apply_to_env(cfg)
                saved = True
                break

            elif key in ("q", "Q", "ESC", "CTRL_C"):
                break

    if saved:
        console.print("[log.success]  ◈ config saved[/log.success]")
        console.print(f"[muted]  {CONFIG_FILE}[/muted]")
    else:
        console.print("[muted]  ◇ config unchanged[/muted]")


# ---------------------------------------------------------------------------
# API-error prompt helper  (called from llm.py after auth/404 errors)
# ---------------------------------------------------------------------------

_ERROR_KEY_MAP: dict[str, str] = {
    "AuthenticationError": "api_key",
    "PermissionDeniedError": "api_key",
    "NotFoundError": "model",
}


def prompt_config_fix(error_type: str) -> None:
    """Called after an API error. Asks the user if they want to fix
    the relevant config field right now, then opens the TUI focused
    on that field.

    ``error_type`` is the class name of the OpenAI exception.
    """
    from agent.ui.console import console

    focus = _ERROR_KEY_MAP.get(error_type)

    messages = {
        "AuthenticationError": (
            "[bold red]  401 - API key rejected.[/bold red]\n"
            "  The key stored in config (or API_KEY / NVIDIA_API_KEY) was not accepted.\n"
        ),
        "PermissionDeniedError": (
            "[bold red]  403 - Permission denied.[/bold red]\n"
            "  Your key doesn't have access to this model or your quota is exhausted.\n"
        ),
        "NotFoundError": (
            "[bold red]  404 - Model not found.[/bold red]\n"
            "  Check the model name in /config, or leave blank to use the default.\n"
        ),
    }

    msg = messages.get(error_type, "[bold red]  API error.[/bold red]\n")
    console.print()
    console.print(msg)

    # Non-interactive sessions (piped stdin, redirected stdout, CI, harnesses)
    # must NEVER block on input() — surface guidance and return immediately.
    import sys as _sys
    if not _sys.stdin or not _sys.stdin.isatty():
        console.print()
        console.print("  [muted]Set a valid key with /config (non-interactive session).[/muted]")
        return

    field_label = KEY_LABELS.get(focus, "config") if focus else "config"
    console.print(
        f"  Open config to fix [kryth.core]{field_label}[/kryth.core]? "
        f"[muted]\\[Y/n][/muted] ",
        end="",
    )

    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print()
        return

    if answer in ("", "y", "yes"):
        open_config_tui(focus_key=focus)


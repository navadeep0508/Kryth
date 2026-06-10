"""KRYTH CLI config â€” interactive TUI + read/write for model, base_url, api_key.

Storage: ``~/.ai-coder/config.json``  (mode 0600)

Keys
----
  model            -> AICODER_MAIN_MODEL
  planner_model    -> AICODER_PLANNER_MODEL
  summarizer_model -> AICODER_SUMMARIZER_MODEL
  base_url         -> AICODER_BASE_URL
  api_key          â†’ OPENAI_API_KEY
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

CONFIG_DIR  = Path.home() / ".ai-coder"
CONFIG_FILE = CONFIG_DIR / "config.json"
LEGACY_CONFIG_FILE = Path.home() / ".kryth_cli" / "config.json"

KEY_TO_ENV: dict[str, str] = {
    "model":             "AICODER_MAIN_MODEL",
    "planner_model":     "AICODER_PLANNER_MODEL",
    "summarizer_model":  "AICODER_SUMMARIZER_MODEL",
    "base_url":          "AICODER_BASE_URL",
    "api_key":           "OPENAI_API_KEY",
    "nvidia_api_key":    "NVIDIA_API_KEY",
    # New role-specific models
    "vision_model":      "KRYTH_VISION_MODEL",
    "extraction_model":  "KRYTH_EXTRACTION_MODEL",
    "reasoning_model":   "KRYTH_REASONING_MODEL",
    # Provider keys
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "anthropic_api_key":  "ANTHROPIC_API_KEY",
    "google_api_key":     "GOOGLE_API_KEY",
}

LEGACY_ENV: dict[str, str] = {
    "AICODER_MAIN_MODEL":      "KRYTH_MAIN_MODEL",
    "AICODER_PLANNER_MODEL":   "KRYTH_PLANNER_MODEL",
    "AICODER_SUMMARIZER_MODEL": "KRYTH_SUMMARIZER_MODEL",
    "AICODER_BASE_URL":        "KRYTH_BASE_URL",
}

LEGACY_ENV_2: dict[str, str] = {
    "AICODER_MAIN_MODEL":      "KRYTH_CLI_MAIN_MODEL",
    "AICODER_PLANNER_MODEL":   "KRYTH_CLI_PLANNER_MODEL",
    "AICODER_SUMMARIZER_MODEL": "KRYTH_CLI_SUMMARIZER_MODEL",
    "AICODER_BASE_URL":        "KRYTH_CLI_BASE_URL",
}

VALID_KEYS = list(KEY_TO_ENV)   # ordered

DEFAULTS: dict[str, str] = {
    "model":             "gpt-4o-mini",
    "planner_model":     "gpt-4o-mini",
    "summarizer_model":  "gpt-4o-mini",
    "base_url":          "https://api.openai.com/v1",
    "api_key":           "",
    "nvidia_api_key":    "",
    "vision_model":      "",
    "extraction_model":  "",
    "reasoning_model":   "",
    "openrouter_api_key": "",
    "anthropic_api_key":  "",
    "google_api_key":     "",
}

# Human-readable labels shown in the TUI
KEY_LABELS: dict[str, str] = {
    "model":             "Main model",
    "planner_model":     "Planner model",
    "summarizer_model":  "Summarizer model",
    "base_url":          "Base URL",
    "api_key":           "API key (OpenAI)",
    "nvidia_api_key":    "NVIDIA API key (vision)",
    "vision_model":      "Vision model",
    "extraction_model":  "Extraction model",
    "reasoning_model":   "Reasoning model",
    "openrouter_api_key": "OpenRouter API key",
    "anthropic_api_key":  "Anthropic API key",
    "google_api_key":     "Google API key",
}

# Which keys are sensitive (masked in display)
SENSITIVE = {
    "api_key", "nvidia_api_key", "openrouter_api_key",
    "anthropic_api_key", "google_api_key",
}


def _load() -> dict[str, str]:
    stored: dict[str, str] = {}
    source = CONFIG_FILE if CONFIG_FILE.exists() else LEGACY_CONFIG_FILE
    if source.exists():
        try:
            stored = json.loads(source.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {k: stored.get(k, DEFAULTS[k]) for k in DEFAULTS}


def _save(cfg: dict[str, str]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:
        CONFIG_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass


def _mask(value: str, key: str) -> str:
    if key in SENSITIVE and len(value) > 8:
        return value[:6] + "â€¦" + value[-4:]
    return value or "(not set)"


def apply_to_env(cfg: dict[str, str] | None = None) -> None:
    """Inject stored config into os.environ (env / .env wins)."""
    if cfg is None:
        cfg = _load()
    for key, env_var in KEY_TO_ENV.items():
        value = cfg.get(key, "")
        legacy = LEGACY_ENV.get(env_var)
        legacy2 = LEGACY_ENV_2.get(env_var)
        live = os.environ.get(env_var) or (os.environ.get(legacy, "") if legacy else "") or (os.environ.get(legacy2, "") if legacy2 else "")
        if value and not live:
            os.environ[env_var] = value
            if legacy:
                os.environ[legacy] = value
            if legacy2:
                os.environ[legacy2] = value
        elif live:
            if not os.environ.get(env_var):
                os.environ[env_var] = live
            if legacy and not os.environ.get(legacy):
                os.environ[legacy] = live
            if legacy2 and not os.environ.get(legacy2):
                os.environ[legacy2] = live


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
      â†‘ / â†“   move between fields
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
            border_style="divider",
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
            border_style="divider",
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
                for k, env_var in KEY_TO_ENV.items():
                    v = cfg.get(k, "")
                    if v:
                        os.environ[env_var] = v
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

# Maps error type names â†’ which config key to focus
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
            "[bold red]  401 â€” API key rejected.[/bold red]\n"
            "  The key stored in config (or OPENAI_API_KEY) was not accepted.\n"
        ),
        "PermissionDeniedError": (
            "[bold red]  403 â€” Permission denied.[/bold red]\n"
            "  Your key doesn't have access to this model or your quota is exhausted.\n"
        ),
        "NotFoundError": (
            "[bold red]  404 - Endpoint or model not found.[/bold red]\n"
            "  First confirm the model name is available for this key.\n"
            "  If the model is right, check that the base URL includes the provider's OpenAI-compatible path, usually /v1.\n"
        ),
    }

    msg = messages.get(error_type, "[bold red]  API error.[/bold red]\n")
    console.print()
    console.print(msg)

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


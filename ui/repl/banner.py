from __future__ import annotations

import time

from rich.align import Align
from rich.console import Console
from rich.console import Group
from config.config import Config
from ui.components.logo import logo_lines
from ui.components.welcome import welcome_parts
from ui.motion import animations_enabled


def render_banner(
    console: Console,
    config: Config,
    session_id: str | None = None,
    git_branch: str | None = None,
    fresh: bool = False,
    last_task: str | None = None,
) -> None:
    """Render KRYTH's opening composition without clearing terminal history."""
    supports_unicode = "utf" in (console.encoding or "").lower()
    parts = welcome_parts(
        model=config.model_name or None,
        workspace=config.cwd.name or str(config.cwd),
        session_id=session_id,
        branch=git_branch,
        width=console.width,
        height=console.height,
        unicode=supports_unicode,
        fresh=fresh,
        last_task=last_task,
    )

    if _should_reveal_banner(console, config, fresh):
        for row in logo_lines(unicode=supports_unicode):
            console.print(Align.center(row, width=console.width))
            time.sleep(0.012)
        console.print(Group(*parts[1:]))
    else:
        console.print(Group(*parts))


def _should_reveal_banner(console: Console, config: Config, fresh: bool) -> bool:
    return (
        animations_enabled(config)
        and console.is_terminal
        and fresh
        and console.width >= 54
        and console.height >= 23
    )

from __future__ import annotations

from rich.console import Console
from config.config import Config
from ui.components.welcome import welcome_parts


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
        workspace=str(config.cwd),
        provider=config.providers.active_name,
        session_id=session_id,
        branch=git_branch,
        width=console.width,
        height=console.height,
        unicode=supports_unicode,
        fresh=fresh,
        last_task=last_task,
    )

    console.print(*parts, sep="\n")

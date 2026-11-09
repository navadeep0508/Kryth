from __future__ import annotations

from typing import Any, Callable

from rich.console import Console

from agent.store import Checkpoint, SessionMeta
from config.config import Config


def pick(items: list, token: str, identity: Callable[[Any], str]) -> Any | None:
    """Resolve a `1`-based list position or an id prefix to one item."""

    token = token.strip()
    if not token:
        return None

    if token.isdigit():
        index = int(token) - 1
        return items[index] if 0 <= index < len(items) else None

    return next((item for item in items if identity(item).startswith(token)), None)


class CommandGroup:
    """State every command group shares: the config, the console, the listings.

    Groups are mixed together into `SlashCommands`, so they all see the same
    instance and the numbers printed by one command stay valid arguments for
    the next.
    """

    def __init__(self, config: Config, console: Console) -> None:
        self.config = config
        self.console = console

        # Whatever the last /sessions and /checkpoints printed, so the numbers
        # in those listings can be used as arguments.
        self._listed_sessions: list[SessionMeta] = []
        self._listed_checkpoints: list[Checkpoint] = []

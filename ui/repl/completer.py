from __future__ import annotations

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.completion.base import CompleteEvent
from prompt_toolkit.document import Document


class SlashCompleter(Completer):
    """Complete slash commands while typing in the REPL.

    Anything after a leading ``/`` is matched against the registered command
    names. The completion menu opens as soon as a ``/`` is typed and narrows
    as the user keeps typing, so commands are discoverable without /help.
    """

    def __init__(self, commands) -> None:
        self._commands = commands

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ):
        text = document.text_before_cursor

        # Only complete a command in the first token, and only while the
        # cursor is still on it (i.e. no arguments typed yet).
        if not text.startswith("/") or " " in text or "\n" in text:
            return

        prefix = text[1:]
        for name in self._commands.command_names():
            if name.startswith(prefix):
                yield Completion(
                    name,
                    start_position=-len(prefix),
                    display=f"/{name}",
                    display_meta=self._commands.describe(name),
                )

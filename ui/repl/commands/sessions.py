from __future__ import annotations

from rich.table import Table
from rich.text import Text

from agent.agent import Agent
from client.response import TokenUsage
from ui.components import render_transcript
from ui.format import format_ago
from ui.repl.commands.base import CommandGroup, pick


class SessionCommands(CommandGroup):

    def clear_history(self, agent: Agent, args: list[str]) -> None:
        session = agent.session

        # The conversation being cleared stays on disk under its own id, so
        # clearing is recoverable with /resume.
        if session.turns:
            session.save_checkpoint()

        saved = session.store.exists(session.session_id)
        previous = session.short_id

        session.reset()
        session.context_manager.set_latest_usage(TokenUsage())
        self._listed_checkpoints = []

        self.console.clear()
        self.console.print(Text("Conversation history cleared.", style="muted"))
        if saved:
            self.console.print(
                Text(f"The previous conversation is saved as {previous}.", style="muted")
            )

    def list_sessions(self, agent: Agent, args: list[str]) -> None:
        session = agent.session

        if args and args[0].lower() in {"rm", "delete", "remove"}:
            self._delete_session(agent, args[1:])
            return

        # Sessions are scoped to the directory they ran in; `all` widens that.
        every = bool(args) and args[0].lower() == "all"
        sessions = session.store.list(None if every else self.config.cwd)

        if not sessions:
            scope = "" if every else " for this directory"
            self.console.print(Text(f"No saved sessions{scope}.", style="muted"))
            if not every:
                self.console.print(
                    Text("Try /sessions all to look in every directory.", style="muted")
                )
            return

        self._listed_sessions = sessions

        table = Table.grid(padding=(0, 2))
        table.add_column(style="muted", justify="right")
        table.add_column(style="subtitle")
        table.add_column(style="muted")
        table.add_column(style="muted", justify="right")
        table.add_column()

        for index, meta in enumerate(sessions, start=1):
            current = meta.session_id == session.session_id
            table.add_row(
                str(index),
                meta.short_id,
                format_ago(meta.updated_at),
                f"{meta.turns} turn{'s' if meta.turns != 1 else ''}",
                Text(
                    meta.title + (" (current)" if current else ""),
                    style="highlight" if current else "text",
                ),
            )

        header = "Saved sessions" if every else "Saved sessions here"
        self.console.print(Text(f"{header} ({len(sessions)})", style="highlight"))
        self.console.print(table)
        self.console.print(Text("Usage: /resume (scroll ↑↓, Enter to pick) or /resume <number|id>", style="muted"))

    def _interactive_pick(
        self,
        items: list,
        title: str,
        format_item,
    ):
        """Interactive selection with ↑↓ arrow keys instead of typing a number.

        Returns the selected item, or None if cancelled.
        """
        if not items:
            return None

        from prompt_toolkit.input import create_input
        from prompt_toolkit.keys import Keys

        idx = 0

        def render() -> None:
            self.console.print()
            self.console.print(f"{title}", style="highlight")
            for i, item in enumerate(items):
                mark = "  > " if i == idx else "    "
                self.console.print(f"{mark}{format_item(item)}")
            self.console.print(
                "↑↓ scroll · Enter select · Ctrl+C cancel", style="muted"
            )

        render()

        try:
            device = create_input()
        except Exception:
            return None

        try:
            with device.raw_mode():
                while True:
                    try:
                        key_presses = device.read_keys()
                    except (OSError, EOFError):
                        return None

                    for kp in key_presses:
                        key = kp.key
                        if key == Keys.Up:
                            idx = max(0, idx - 1)
                            render()
                        elif key == Keys.Down:
                            idx = min(len(items) - 1, idx + 1)
                            render()
                        elif key == Keys.Enter:
                            return items[idx]
                        elif key == Keys.ControlC:
                            return None
        except Exception:
            return None

        return None

    def _delete_session(self, agent: Agent, args: list[str]) -> None:
        session = agent.session
        candidates = self._listed_sessions or session.store.list(self.config.cwd)

        if not args:
            if not candidates:
                self.console.print(Text("No saved sessions to delete.", style="muted"))
                return
            self.console.print(Text("Select a session to delete:", style="muted"))
            target = self._interactive_pick(
                candidates,
                "Delete session",
                lambda meta: f"{meta.short_id}  ·  {format_ago(meta.updated_at)}  ·  {meta.turns} turn{'s' if meta.turns != 1 else ''}",
            )
        else:
            target = pick(candidates, args[0], lambda meta: meta.session_id)

        if target is None:
            if args:
                self.console.print(f"[error]No such session: {args[0]}[/error]")
            return

        if target.session_id == session.session_id:
            self.console.print(
                "[error]That is the session you are in, it cannot be deleted.[/error]"
            )
            return

        if session.store.delete(target.session_id):
            self._listed_sessions = []
            self.console.print(
                f"[success]Deleted session {target.short_id}[/success] - {target.title}"
            )
        else:
            self.console.print(f"[error]Could not delete {target.short_id}[/error]")

    def resume_session(self, agent: Agent, args: list[str]) -> None:
        session = agent.session

        if not args:
            self.list_sessions(agent, [])
            candidates = self._listed_sessions or session.store.list(self.config.cwd)
            if not candidates:
                return
            target = self._interactive_pick(
                candidates,
                "Select a session to resume",
                lambda meta: (
                    f"{meta.short_id}  ·  {format_ago(meta.updated_at)}  ·  "
                    f"{meta.turns} turn{'s' if meta.turns != 1 else ''}  ·  {meta.title}"
                ),
            )
            if target is None:
                return
        else:
            candidates = self._listed_sessions or session.store.list(self.config.cwd)
            target = pick(candidates, args[0], lambda meta: meta.session_id)

            if target is None:
                # Not in the listing: it may still be an id from another directory.
                resolved = session.store.resolve(args[0], self.config.cwd)
                target = session.store.read_meta(resolved) if resolved else None

            if target is None:
                self.console.print(f"[error]No such session: {args[0]}[/error]")
                self.console.print(Text("Type /sessions to see what is saved.", style="muted"))
                return

        if target.session_id == session.session_id:
            self.console.print(Text("Already in that session.", style="muted"))
            return

        # Do not strand the conversation that is being replaced. A session
        # that never took a turn has nothing worth keeping.
        if session.turns:
            session.save_checkpoint()

        record = session.resume(target.session_id)
        if record is None:
            self.console.print(f"[error]Could not read session {target.short_id}[/error]")
            return

        self._listed_checkpoints = []
        self.announce_resume(agent)

    def announce_resume(self, agent: Agent) -> None:
        session = agent.session
        meta = session.resumed_from.meta if session.resumed_from else session.meta()

        line = Text.assemble(
            ("Resumed ", "success"),
            (meta.short_id, "subtitle"),
            (f" - {meta.title}", "muted"),
        )
        self.console.print()
        self.console.print(line)
        turns = session.turns
        self.console.print(
            Text(
                f"{session.context_manager.message_count} messages · "
                f"{turns} turn{'s' if turns != 1 else ''} · "
                f"saved {format_ago(meta.updated_at)}",
                style="muted",
            )
        )

        if meta.model and meta.model != self.config.model_name:
            self.console.print(
                Text(
                    f"Saved with {meta.model}, continuing with {self.config.model_name}.",
                    style="warning",
                )
            )

        render_transcript(self.console, session.context_manager.history)

    def save_checkpoint(self, agent: Agent, args: list[str]) -> None:
        session = agent.session

        if not self.config.session.enabled:
            self.console.print(
                "[error]Session saving is disabled in your config.[/error]"
            )
            return

        if session.context_manager.message_count == 0:
            self.console.print(Text("Nothing to save yet.", style="muted"))
            return

        label = " ".join(args).strip() or None
        checkpoint = session.save_checkpoint(label=label, auto=False)

        if checkpoint is None:
            self.console.print("[error]Could not write the checkpoint.[/error]")
            return

        self._listed_checkpoints = []
        self.console.print(
            Text.assemble(
                ("Saved ", "success"),
                (checkpoint.label, "subtitle"),
                (f" · {checkpoint.message_count} messages · session ", "muted"),
                (session.short_id, "subtitle"),
            )
        )

    def list_checkpoints(self, agent: Agent, args: list[str]) -> None:
        session = agent.session
        checkpoints = session.checkpoints()

        if not checkpoints:
            self.console.print(Text("No checkpoints in this session yet.", style="muted"))
            self.console.print(Text("Use /checkpoint to save one now.", style="muted"))
            return

        self._listed_checkpoints = checkpoints

        table = Table.grid(padding=(0, 2))
        table.add_column(style="muted", justify="right")
        table.add_column(style="subtitle")
        table.add_column(style="muted")
        table.add_column(style="muted", justify="right")
        table.add_column(style="muted")

        for index, checkpoint in enumerate(checkpoints, start=1):
            table.add_row(
                str(index),
                checkpoint.id,
                checkpoint.label,
                f"{checkpoint.message_count} msgs",
                format_ago(checkpoint.created_at),
            )

        self.console.print(
            Text(f"Checkpoints in {session.short_id} ({len(checkpoints)})", style="highlight")
        )
        self.console.print(table)
        self.console.print(Text("Usage: /rewind (scroll ↑↓, Enter to pick) or /rewind <number|id>", style="muted"))

    def rewind(self, agent: Agent, args: list[str]) -> None:
        session = agent.session

        if not args:
            self.list_checkpoints(agent, [])
            checkpoints = self._listed_checkpoints or session.checkpoints()
            if not checkpoints:
                return
            target = self._interactive_pick(
                checkpoints,
                "Select a checkpoint to rewind to",
                lambda checkpoint: (
                    f"{checkpoint.id}  ·  {checkpoint.label}  ·  "
                    f"{checkpoint.message_count} msgs  ·  {format_ago(checkpoint.created_at)}"
                ),
            )
            if target is None:
                return
        else:
            checkpoints = self._listed_checkpoints or session.checkpoints()
            target = pick(checkpoints, args[0], lambda checkpoint: checkpoint.id)

            if target is None:
                self.console.print(f"[error]No such checkpoint: {args[0]}[/error]")
                self.console.print(Text("Type /checkpoints to see them.", style="muted"))
                return

        record = session.store.load(session.session_id, target.id)
        if record is None:
            self.console.print(f"[error]Could not read checkpoint {target.id}[/error]")
            return

        session.restore(record)

        # Rewinding moves the session head, otherwise the next resume would
        # come back to the state we just rolled away from.
        session.save_checkpoint(label=f"rewound to {target.label}", auto=False)

        self._listed_checkpoints = []

        detail = (
            f" · {session.context_manager.message_count} messages"
            f" · turn {session.turns}"
        )

        self.console.print()
        self.console.print(
            Text.assemble(
                ("Rewound to ", "success"),
                (target.label, "subtitle"),
                (detail, "muted"),
            )
        )
        render_transcript(self.console, session.context_manager.history)

"""Prompt Toolkit input layer for KRYTH."""

from __future__ import annotations

from typing import Callable, Iterable

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.enums import EditingMode
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from agent.env import home_dir


_HISTORY_DIR = home_dir()
_HISTORY_FILE = _HISTORY_DIR / "history"


def _history() -> FileHistory:
    _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    return FileHistory(str(_HISTORY_FILE))


class _SlashCompleter(Completer):
    def __init__(self, names_provider: Callable[[], Iterable[str]]) -> None:
        self._provider = names_provider

    def get_completions(self, document: Document, complete_event):
        del complete_event
        text = document.text_before_cursor
        if "\n" in text:
            return
        head = text.lstrip()
        if not head.startswith("/") or " " in head:
            return
        prefix = head[1:]
        for name in self._provider():
            if name.startswith(prefix):
                yield Completion(
                    text=f"/{name}",
                    start_position=-len(head),
                    display=HTML(f"<b>/{name}</b>"),
                )


def _make_bindings() -> KeyBindings:
    kb = KeyBindings()

    @kb.add("enter")
    def _(event):
        buf = event.current_buffer
        if buf.text.rstrip(" ").endswith("\\"):
            buf.delete_before_cursor(1)
            buf.insert_text("\n")
            return
        buf.validate_and_handle()

    @kb.add("escape", "enter")
    def _(event):
        event.current_buffer.insert_text("\n")

    @kb.add("c-j")
    def _(event):
        event.current_buffer.insert_text("\n")

    @kb.add("c-l")
    def _(event):
        event.app.renderer.clear()

    return kb


_STYLE = Style.from_dict({
    "bottom-toolbar": "bg:#111111 #888888",
    "bottom-toolbar.label": "bg:#111111 #888888",
    "bottom-toolbar.model": "bg:#111111 #FFFFFF bold",
    "bottom-toolbar.mode": "bg:#111111 #E8FF3A",
    "bottom-toolbar.tokens": "bg:#111111 #888888",
    "bottom-toolbar.tip": "bg:#111111 #888888 italic",
    "bottom-toolbar.sep": "bg:#111111 #4B4B4B",
    "bottom-toolbar.profile-info": "bg:#111111 #4ADE80",
    "bottom-toolbar.profile-warn": "bg:#111111 #E8FF3A bold",
    "bottom-toolbar.profile-danger": "bg:#111111 #FF5A5A bold",
    "prompt": "ansiyellow bold",
    "prompt.ready": "ansiwhite",
    "continuation": "ansibrightblack",
})


def _prompt_text():
    return HTML("<prompt>◈</prompt><prompt.ready> Ready</prompt.ready>\n<prompt>◈</prompt> ")


def _continuation(width: int, line_number: int, wrap_count: int):
    del width, line_number, wrap_count
    return HTML("<continuation>·</continuation> ")


ToolbarData = Callable[[], dict]


def _profile_severity(name: str) -> str:
    try:
        from agent.profiles import get as _get
        return _get(name).severity
    except Exception:
        return "info"


class PromptUI:
    def __init__(
        self,
        *,
        slash_names: Callable[[], Iterable[str]],
        toolbar_data: ToolbarData,
    ) -> None:
        self._slash_names = slash_names
        self._toolbar_data = toolbar_data
        self._session: PromptSession | None = None
        self._fallback = False

    def _ensure_session(self) -> bool:
        if self._session is not None or self._fallback:
            return not self._fallback
        try:
            self._session = PromptSession(
                history=_history(),
                auto_suggest=AutoSuggestFromHistory(),
                completer=_SlashCompleter(self._slash_names),
                complete_while_typing=True,
                multiline=True,
                wrap_lines=True,
                mouse_support=False,
                editing_mode=EditingMode.EMACS,
                bottom_toolbar=self._render_toolbar,
                prompt_continuation=_continuation,
                key_bindings=_make_bindings(),
                style=_STYLE,
                include_default_pygments_style=False,
            )
            return True
        except Exception:
            self._fallback = True
            return False

    def _term_width(self) -> int:
        try:
            import shutil
            return shutil.get_terminal_size((80, 24)).columns
        except Exception:
            return 80

    def _render_toolbar(self):
        try:
            data = self._toolbar_data() or {}
        except Exception:
            return ""

        cols = self._term_width()
        model = data.get("model", "")
        mode = data.get("mode", "default")
        profile = data.get("profile", "default")
        tokens = data.get("tokens")
        depth = data.get("depth", 0)

        parts: list[str] = []
        if model:
            parts.append(
                "<bottom-toolbar.label> ◈ KRYTH </bottom-toolbar.label>"
                f"<bottom-toolbar.model> {model} </bottom-toolbar.model>"
            )
        parts.append(
            "<bottom-toolbar.label> mode </bottom-toolbar.label>"
            f"<bottom-toolbar.mode> {mode} </bottom-toolbar.mode>"
        )
        if profile and profile != "default":
            severity = _profile_severity(profile)
            cls = {
                "info": "bottom-toolbar.profile-info",
                "warn": "bottom-toolbar.profile-warn",
                "danger": "bottom-toolbar.profile-danger",
            }.get(severity, "bottom-toolbar.profile-info")
            parts.append(
                "<bottom-toolbar.label> profile </bottom-toolbar.label>"
                f"<{cls}> {profile} </{cls}>"
            )
        if tokens is not None and cols >= 90:
            parts.append(
                "<bottom-toolbar.label> tokens </bottom-toolbar.label>"
                f"<bottom-toolbar.tokens> {tokens:,} </bottom-toolbar.tokens>"
            )
        if depth and cols >= 120:
            parts.append(
                "<bottom-toolbar.label> depth </bottom-toolbar.label>"
                f"<bottom-toolbar.tokens> {depth} </bottom-toolbar.tokens>"
            )
        if cols >= 120:
            parts.append(
                "<bottom-toolbar.tip> enter submit · \\ enter newline · "
                "ctrl-c cancel · ctrl-l clear </bottom-toolbar.tip>"
            )
        return HTML("<bottom-toolbar.sep> · </bottom-toolbar.sep>".join(parts))

    def read(self) -> str:
        if self._ensure_session():
            assert self._session is not None
            return self._session.prompt(_prompt_text())
        return input("◈ Ready\n◈ ")

from __future__ import annotations

import asyncio
from io import StringIO
from types import SimpleNamespace

from rich.console import Console
from prompt_toolkit.keys import Keys

from ui.components.status_bar import status_bar
from ui.components.logo import LOGO_HEIGHT, logo, small_wordmark
from ui.repl.banner import render_banner
from ui.repl.keys import build_key_bindings
from ui.repl.prompt import bottom_fragments, continuation_fragments, prompt_fragments


def test_ctrl_p_opens_handler_backed_slash_picker():
    repl = SimpleNamespace(
        session=SimpleNamespace(default_buffer=SimpleNamespace(text="", cursor_position=0)),
        _completion_index=4,
        _completion_scroll=2,
    )
    bindings = build_key_bindings(SimpleNamespace(), repl)
    binding = bindings.get_bindings_for_keys((Keys.ControlP,))[0]
    buffer = SimpleNamespace(text="", cursor_position=0)

    binding.handler(SimpleNamespace(current_buffer=buffer))

    assert buffer.text == "/"
    assert buffer.cursor_position == 1
    assert repl._completion_index == 0
    assert repl._completion_scroll == 0


def test_status_bar_shows_provider_branch_and_ready_state():
    style, text = status_bar(
        "step-3.7-flash",
        "approval: ask",
        None,
        0.42,
        "C:/projects/kryth",
        100,
        provider="openrouter",
        git_branch="main",
    )

    assert style == "status"
    assert text == "KRYTH | READY | step-3.7-flash | kryth | main | CONTEXT 42% | openrouter | APPROVAL ASK"


def test_status_bar_truncates_to_narrow_terminal():
    _style, text = status_bar(
        "model-long-name", "approval: ask", None, 0.42, "C:/projects/kryth", 24,
        provider="openrouter", git_branch="main",
    )

    assert len(text) <= 24


def test_status_bar_never_exceeds_tiny_terminal_width():
    for width in (1, 2, 3):
        _style, text = status_bar("model", "ask", None, None, None, width)
        assert len(text) <= width


def test_empty_prompt_shows_a_width_aware_composer_hint():
    parts = prompt_fragments(30, placeholder=True)

    assert parts[0] == ("class:prompt", "> ")
    assert "Describe your task..." in parts[1][1]


def test_narrow_prompt_omits_composer_hint():
    parts = prompt_fragments(6, placeholder=True)
    assert parts == [("class:prompt", "> ")]


def test_fresh_session_banner_is_compact_and_contextual(tmp_path):
    from config.config import Config

    output = StringIO()
    from ui.theme import AGENT_THEME

    console = Console(file=output, width=80, color_system=None, theme=AGENT_THEME)
    config = Config(cwd=tmp_path)
    config.model_name = "provider/model-name"
    render_banner(
        console,
        config,
        session_id="1234567890",
        git_branch="main",
        fresh=True,
    )

    rendered = output.getvalue()
    assert "██╗  ██╗" in rendered
    assert "model-name" in rendered
    assert tmp_path.name in rendered
    assert "WORKSPACE" in rendered
    assert "██╗  ██╗" in rendered
    assert LOGO_HEIGHT == 6
    assert len(logo().plain.splitlines()) == 6
    assert all(ord(char) < 128 for char in logo(unicode=False).plain)


def test_wide_prompt_keeps_the_composer_borderless():
    parts = prompt_fragments(80, placeholder=True)

    assert parts[0] == ("class:prompt", "> ")
    assert "-" not in parts[0][1]
    assert continuation_fragments(80) == [("", "  ")]


def test_composer_status_line_keeps_status():
    fragments = bottom_fragments(
        60,
        "approval: ask",
        "normal",
        ("status", "model | workspace | Ready"),
    )

    assert fragments == [("class:status", " model | workspace | Ready")]


def test_medium_width_composer_uses_no_decorative_rule():
    top = prompt_fragments(60)[0][1]

    assert top == "> "


def test_welcome_metadata_fits_narrow_width_and_keeps_core_fields():
    from rich.console import Console
    from ui.components.welcome import welcome_screen
    from ui.theme import AGENT_THEME

    console = Console(record=True, theme=AGENT_THEME, width=28, color_system=None)
    console.print(
        welcome_screen(
            model="very-long-model-name",
            workspace="a-very-long-workspace-name",
            session_id="1234567890",
            branch="main",
            width=28,
            unicode=False,
        )
    )
    rendered = console.export_text()

    assert "MODEL" in rendered
    assert "WORKSPACE" in rendered


def test_narrow_banner_uses_text_wordmark(tmp_path):
    from config.config import Config

    output = StringIO()
    from ui.theme import AGENT_THEME

    console = Console(file=output, width=40, color_system=None, theme=AGENT_THEME)
    render_banner(console, Config(cwd=tmp_path), fresh=False)

    assert "KRYTH" in output.getvalue()
    assert len(small_wordmark().plain) == 5


def test_turn_keys_ctrl_c_cancels_both_approval_and_turn():
    """Ctrl+C during approval cancels both the pending confirmation and the turn task."""
    from config.config import Config
    from rich.console import Console
    from ui.theme import AGENT_THEME
    from ui.tui import TUI
    from ui.components.terminal_approval import TerminalApprovalPanel, ApprovalAction
    from tools.base import ToolConfirmation
    from ui.repl.keys import turn_keys
    from prompt_toolkit.keys import Keys
    from unittest.mock import patch
    from types import SimpleNamespace

    async def run():
        tui = TUI(Config(), Console(record=True, theme=AGENT_THEME))
        pending = asyncio.get_running_loop().create_future()
        tui._pending_confirmation = pending
        tui._approval_panel = TerminalApprovalPanel(ToolConfirmation(
            tool_name="bash", params={"command": "echo ok"}, description="Execute: echo ok", command="echo ok"
        ))

        async def dummy():
            await asyncio.sleep(100)
        task = asyncio.create_task(dummy())

        key_press = SimpleNamespace(key=Keys.ControlC, data="")

        class FakeDevice:
            def __init__(self, keys):
                self.keys = keys

            def raw_mode(self):
                return self

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def attach(self, cb):
                cb()  # simulate immediate key processing
                return self

            def read_keys(self):
                return list(self.keys)

        device = FakeDevice([key_press])

        with patch("ui.repl.keys.create_input", return_value=device):
            with turn_keys(tui, task):
                pass

        # Let the cancelled task process the cancellation request.
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert pending.done()
        assert pending.result() == ApprovalAction.CANCELLED
        assert task.cancelled()

    asyncio.run(run())


def test_turn_keys_cancels_dangling_pending_on_exit():
    """When turn_keys exits with a pending confirmation, it is failed closed with CANCELLED."""
    from config.config import Config
    from rich.console import Console
    from ui.theme import AGENT_THEME
    from ui.tui import TUI
    from ui.components.terminal_approval import TerminalApprovalPanel, ApprovalAction
    from tools.base import ToolConfirmation
    from ui.repl.keys import turn_keys
    from unittest.mock import patch

    async def run():
        tui = TUI(Config(), Console(record=True, theme=AGENT_THEME))
        pending = asyncio.get_running_loop().create_future()
        tui._pending_confirmation = pending
        tui._approval_panel = TerminalApprovalPanel(ToolConfirmation(
            tool_name="bash", params={"command": "echo ok"}, description="Execute: echo ok", command="echo ok"
        ))

        async def dummy():
            await asyncio.sleep(100)
        task = asyncio.create_task(dummy())

        class FakeDevice:
            def raw_mode(self):
                return self

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def attach(self, cb):
                cb()  # invoke on_keys; read_keys returns [] — nothing consumed
                return self

            def read_keys(self):
                return []

        device = FakeDevice()

        with patch("ui.repl.keys.create_input", return_value=device):
            with turn_keys(tui, task):
                pass

        assert pending.done()
        assert pending.result() == ApprovalAction.CANCELLED
        assert not task.cancelled()

    asyncio.run(run())

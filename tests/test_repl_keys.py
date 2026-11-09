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
        "model-long-name", "approval: ask", 0.42, "C:/projects/kryth", 24,
        provider="openrouter", git_branch="main",
    )

    assert len(text) <= 24


def test_status_bar_never_exceeds_tiny_terminal_width():
    for width in (1, 2, 3):
        _style, text = status_bar("model", "ask", None, None, width)
        assert len(text) <= width


def test_empty_prompt_shows_a_width_aware_composer_hint():
    parts = prompt_fragments(30, placeholder=True)

    assert parts[0] == ("class:prompt", "> ")
    assert "Ask KRYTH" in parts[1][1]
    assert len(parts[1][1]) <= 28


def test_narrow_prompt_omits_composer_hint():
    assert prompt_fragments(6, placeholder=True) == [("class:prompt", "> ")]


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
    assert "KRYTH" in rendered
    assert "model-name" in rendered
    assert tmp_path.name in rendered
    assert "main" in rendered
    assert "12345678" in rendered
    assert "Your workspace. Your agent. Your next build." in rendered
    assert LOGO_HEIGHT == 6
    assert len(logo().plain.splitlines()) == 6
    assert all(ord(char) < 128 for char in logo(unicode=False).plain)


def test_wide_prompt_has_a_responsive_ascii_frame_and_action_hints():
    parts = prompt_fragments(80, placeholder=True)
    top = parts[0][1].rstrip("\n")

    assert len(top) == 80
    assert "KRYTH" in top
    assert "Ctrl+J new line" in top
    assert parts[1] == ("class:frame", "| ")
    assert continuation_fragments(80) == [("class:frame", "|   ")]


def test_composer_status_line_keeps_status():
    fragments = bottom_fragments(
        60,
        "approval: ask",
        "normal",
        ("status", "model | workspace | Ready"),
    )

    assert fragments == [("class:status", " model | workspace | Ready")]


def test_medium_width_composer_uses_short_action_hints():
    top = prompt_fragments(60)[0][1]

    assert len(top.rstrip("\n")) == 60
    assert "/ commands" in top
    assert "Ctrl+J" not in top


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

    assert "M " in rendered
    assert "WORK" in rendered
    assert "VER" in rendered
    assert "main" not in rendered
    assert "12345678" in rendered


def test_narrow_banner_uses_text_wordmark(tmp_path):
    from config.config import Config

    output = StringIO()
    from ui.theme import AGENT_THEME

    console = Console(file=output, width=40, color_system=None, theme=AGENT_THEME)
    render_banner(console, Config(cwd=tmp_path), fresh=False)

    assert "KRYTH" in output.getvalue()
    assert len(small_wordmark().plain) == 5

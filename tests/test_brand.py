from pathlib import Path
from xml.etree import ElementTree

from io import StringIO

from rich.console import Console

from ui.components.logo import LOGO_HEIGHT, LOGO_WIDTH, horizontal_logo, logo, logo_lines
from ui.components.welcome import welcome_screen
from ui.theme import AGENT_THEME, hex_colour


ROOT = Path(__file__).resolve().parents[1]


def render(renderable, width: int) -> str:
    console = Console(record=True, theme=AGENT_THEME, width=width, color_system=None)
    console.print(renderable)
    return console.export_text()


def test_brand_tokens_match_the_kryth_palette():
    assert [hex_colour(key) for key in ("obsidian", "graphite", "muted", "secondary", "primary")] == [
        "#090909", "#171717", "#737373", "#a3a3a3", "#f5f5f5"
    ]
    assert {
        key: hex_colour(key) for key in ("blue", "green", "amber", "red", "violet")
    } == {
        "blue": "#7890a4",
        "green": "#8baf88",
        "amber": "#d6aa65",
        "red": "#d77a7a",
        "violet": "#a3a3a3",
    }
    assert hex_colour("brand") == "#e8ff3a"


def test_terminal_mark_has_unicode_and_ascii_fallbacks():
    unicode_mark = logo(unicode=True).plain
    ascii_mark = logo(unicode=False).plain

    assert len(unicode_mark.splitlines()) == 6
    assert len(ascii_mark.splitlines()) == 6
    assert LOGO_HEIGHT == 6
    assert LOGO_WIDTH == 42
    assert {len(row.plain) for row in logo_lines(unicode=False)} == {LOGO_WIDTH}
    assert all(ord(char) < 128 for char in ascii_mark)
    assert unicode_mark.count("█") > 0


def test_banner_uses_the_user_supplied_outline_style():
    rows = logo(unicode=True).plain.splitlines()

    assert rows == [
        "██╗  ██╗██████╗ ██╗   ██╗████████╗██╗  ██╗",
        "██║ ██╔╝██╔══██╗╚██╗ ██╔╝╚══██╔══╝██║  ██║",
        "█████╔╝ ██████╔╝ ╚████╔╝    ██║   ███████║",
        "██╔═██╗ ██╔══██╗  ╚██╔╝     ██║   ██╔══██║",
        "██║  ██╗██║  ██║   ██║      ██║   ██║  ██║",
        "╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝      ╚═╝   ╚═╝  ╚═╝",
    ]


def test_kryth_wordmark_uses_white_letters_with_an_acid_gold_y():
    rows = logo_lines()

    assert len(rows) == 6
    assert all(any("#F5F5F5" in str(span.style) for span in row.spans) for row in rows)
    assert all(any("#E8FF3A" in str(span.style) for span in row.spans) for row in rows)
    assert all(len(row.plain) == LOGO_WIDTH for row in rows)


def test_welcome_is_compact_and_shows_model_and_workspace():
    rendered = render(
        welcome_screen(
            model="provider/model-x",
            workspace="saas-platform",
            session_id="1f2f9968abcd",
            branch="main",
            width=90,
        ),
        90,
    )

    for value in (
        "██╗  ██╗", "AUTONOMOUS INTELLIGENCE",
        "Your workspace. Your agent. Your next build.", "MODEL", "model-x",
        "WORKSPACE", "saas-platform", "SESSION", "VERSION", "/help",
        "/model", "/sessions", "/config",
    ):
        assert value in rendered


def test_welcome_drops_tagline_and_optional_branch_when_narrow():
    rendered = render(
        welcome_screen(
            model="model-x",
            workspace="api",
            session_id="abcdef1234",
            branch="main",
            width=40,
            unicode=False,
        ),
        40,
    )

    assert "AUTONOMOUS INTELLIGENCE" in rendered
    assert "BRANCH" not in rendered
    assert "MODEL" in rendered
    assert "WORKSPACE" in rendered


def test_short_terminal_hides_optional_welcome_content():
    rendered = render(
        welcome_screen(
            model="model-x",
            workspace="api",
            session_id="abcdef1234",
            branch="main",
            width=80,
            height=14,
        ),
        80,
    )

    assert "What are we building today?" not in rendered
    assert "VERSION" not in rendered
    assert "BRANCH" not in rendered
    assert "MODEL" in rendered
    assert "WORKSPACE" in rendered


def test_very_narrow_welcome_remains_small():
    rendered = render(
        welcome_screen(
            model="model-x",
            workspace="api",
            session_id="abcdef1234",
            branch=None,
            width=18,
            unicode=False,
        ),
        18,
    )

    assert "KRYTH" in rendered
    assert "MODEL" in rendered
    assert "WORKSPACE" in rendered


def test_horizontal_logo_renders_wordmark_and_descriptor():
    rendered = render(horizontal_logo(tagline=True), 80)

    assert "KRYTH" in rendered
    assert "Autonomous intelligence" in rendered


def test_svg_brand_assets_are_well_formed():
    brand_dir = ROOT / "assets" / "brand"
    files = {
        "kryth-symbol.svg",
        "kryth-horizontal.svg",
        "kryth-monochrome.svg",
        "kryth-icon.svg",
    }
    assert files <= {path.name for path in brand_dir.glob("*.svg")}
    for filename in files:
        root = ElementTree.parse(brand_dir / filename).getroot()
        assert root.tag.endswith("svg")


def test_ui_animation_setting_defaults_on_and_can_be_disabled():
    from config.config import Config

    assert Config().ui.animations is True
    config = Config(ui={"animations": False})
    assert config.ui.animations is False


def test_welcome_hides_missing_runtime_metadata():
    rendered = render(
        welcome_screen(
            model=None,
            workspace=None,
            session_id=None,
            branch=None,
            width=90,
        ),
        90,
    )

    assert "not configured" in rendered


def test_returning_session_uses_compact_identity_and_last_task():
    rendered = render(
        welcome_screen(
            model="provider/model-x",
            workspace="saas-platform",
            session_id="1f2f9968abcd",
            branch="main",
            width=90,
            fresh=False,
            last_task="Improve tool rendering",
        ),
        90,
    )

    assert "██╗  ██╗" in rendered
    assert "Improve tool rendering" not in rendered
    assert "What are we building today?" not in rendered


def test_truecolor_and_monochrome_logo_output():
    colored = Console(record=True, theme=AGENT_THEME, width=80, color_system="truecolor")
    colored.print(logo())
    ansi = colored.export_text(styles=True)
    assert "38;2;232;255;58" in ansi

    monochrome = render(logo(), 80)
    assert "██╗" in monochrome
    assert "█" in monochrome


def test_startup_renders_wordmark_and_session_information():
    from config.config import Config
    from ui.repl.banner import render_banner

    output = StringIO()
    console = Console(
        file=output,
        force_terminal=True,
        width=100,
        height=30,
        color_system=None,
        theme=AGENT_THEME,
    )
    render_banner(
        console,
        Config(cwd=ROOT),
        session_id="12345678",
        git_branch="main",
        fresh=True,
    )
    rendered = output.getvalue()

    assert "██╗  ██╗" in rendered
    assert "Your workspace. Your agent. Your next build." in rendered
    assert "What are we building today?" in rendered
    assert all(command in rendered for command in ("/help", "/model", "/sessions", "/config"))
    assert "SESSION" in rendered
    assert "VERSION" in rendered

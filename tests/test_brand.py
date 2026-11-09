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
    assert hex_colour("obsidian") == "#101114"
    assert hex_colour("graphite") == "#1d2025"
    assert hex_colour("surface") == "#252930"
    assert hex_colour("muted") == "#858b91"
    assert hex_colour("primary") == "#ecedef"
    assert hex_colour("secondary") == "#a1a5ad"
    assert hex_colour("accent") == "#dfff00"


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

    assert len(rows) == 6
    assert rows[0].startswith("██╗  ██╗")
    assert rows[1].startswith("██║ ██╔╝")
    assert rows[2].startswith("█████╔╝")
    assert rows[3].startswith("██╔═██╗")
    assert rows[4].startswith("██║  ██╗")
    assert rows[5].startswith("╚═╝  ╚═╝")


def test_kryth_banner_uses_lime_only_on_the_y_letter():
    rows = logo_lines()
    lime = rows[0].spans

    assert len(rows) == 6
    assert any("223,255,0" in str(span.style) for span in lime)
    assert all(len(row.plain) == LOGO_WIDTH for row in rows)


def test_welcome_contains_live_context_and_existing_actions():
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

    for value in ("KRYTH", "MODEL", "model-x", "WORKSPACE", "saas-platform", "SESSION", "1f2f9968", "VERSION", "BRANCH", "main", "/help", "/model", "/sessions"):
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

    assert "Autonomous intelligence" not in rendered
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


def test_very_narrow_welcome_uses_wordmark_and_short_labels():
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
    assert "M" in rendered
    assert "WORK" in rendered
    assert "VER" in rendered


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

    assert "MODEL" not in rendered
    assert "WORKSPACE" not in rendered
    assert "BRANCH" not in rendered
    assert "SESSION" not in rendered
    assert "VERSION" in rendered


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

    assert "KRYTH" in rendered
    assert "Improve tool rendering" in rendered
    assert "What are we building today?" not in rendered


def test_truecolor_and_monochrome_logo_output():
    colored = Console(record=True, theme=AGENT_THEME, width=80, color_system="truecolor")
    colored.print(logo())
    ansi = colored.export_text(styles=True)
    assert "38;2;223;255;0" in ansi

    monochrome = render(logo(), 80)
    assert "KRYTH" not in monochrome
    assert "█" in monochrome


def test_reduced_motion_disables_startup_reveal(monkeypatch):
    from config.config import Config
    from ui.motion import animations_enabled
    from ui.repl.banner import _should_reveal_banner

    console = Console(file=StringIO(), force_terminal=True, width=100, height=30)
    config = Config()
    assert _should_reveal_banner(console, config, fresh=True)

    monkeypatch.setenv("KRYTH_REDUCED_MOTION", "1")
    assert not animations_enabled(config)
    assert not _should_reveal_banner(console, config, fresh=True)

    monkeypatch.delenv("KRYTH_REDUCED_MOTION")
    monkeypatch.setenv("KRYTH_NO_ANIMATION", "true")
    assert not animations_enabled(config)


def test_disabled_animation_still_renders_complete_banner(monkeypatch):
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
    monkeypatch.setenv("KRYTH_NO_ANIMATION", "1")
    render_banner(
        console,
        Config(cwd=ROOT),
        session_id="12345678",
        git_branch="main",
        fresh=True,
    )
    rendered = output.getvalue()

    assert "KRYTH" in rendered
    assert "What are we building today?" in rendered
    assert "12345678" in rendered

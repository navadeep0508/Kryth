from rich.console import Console

from ui.components.markdown import render_markdown
from ui.theme import AGENT_THEME


def render(text: str, width: int = 80) -> str:
    console = Console(record=True, theme=AGENT_THEME, width=width, color_system=None)
    console.print(render_markdown(text))
    return console.export_text()


def test_markdown_renders_headings_paragraphs_lists_and_quotes():
    output = render(
        "# Summary\n\nA paragraph that\ncontinues on the next line.\n\n"
        "- first\n  - nested\n1. second\n\n> quoted text"
    )
    for value in ("Summary", "A paragraph that continues on the next line.", "- first", "  - nested", "1. second", "> quoted text"):
        assert value in output


def test_markdown_renders_tables_links_and_inline_code():
    output = render(
        "Name | Value\n--- | ---\nmodel | `kryth`\n\n[Docs](https://example.test/docs)"
    )
    for value in ("Name", "Value", "model", "kryth", "Docs", "https://example.test/docs"):
        assert value in output


def test_markdown_code_blocks_are_unboxed_and_width_aware():
    output = render("```python\ndef run():\n    return 1\n```", width=40)
    assert "python" in output
    assert "def run():" in output
    assert "+--" not in output

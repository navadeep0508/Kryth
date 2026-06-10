"""Language detection + syntax highlighting helpers.

Single source of truth for "given a path, what Pygments lexer should I
use?" and "given one line of code, highlight it with that lexer". The
diff renderer asks here; the file-creation panel asks here; nothing
else needs to know about Pygments.
"""

from __future__ import annotations

import os
from functools import lru_cache

from rich.syntax import Syntax
from rich.text import Text


_LEXER_FOR_EXT: dict[str, str] = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript", ".tsx": "tsx",
    ".html": "html", ".htm": "html",
    ".css": "css", ".scss": "scss", ".sass": "sass",
    ".json": "json", ".jsonc": "json",
    ".md": "markdown", ".mdx": "markdown",
    ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp",
    ".cs": "csharp", ".rb": "ruby", ".php": "php", ".sql": "sql",
    ".tf": "hcl", ".xml": "xml",
    ".lua": "lua", ".pl": "perl", ".r": "r",
    ".dart": "dart", ".swift": "swift",
}


def lexer_for_path(path: str) -> str:
    """Best-effort Pygments lexer name for ``path``. Falls back to
    ``text`` if the extension is unknown.
    """
    base = os.path.basename(path).lower()
    if base == "dockerfile" or base.startswith("dockerfile."):
        return "dockerfile"
    if base in ("makefile", "gnumakefile"):
        return "make"
    if base.endswith(".env") or base == ".env":
        return "ini"
    ext = os.path.splitext(path)[1].lower()
    return _LEXER_FOR_EXT.get(ext, "text")


@lru_cache(maxsize=32)
def _syntax_template(lexer: str) -> Syntax:
    """Cached Syntax object per lexer. We never render the template
    itself — only call ``highlight()`` on it — so a single instance per
    lexer is safe to share."""
    return Syntax(
        "",
        lexer,
        theme="ansi_dark",
        background_color="default",
        line_numbers=False,
        word_wrap=False,
    )


def highlight_line(text: str, lexer: str) -> Text:
    """Syntax-highlight a single line of code and return a Rich ``Text``.

    Safe for any lexer name — unknown lexers degrade to plain ``Text``.
    Trailing newlines are stripped so the highlighter doesn't emit a
    stray blank line that the diff grid would render as a gap.
    """
    if not text:
        return Text("")
    try:
        out = _syntax_template(lexer).highlight(text)
    except Exception:
        return Text(text)
    # Syntax.highlight always ends with a newline. The diff grid prints
    # each line in its own row, so the trailing newline would double-space.
    if out.plain.endswith("\n"):
        out = out[:-1]
    return out


def highlight_block(text: str, lexer: str) -> Syntax:
    """Multi-line block highlight — used by the file-creation panel.
    Uses a fresh Syntax instance so we can opt into line numbers without
    contaminating the cached single-line template."""
    return Syntax(
        text,
        lexer,
        theme="ansi_dark",
        background_color="default",
        line_numbers=True,
        word_wrap=False,
        indent_guides=False,
    )

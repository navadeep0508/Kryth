"""KRYTH block wordmark and compact geometric terminal lockup."""

from rich.table import Table
from rich.text import Text

from ui.theme.palette import PALETTE

KRYTH_VERSION = "0.0.38"

_BLOCK = "█"
_ROWS = (
    "\u2588\u2588\u2557  \u2588\u2588\u2557\u2588\u2588\u2588\u2588\u2588\u2588\u2557 \u2588\u2588\u2557   \u2588\u2588\u2557\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2557\u2588\u2588\u2557  \u2588\u2588\u2557",
    "\u2588\u2588\u2551 \u2588\u2588\u2554\u255d\u2588\u2588\u2554\u2550\u2550\u2588\u2588\u2557\u255a\u2588\u2588\u2557 \u2588\u2588\u2554\u255d\u255a\u2550\u2550\u2588\u2588\u2554\u2550\u2550\u255d\u2588\u2588\u2551  \u2588\u2588\u2551",
    "\u2588\u2588\u2588\u2588\u2588\u2554\u255d \u2588\u2588\u2588\u2588\u2588\u2588\u2554\u255d \u255a\u2588\u2588\u2588\u2588\u2554\u255d    \u2588\u2588\u2551   \u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2551",
    "\u2588\u2588\u2554\u2550\u2588\u2588\u2557 \u2588\u2588\u2554\u2550\u2550\u2588\u2588\u2557  \u255a\u2588\u2588\u2554\u255d     \u2588\u2588\u2551   \u2588\u2588\u2554\u2550\u2550\u2588\u2588\u2551",
    "\u2588\u2588\u2551  \u2588\u2588\u2557\u2588\u2588\u2551  \u2588\u2588\u2551   \u2588\u2588\u2551      \u2588\u2588\u2551   \u2588\u2588\u2551  \u2588\u2588\u2551",
    "\u255a\u2550\u255d  \u255a\u2550\u255d\u255a\u2550\u255d  \u255a\u2550\u255d   \u255a\u2550\u255d      \u255a\u2550\u255d   \u255a\u2550\u255d  \u255a\u2550\u255d",
)
_ASCII_TRANSLATION = str.maketrans({
    "█": "#", "╗": "+", "║": "|", "╔": "+",
    "╝": "+", "═": "=", "╚": "+",
})
_LETTERS = {
    "K": ("█   █", "█  █ ", "█ █  ", "██   ", "█ █  ", "█  █ "),
    "R": ("████ ", "█   █", "█   █", "████ ", "█ █  ", "█  █ "),
    "Y": ("█   █", " █ █ ", "  █  ", "  █  ", "  █  ", "  █  "),
    "T": ("█████", "  █  ", "  █  ", "  █  ", "  █  ", "  █  "),
    "H": ("█   █", "█   █", "█   █", "█████", "█   █", "█   █"),
}
_UNICODE_SYMBOL = ("┃╲", "┣━╸", "┃╱")
_ASCII_SYMBOL = ("|\\", "|-", "|/")
_PRIMARY = PALETTE["primary"]
_ACCENT = PALETTE["accent"]


def small_wordmark(justify: str = "left") -> Text:
    """The compact wordmark preserves the banner's white and lime treatment."""
    text = Text(no_wrap=True, justify=justify)
    text.append("KR", style=f"bold {_PRIMARY}")
    text.append("Y", style=f"bold {_ACCENT}")
    text.append("TH", style=f"bold {_PRIMARY}")
    return text


def logo_lines(*, unicode: bool = True) -> list[Text]:
    """Render the six-row KRYTH outline wordmark with an ASCII fallback."""
    rows: list[Text] = []
    for value in _ROWS:
        row = value.ljust(LOGO_WIDTH)
        if not unicode:
            row = row.translate(_ASCII_TRANSLATION)
        line = Text(no_wrap=True)
        line.append(row[:16], style=f"bold {_PRIMARY}")
        line.append(row[16:24], style=f"bold {_ACCENT}")
        line.append(row[24:], style=f"bold {_PRIMARY}")
        rows.append(line)
    return rows


def logo(*, unicode: bool = True) -> Text:
    """Return the six-line KRYTH wordmark, or its ASCII-safe fallback."""
    result = Text(no_wrap=True, justify="center")
    for index, line in enumerate(logo_lines(unicode=unicode)):
        if index:
            result.append("\n")
        result.append_text(line)
    return result


def compact_symbol(*, unicode: bool = True) -> Text:
    symbol = _UNICODE_SYMBOL if unicode else _ASCII_SYMBOL
    return Text("\n".join(symbol), style=f"bold {_ACCENT}", no_wrap=True)


def horizontal_logo(*, unicode: bool = True, tagline: bool = True) -> Table:
    """Compose the compact geometric K and wordmark for returning sessions."""
    brand = Table.grid(padding=(0, 0))
    brand.add_column(no_wrap=True)
    brand.add_row(small_wordmark())
    if tagline:
        brand.add_row(Text("Autonomous intelligence", style="muted"))

    lockup = Table.grid(padding=(0, 2))
    lockup.add_column(no_wrap=True, vertical="middle")
    lockup.add_column(no_wrap=True, vertical="middle")
    lockup.add_row(compact_symbol(unicode=unicode), brand)
    return lockup


LOGO_WIDTH = 42
LOGO_HEIGHT = 6

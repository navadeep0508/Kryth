import re

from rich.syntax import Syntax
import re

from rich.syntax import Syntax
from rich.text import Text

from ui.theme import KRYTH_SYNTAX

from ui.theme import KRYTH_SYNTAX


def diff_glimpse(diff: str, max_lines: int = 3) -> str:

    added: list[str] = []
    removed: list[str] = []

    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            body = line[1:]
            if body.strip():
                added.append(body)
        elif line.startswith("-") and not line.startswith("---"):
            body = line[1:]
            if body.strip():
                removed.append(body)
        if len(added) >= max_lines:
            break

    chosen = added or removed[:max_lines]
    if not chosen:
        return ""

    common = min((len(l) - len(l.lstrip()) for l in chosen), default=0)
    return "\n".join(line[common:] for line in chosen)


def diff_counts(diff: str) -> tuple[int, int]:
    added = removed = 0
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def diff_stat(diff: str) -> str:
    added, removed = diff_counts(diff)
    return f"+{added} -{removed}"


def diff_stat_text(diff: str) -> Text:
    added, removed = diff_counts(diff)
    stat = Text()
    stat.append(f"+{added}", style="diff.plus")
    stat.append(" ")
    stat.append(f"-{removed}", style="diff.minus")
    return stat


def edit_preview_rows(
    diff: str, language: str, max_lines: int = 16
) -> tuple[list[Text], int]:
    """Render a compact, numbered first-pass preview of a file diff."""
    rows: list[Text] = []
    new_line = 1
    in_hunk = False

    for line in diff.splitlines():
        if line.startswith("@@"):
            match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if not match:
                continue
            new_line = int(match.group(2))
            in_hunk = True
            continue
        if not in_hunk or line.startswith(("---", "+++", "\\")):
            continue

        marker, code = line[:1], line[1:]
        if marker == "-":
            continue
        if marker not in {" ", "+"}:
            continue

        is_added = marker == "+"
        row = Text()
        row.append(f"{new_line:>4} ", style="success" if is_added else "muted")
        row.append("+ " if is_added else "  ", style="success" if is_added else "muted")
        row.append_text(Syntax(code, language, theme=KRYTH_SYNTAX).highlight(code))
        if is_added:
            row.stylize("on #09270c")
        rows.append(row)
        new_line += 1

    return rows[:max_lines], max(0, len(rows) - max_lines)


def edit_preview_rows(
    diff: str, language: str, max_lines: int = 16
) -> tuple[list[Text], int]:
    """Render a compact, numbered first-pass preview of a file diff."""
    rows: list[Text] = []
    new_line = 1
    in_hunk = False

    for line in diff.splitlines():
        if line.startswith("@@"):
            match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if not match:
                continue
            new_line = int(match.group(2))
            in_hunk = True
            continue
        if not in_hunk or line.startswith(("---", "+++", "\\")):
            continue

        marker, code = line[:1], line[1:]
        if marker == "-":
            continue
        if marker not in {" ", "+"}:
            continue

        is_added = marker == "+"
        row = Text()
        row.append(f"{new_line:>4} ", style="success" if is_added else "muted")
        row.append("+ " if is_added else "  ", style="success" if is_added else "muted")
        row.append_text(Syntax(code, language, theme=KRYTH_SYNTAX).highlight(code))
        if is_added:
            row.stylize("on #09270c")
        rows.append(row)
        new_line += 1

    return rows[:max_lines], max(0, len(rows) - max_lines)

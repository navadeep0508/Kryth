from rich.text import Text


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

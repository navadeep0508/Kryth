import re

from pathlib import Path

_EXTENSION_LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".json": "json",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".md": "markdown",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".css": "css",
    ".html": "html",
    ".xml": "xml",
    ".sql": "sql",
}


def guess_language(path: str | None) -> str:
    if not path:
        return "text"
    return _EXTENSION_LANGUAGES.get(Path(path).suffix.lower(), "text")


def extract_read_code(text: str) -> tuple[int, str] | None:
    body = text
    header_match = re.match(
        r"^Showing lines (\d+)-(\d+) of (\d+)[^\n]*\n\n", text, re.IGNORECASE
    )
    if header_match:
        body = text[header_match.end():]

    code_lines: list[str] = []
    start_line: int | None = None

    for line in body.splitlines():
        match = re.match(r"^\s*(\d+)\|(.*)$", line)
        if not match:
            return None
        if start_line is None:
            start_line = int(match.group(1))
        code_lines.append(match.group(2))

    if start_line is None:
        return None

    return start_line, "\n".join(code_lines)

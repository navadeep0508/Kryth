"""Project file map for the initial system prompt.

Capped to ``MAX_PROJECT_MAP_FILES`` entries — the agent uses
``list_files``/``glob``/``grep`` for everything beyond that, and the
prompt-resident map only needs to give a sense of structure.
"""

import os

IGNORE_DIRS = {
    "node_modules",
    ".git",
    "__pycache__",
    "venv",
    ".venv",
    "env",
    "dist",
    "build",
    ".next",
    ".cache",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
}

SOURCE_SUFFIXES = (
    ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".rb", ".php", ".c", ".h",
    ".cpp", ".hpp", ".cs", ".swift", ".sh", ".yml", ".yaml",
    ".toml", ".json", ".md",
)

MAX_PROJECT_MAP_FILES = 300


def build_project_map(directory: str = ".", limit: int = MAX_PROJECT_MAP_FILES) -> str:
    """Return a newline-joined list of source files, capped at ``limit``.

    Files are ranked by (depth ascending, mtime descending) so the cap
    favours top-level structural files over deeply nested generated ones.
    """
    candidates = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]
        for file in files:
            if not file.endswith(SOURCE_SUFFIXES):
                continue
            path = os.path.join(root, file)
            depth = path.count(os.sep)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = 0.0
            candidates.append((depth, -mtime, path))

    candidates.sort()
    paths = [c[2] for c in candidates[:limit]]
    out = "\n".join(paths)
    if len(candidates) > limit:
        out += f"\n...({len(candidates) - limit} more files; use list_files / glob to explore)"
    return out

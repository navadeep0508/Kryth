"""READ handler — scan repo, detect important files, read, summarize."""

from __future__ import annotations

import os
from pathlib import Path


def scan_repo(directory: str = ".") -> dict:
    """Scan a repo and return structure overview."""
    root = Path(directory).resolve()
    if not root.exists():
        return {"error": f"path does not exist: {root}"}

    entries = {"files": [], "dirs": [], "config_files": [], "source_dirs": []}
    try:
        for entry in sorted(root.iterdir()):
            name = entry.name
            if name.startswith("."):
                continue
            if entry.is_file():
                entries["files"].append(name)
                if name in ("package.json", "pyproject.toml", "Cargo.toml", "go.mod", "Gemfile"):
                    entries["config_files"].append(name)
            elif entry.is_dir():
                entries["dirs"].append(name)
                if name in ("src", "lib", "app", "components", "pages", "routes", "api"):
                    entries["source_dirs"].append(name)
    except PermissionError:
        pass

    return {
        "root": str(root),
        "file_count": len(entries["files"]),
        "dir_count": len(entries["dirs"]),
        "config_files": entries["config_files"],
        "source_dirs": entries["source_dirs"],
        "entries": entries["files"] + [f"{d}/" for d in entries["dirs"]],
    }


def detect_main_files(directory: str = ".") -> list[dict]:
    """Detect entry points and important files."""
    root = Path(directory)
    candidates = [
        "README.md", "index.js", "index.ts", "main.py", "app.py",
        "package.json", "pyproject.toml", "Cargo.toml", "go.mod",
        "Dockerfile", "docker-compose.yml", "Makefile",
        "setup.py", "setup.cfg", "requirements.txt",
        "next.config.js", "vite.config.ts", "webpack.config.js",
        "tailwind.config.js", "tsconfig.json",
    ]
    found = []
    for name in candidates:
        p = root / name
        if p.exists() and p.is_file():
            found.append({"path": str(p), "name": name, "size": p.stat().st_size})
    return found


def read_files(paths: list[str], max_files: int = 5) -> list[dict]:
    """Read up to max_files and return their content."""
    results = []
    for path in paths[:max_files]:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            results.append({"path": path, "content": content, "size": len(content), "error": None})
        except Exception as e:
            results.append({"path": path, "content": "", "size": 0, "error": str(e)})
    return results


def summarize_project(directory: str = ".") -> str:
    """Full project summary in one call."""
    scan = scan_repo(directory)
    main_files = detect_main_files(directory)
    lines = [
        f"Project root: {scan['root']}",
        f"Files: {scan['file_count']}, Dirs: {scan['dir_count']}",
    ]
    if scan["config_files"]:
        lines.append(f"Config: {', '.join(scan['config_files'])}")
    if scan["source_dirs"]:
        lines.append(f"Source dirs: {', '.join(scan['source_dirs'])}")
    if scan["entries"]:
        entries = [e for e in scan["entries"] if not e.startswith(".")][:30]
        lines.append(f"Top entries: {', '.join(entries)}")
    if main_files:
        lines.append(f"Key files: {', '.join(m['name'] for m in main_files)}")
    return "\n".join(lines)

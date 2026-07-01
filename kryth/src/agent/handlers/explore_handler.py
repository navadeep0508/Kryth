"""EXPLORE handler — search repo, trace flow, summarize."""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path


IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".next"}


def search(pattern: str, directory: str = ".", include: str | None = None) -> dict:
    """Search repo for a regex pattern. Returns matched lines grouped by file."""
    root = Path(directory).resolve()
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return {"error": f"invalid regex: {e}", "matches": []}

    matches = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(d in path.parts for d in IGNORE_DIRS):
            continue
        if include and not fnmatch.fnmatch(path.name, include):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    rel = path.relative_to(root)
                    matches.append({"file": str(rel), "line": i, "text": line.strip()})
        except Exception:
            continue
    return {"matches": matches, "count": len(matches), "error": None}


def trace_flow(entry_point: str, directory: str = ".") -> list[dict]:
    """Simple flow trace — find imports/references from entry point."""
    root = Path(directory).resolve()
    entry = root / entry_point
    if not entry.exists():
        return [{"error": f"entry point not found: {entry_point}"}]

    results = []
    try:
        text = entry.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r'(?:from|import)\s+["\']?([\w./]+)["\']?', text):
            ref = match.group(1)
            ref_path = root / ref.replace(".", "/").replace("/", os.sep)
            found = list(root.rglob(f"{ref}.*")) or list(root.rglob(f"{ref.replace('.','/')}.*"))
            results.append({
                "source": entry_point,
                "reference": ref,
                "resolved": [str(f.relative_to(root)) for f in found[:3]] if found else ["not found"],
            })
    except Exception as e:
        return [{"error": str(e)}]
    return results


def summarize_file(filepath: str) -> dict:
    """Summarize a file: lines, classes, functions, imports."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        lines = content.splitlines()
        summary = {
            "path": filepath,
            "lines": len(lines),
            "size": len(content),
            "language": Path(filepath).suffix,
        }
        if filepath.endswith(".py"):
            summary["classes"] = len(re.findall(r"^class\s+\w+", content, re.MULTILINE))
            summary["functions"] = len(re.findall(r"^def\s+\w+", content, re.MULTILINE))
            summary["imports"] = len(re.findall(r"^(?:import|from)\s+\w+", content, re.MULTILINE))
        return summary
    except Exception as e:
        return {"path": filepath, "error": str(e)}

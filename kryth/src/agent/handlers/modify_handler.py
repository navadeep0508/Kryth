"""MODIFY handler — locate, read, patch, verify syntax."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


def locate_target(pattern: str, directory: str = ".") -> list[str]:
    """Find files matching a pattern."""
    root = Path(directory).resolve()
    matches = []
    for path in root.rglob(pattern):
        if path.is_file():
            matches.append(str(path))
    return matches


def read_target(path: str) -> dict:
    """Read a file for modification."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {"path": path, "content": content, "size": len(content), "error": None}
    except Exception as e:
        return {"path": path, "content": "", "size": 0, "error": str(e)}


def apply_edit(path: str, old_text: str, new_text: str) -> dict:
    """Apply an edit to a file — replace first occurrence of old_text."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if old_text not in content:
            return {"success": False, "error": "old_text not found in file", "path": path}
        new_content = content.replace(old_text, new_text, 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return {"success": True, "path": path}
    except Exception as e:
        return {"success": False, "error": str(e), "path": path}


def apply_write(path: str, content: str) -> dict:
    """Write content to a file."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"success": True, "path": path, "size": len(content)}
    except Exception as e:
        return {"success": False, "error": str(e), "path": path}


def verify_syntax(filepath: str) -> dict:
    """Check Python syntax validity."""
    ext = Path(filepath).suffix
    if ext == ".py":
        try:
            import ast
            with open(filepath, "r", encoding="utf-8") as f:
                ast.parse(f.read())
            return {"valid": True, "error": None}
        except SyntaxError as e:
            return {"valid": False, "error": f"SyntaxError at line {e.lineno}: {e.msg}"}
        except Exception as e:
            return {"valid": False, "error": str(e)}
    elif ext in (".js", ".ts", ".jsx", ".tsx"):
        try:
            result = subprocess.run(
                ["node", "--check", filepath],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                return {"valid": True, "error": None}
            return {"valid": False, "error": result.stderr.strip() or result.stdout.strip()}
        except FileNotFoundError:
            return {"valid": None, "error": "node not found — cannot verify"}
        except Exception as e:
            return {"valid": False, "error": str(e)}
    elif ext == ".json":
        try:
            import json
            with open(filepath, "r", encoding="utf-8") as f:
                json.load(f)
            return {"valid": True, "error": None}
        except json.JSONDecodeError as e:
            return {"valid": False, "error": str(e)}
    return {"valid": None, "error": "unsupported file type for syntax verification"}

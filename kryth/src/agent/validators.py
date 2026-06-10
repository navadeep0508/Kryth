"""Post-edit verification helpers.

Per-write syntax checks already live in ``tools/_file_ops._validate_content``.
This module is the BATCH layer: after several edits across several
files, validate the set as a whole using cheap, language-aware checks.

What's checked:
    .py                  py_compile (catches more than ast.parse: bytecode-level)
    .json                json.loads (full parse)
    .yaml / .yml         yaml.safe_load if PyYAML is importable; else skipped
    .toml                tomllib.loads on Python 3.11+; else skipped

Whatever isn't directly checkable is skipped silently — the goal is to
SURFACE failures, not to gate progress on a missing dev dep.

The public entry point is ``validate_paths(paths)``, which returns a
single string the agent can grep ``[validation]`` for. It never raises.
"""

from __future__ import annotations

import json
import os
import py_compile
from pathlib import Path


def _check_python(path: str) -> str:
    try:
        py_compile.compile(path, doraise=True)
    except py_compile.PyCompileError as e:
        # PyCompileError stringifies to the full Python traceback. Trim
        # it to the message line for the model — full TB is rarely
        # useful and bloats context.
        msg = str(e).strip().splitlines()
        return msg[-1] if msg else "py_compile error"
    except OSError as e:
        return f"py_compile failed: {e}"
    return ""


def _check_json(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            json.load(f)
    except json.JSONDecodeError as e:
        return f"JSON parse error: {e.msg} at line {e.lineno}"
    except OSError as e:
        return f"read failed: {e}"
    return ""


def _check_yaml(path: str) -> str:
    try:
        import yaml  # type: ignore[import-not-found]
    except Exception:
        return ""  # PyYAML not installed; skip silently
    try:
        with open(path, "r", encoding="utf-8") as f:
            yaml.safe_load(f)
    except yaml.YAMLError as e:
        return f"YAML parse error: {e}"
    except OSError as e:
        return f"read failed: {e}"
    return ""


def _check_toml(path: str) -> str:
    try:
        import tomllib  # 3.11+
    except Exception:
        return ""  # too old; skip silently
    try:
        with open(path, "rb") as f:
            tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        return f"TOML parse error: {e}"
    except OSError as e:
        return f"read failed: {e}"
    return ""


_CHECKERS = {
    ".py": _check_python,
    ".json": _check_json,
    ".yaml": _check_yaml,
    ".yml": _check_yaml,
    ".toml": _check_toml,
}


def validate_paths(paths) -> str:
    """Run language-aware validators on ``paths`` and return a report.

    Returns "" when everything passes (caller decides whether to surface
    a "looks clean" message). Returns a multi-line string when at least
    one file failed.
    """
    if isinstance(paths, str):
        path_list = [paths]
    elif isinstance(paths, (list, tuple)):
        path_list = [p for p in paths if isinstance(p, str)]
    else:
        return ""

    failures: list[str] = []
    checked = 0
    skipped: list[str] = []

    for p in path_list:
        if not os.path.isfile(p):
            failures.append(f"  [missing] {p}")
            continue
        ext = Path(p).suffix.lower()
        checker = _CHECKERS.get(ext)
        if checker is None:
            skipped.append(p)
            continue
        msg = checker(p)
        checked += 1
        if msg:
            failures.append(f"  [{ext}] {p}: {msg}")

    if failures:
        head = f"validation: {len(failures)} issue(s) across {checked} checked file(s)"
        return head + "\n" + "\n".join(failures)
    if checked:
        return ""  # all good — caller decides messaging
    return f"(no validators applied — extensions: {', '.join(sorted({Path(p).suffix or '<none>' for p in skipped})) or '<none>'})"


__all__ = ["validate_paths"]

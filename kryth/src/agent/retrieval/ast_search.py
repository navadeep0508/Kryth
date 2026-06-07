"""Structural code search using ast-grep CLI.

ast-grep searches by AST pattern rather than text, so it finds
structural constructs even if the code is formatted differently.

Fallback: when ast-grep is not installed, Python files fall back to the
existing repo_index AST index.

Supported named patterns:
  python:     function, async_function, class, decorator, import,
              from_import, lambda, with_statement, try_except
  javascript: function, arrow_function, class, async_function,
              import, export, react_component, react_hook, api_route
  typescript: same as javascript

Raw ast-grep pattern strings are passed through unchanged.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Dict, List, Optional

from agent.retrieval import config as cfg


_ast_grep_bin: Optional[str] = None
_bin_checked: bool = False

# Named pattern libraries
_PYTHON_PATTERNS: dict[str, str] = {
    "function":        "def $NAME($$$ARGS): $$$BODY",
    "async_function":  "async def $NAME($$$ARGS): $$$BODY",
    "class":           "class $NAME: $$$BODY",
    "class_base":      "class $NAME($$$BASES): $$$BODY",
    "decorator":       "@$DECORATOR\n$FUNC",
    "import":          "import $MODULE",
    "from_import":     "from $MODULE import $NAME",
    "lambda":          "lambda $$$ARGS: $BODY",
    "with_statement":  "with $CTX as $VAR: $$$BODY",
    "try_except":      "try:\n    $$$BODY\nexcept $EXC:\n    $$$HANDLER",
    "comprehension":   "[$EXPR for $VAR in $ITER]",
}

_JS_PATTERNS: dict[str, str] = {
    "function":        "function $NAME($$$ARGS) { $$$BODY }",
    "arrow_function":  "const $NAME = ($$$ARGS) => $$$BODY",
    "class":           "class $NAME { $$$BODY }",
    "async_function":  "async function $NAME($$$ARGS) { $$$BODY }",
    "import":          "import $$$ITEMS from '$MODULE'",
    "export":          "export $$$DECL",
    "react_component": "function $NAME($$$PROPS) { return ($$$JSX) }",
    "react_hook":      "const [$STATE, $SETTER] = useState($INIT)",
    "api_route_get":   "app.get('$PATH', $$$HANDLER)",
    "api_route_post":  "app.post('$PATH', $$$HANDLER)",
    "api_route":       "router.$METHOD('$PATH', $$$HANDLER)",
}


def _find_ast_grep() -> Optional[str]:
    global _ast_grep_bin, _bin_checked
    if _bin_checked:
        return _ast_grep_bin
    _bin_checked = True
    # 'ast-grep' on most platforms; 'sg' is the short alias
    _ast_grep_bin = shutil.which("ast-grep") or shutil.which("sg")
    return _ast_grep_bin


def search(
    pattern: str,
    language: Optional[str] = None,
    directory: str = ".",
    max_results: int = 50,
) -> List[Dict]:
    """Search for structural code patterns.

    *pattern* can be either:
    - A named pattern key  (e.g. ``'function'``, ``'class'``)
    - A raw ast-grep pattern string

    Returns a list of ``{"path", "line", "kind", "text"}`` dicts.
    """
    if not cfg.ENABLE_AST_GREP:
        return _python_fallback(pattern, directory, max_results)

    sg = _find_ast_grep()
    if not sg:
        return _python_fallback(pattern, directory, max_results)

    lang = language or _detect_language(directory)
    pattern_str = _resolve_pattern(pattern, lang)

    return _run_ast_grep(sg, pattern_str, lang, directory, max_results)


def _detect_language(directory: str) -> str:
    """Detect primary language by counting extension occurrences."""
    py = js = ts = 0
    for root, dirs, files in os.walk(directory):
        dirs[:] = [
            d for d in dirs
            if d not in {"node_modules", ".git", "__pycache__", "venv", ".venv"}
        ]
        for f in files:
            if f.endswith(".py"):
                py += 1
            elif f.endswith((".ts", ".tsx")):
                ts += 1
            elif f.endswith((".js", ".jsx", ".mjs")):
                js += 1
        if py + js + ts > 200:
            break

    if py >= js and py >= ts:
        return "python"
    if ts >= js:
        return "typescript"
    return "javascript"


def _resolve_pattern(pattern: str, lang: str) -> str:
    """Resolve a named pattern to an ast-grep pattern string."""
    if lang == "python":
        return _PYTHON_PATTERNS.get(pattern, pattern)
    if lang in ("javascript", "typescript", "jsx", "tsx"):
        return _JS_PATTERNS.get(pattern, pattern)
    return pattern


def _run_ast_grep(
    sg_bin: str,
    pattern: str,
    lang: str,
    directory: str,
    max_results: int,
) -> List[Dict]:
    """Invoke ast-grep and parse its JSON output."""
    cmd = [
        sg_bin, "run",
        "--pattern", pattern,
        "--lang", lang,
        "--json",
        directory,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
    except (subprocess.TimeoutExpired, OSError):
        return _python_fallback(pattern, directory, max_results)

    if result.returncode not in (0, 1) or not result.stdout.strip():
        return _python_fallback(pattern, directory, max_results)

    matches: List[Dict] = []
    try:
        data = json.loads(result.stdout)
        items = data if isinstance(data, list) else data.get("matches", [])
        for item in items[:max_results]:
            file_path = item.get("file", item.get("path", ""))
            start = item.get("range", {}).get("start", {})
            line = start.get("line", 0)
            matches.append({
                "path": file_path,
                "line": line,
                "text": item.get("text", ""),
                "kind": "ast_match",
            })
    except (json.JSONDecodeError, AttributeError, TypeError):
        # Fallback: parse text output
        for line in (result.stdout or "").splitlines()[:max_results]:
            if line.strip():
                matches.append({"path": line, "line": 0, "text": line, "kind": "ast_match"})

    return matches


def _python_fallback(
    pattern: str,
    directory: str,
    max_results: int,
) -> List[Dict]:
    """Python AST fallback for when ast-grep is unavailable."""
    kind_map = {
        "function": {"function"},
        "async_function": {"function"},
        "class": {"class"},
        "method": {"method"},
        "decorator": {"function", "method"},
    }
    target_kinds = kind_map.get(pattern)

    try:
        from agent import repo_index

        repo_index.build_index(directory)
        results: List[Dict] = []

        for name, hits in list(repo_index._index.items()):
            for rel_path, line, kind in hits:
                if target_kinds is None or kind in target_kinds:
                    full_path = os.path.join(directory, rel_path)
                    results.append({
                        "path": full_path,
                        "line": line,
                        "text": f"{kind} {name}",
                        "kind": kind,
                    })
                    if len(results) >= max_results:
                        return results
        return results
    except Exception:
        return []


def available() -> bool:
    """Return True if ast-grep/sg is installed."""
    return _find_ast_grep() is not None

"""Static analysis quality rules — no LLM, deterministic, fast.

Scans a workspace directory and returns violations (penalized) and
rewards (bonused), producing a 0-100 code quality score.
"""

from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path
from typing import Optional

from .evaluation_metrics import RuleViolation, RuleReward


# ── Constants ─────────────────────────────────────────────────────────────────

_MAX_FN_LINES = 50       # penalize functions longer than this
_MAX_FILE_LINES = 400    # penalize files longer than this
_STARTING_SCORE = 80     # base before penalties/rewards

_SECRET_PATTERN = re.compile(
    r'(password|secret|api_key|apikey|token|private_key)\s*=\s*["\'][^"\']{6,}',
    re.IGNORECASE,
)
_TODO_PATTERN = re.compile(r'\b(TODO|FIXME|HACK|XXX)\b')
_CONSOLE_LOG = re.compile(r'\bconsole\.log\s*\(')
_VAR_DECL = re.compile(r'\bvar\s+\w+')
_DUPLICATE_BLANK = re.compile(r'\n{3,}')  # 3+ consecutive blank lines


# ── Python AST analysis ───────────────────────────────────────────────────────

def _py_violations(path: Path, src: str) -> tuple[list[RuleViolation], list[RuleReward]]:
    viols: list[RuleViolation] = []
    rewards: list[RuleReward] = []
    fname = str(path)

    try:
        tree = ast.parse(src, filename=fname)
    except SyntaxError as exc:
        viols.append(RuleViolation(
            rule_id="PY001",
            severity="error",
            file=fname,
            line=exc.lineno or 1,
            message=f"Syntax error: {exc.msg}",
            penalty=30,
        ))
        return viols, rewards

    # Track all names defined and used
    defined_names: set[str] = set()
    used_names: set[str] = set()

    class _Visitor(ast.NodeVisitor):
        def visit_Import(self, node: ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                defined_names.add(name)
            self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom):
            for alias in node.names:
                name = alias.asname or alias.name
                defined_names.add(name)
            self.generic_visit(node)

        def visit_Name(self, node: ast.Name):
            used_names.add(node.id)
            self.generic_visit(node)

        def visit_FunctionDef(self, node):
            _check_function(node)
            self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node):
            if not ast.get_docstring(node):
                # Only penalize public classes
                if not node.name.startswith("_"):
                    viols.append(RuleViolation(
                        rule_id="PY004",
                        severity="info",
                        file=fname,
                        line=node.lineno,
                        message=f"Class '{node.name}' missing docstring",
                        penalty=1,
                    ))
            self.generic_visit(node)

    def _check_function(node):
        lines = (node.end_lineno or node.lineno) - node.lineno + 1
        if lines > _MAX_FN_LINES:
            viols.append(RuleViolation(
                rule_id="PY002",
                severity="warning",
                file=fname,
                line=node.lineno,
                message=f"Function '{node.name}' is {lines} lines (max {_MAX_FN_LINES})",
                penalty=3,
            ))
        has_doc = bool(ast.get_docstring(node))
        has_return_ann = node.returns is not None
        has_arg_anns = any(a.annotation for a in node.args.args)
        if has_doc or (has_return_ann and has_arg_anns):
            rewards.append(RuleReward(
                rule_id="PY_TYPED",
                file=fname,
                message=f"Function '{node.name}' has annotations/docstring",
                bonus=1,
            ))
        # Rough cyclomatic complexity
        complexity = sum(
            1 for n in ast.walk(node)
            if isinstance(n, (ast.If, ast.For, ast.While, ast.ExceptHandler,
                               ast.With, ast.Assert, ast.comprehension))
        )
        if complexity > 15:
            viols.append(RuleViolation(
                rule_id="PY003",
                severity="warning",
                file=fname,
                line=node.lineno,
                message=f"Function '{node.name}' complexity ~{complexity} (threshold 15)",
                penalty=4,
            ))

    _Visitor().visit(tree)

    # Unused imports
    for name in defined_names - used_names:
        viols.append(RuleViolation(
            rule_id="PY005",
            severity="info",
            file=fname,
            line=1,
            message=f"Possibly unused import: '{name}'",
            penalty=1,
        ))

    # Module-level docstring
    if ast.get_docstring(tree):
        rewards.append(RuleReward(
            rule_id="PY_MODULE_DOC",
            file=fname,
            message="Module has docstring",
            bonus=2,
        ))

    return viols, rewards


# ── Line-level checks (language-agnostic) ────────────────────────────────────

def _line_violations(path: Path, src: str) -> tuple[list[RuleViolation], list[RuleReward]]:
    viols: list[RuleViolation] = []
    rewards: list[RuleReward] = []
    fname = str(path)
    lines = src.splitlines()

    # File too long
    if len(lines) > _MAX_FILE_LINES:
        viols.append(RuleViolation(
            rule_id="GEN001",
            severity="warning",
            file=fname,
            line=1,
            message=f"File has {len(lines)} lines (max {_MAX_FILE_LINES})",
            penalty=5,
        ))

    for i, line in enumerate(lines, 1):
        # Hardcoded secrets
        if _SECRET_PATTERN.search(line):
            viols.append(RuleViolation(
                rule_id="SEC001",
                severity="error",
                file=fname,
                line=i,
                message="Possible hardcoded secret/password",
                penalty=15,
            ))

        # TODO/FIXME
        if _TODO_PATTERN.search(line):
            viols.append(RuleViolation(
                rule_id="GEN002",
                severity="info",
                file=fname,
                line=i,
                message="TODO/FIXME comment — incomplete work",
                penalty=1,
            ))

    # JS/TS specific
    ext = path.suffix.lower()
    if ext in (".js", ".jsx", ".ts", ".tsx"):
        for i, line in enumerate(lines, 1):
            if _CONSOLE_LOG.search(line) and "test" not in fname.lower():
                viols.append(RuleViolation(
                    rule_id="JS001",
                    severity="warning",
                    file=fname,
                    line=i,
                    message="console.log in non-test file",
                    penalty=2,
                ))
            if _VAR_DECL.search(line):
                viols.append(RuleViolation(
                    rule_id="JS002",
                    severity="info",
                    file=fname,
                    line=i,
                    message="Use let/const instead of var",
                    penalty=1,
                ))

    # Excessive blank lines
    if _DUPLICATE_BLANK.search(src):
        viols.append(RuleViolation(
            rule_id="GEN003",
            severity="info",
            file=fname,
            line=1,
            message="3+ consecutive blank lines — poor formatting",
            penalty=1,
        ))

    return viols, rewards


# ── JSON validation ────────────────────────────────────────────────────────────

def _json_violations(path: Path, src: str) -> list[RuleViolation]:
    try:
        json.loads(src)
        return []
    except json.JSONDecodeError as exc:
        return [RuleViolation(
            rule_id="JSON001",
            severity="error",
            file=str(path),
            line=exc.lineno,
            message=f"Invalid JSON: {exc.msg}",
            penalty=20,
        )]


# ── Test coverage heuristic ───────────────────────────────────────────────────

def _check_test_presence(workspace: str, source_files: list[Path]) -> list[RuleReward]:
    rewards = []
    ws = Path(workspace)
    test_files = set()
    for p in ws.rglob("test_*.py"):
        test_files.add(p)
    for p in ws.rglob("*_test.py"):
        test_files.add(p)
    for p in ws.rglob("*.test.*"):
        test_files.add(p)
    for p in ws.rglob("*.spec.*"):
        test_files.add(p)

    if test_files:
        rewards.append(RuleReward(
            rule_id="TEST_PRESENT",
            file=".",
            message=f"Test files found: {len(test_files)}",
            bonus=10,
        ))
    else:
        pass  # penalized via testing dimension, not here

    return rewards


# ── Architecture rewards ──────────────────────────────────────────────────────

def _check_architecture(workspace: str) -> list[RuleReward]:
    rewards = []
    ws = Path(workspace)
    py_files = list(ws.rglob("*.py"))
    non_init = [f for f in py_files if f.name != "__init__.py"]

    # Has package structure
    init_files = list(ws.rglob("__init__.py"))
    if init_files:
        rewards.append(RuleReward(
            rule_id="ARCH_PACKAGE",
            file=".",
            message="Uses Python package structure (__init__.py)",
            bonus=3,
        ))

    # Separation of concerns: has models/routes/crud style naming
    names = {f.stem.lower() for f in non_init}
    soc_hints = {"models", "routes", "crud", "schema", "service",
                 "handler", "controller", "repository", "utils"}
    matched = soc_hints & names
    if len(matched) >= 2:
        rewards.append(RuleReward(
            rule_id="ARCH_SOC",
            file=".",
            message=f"Separation of concerns: {sorted(matched)}",
            bonus=5,
        ))

    # Has requirements.txt or package.json
    if (ws / "requirements.txt").exists() or (ws / "package.json").exists():
        rewards.append(RuleReward(
            rule_id="ARCH_DEPS",
            file=".",
            message="Dependency manifest present",
            bonus=2,
        ))

    return rewards


# ── Main entry point ─────────────────────────────────────────────────────────

_SKIP_DIRS = frozenset(
    {"node_modules", ".git", "__pycache__", ".venv", "venv", ".tox",
     "dist", "build", ".mypy_cache", ".pytest_cache"}
)
_SKIP_EXTS = frozenset(
    {".pyc", ".pyo", ".so", ".dll", ".exe", ".png", ".jpg", ".jpeg",
     ".gif", ".ico", ".woff", ".woff2", ".ttf", ".eot", ".map",
     ".lock", ".sum"}
)
_MAX_FILE_SIZE = 200_000  # bytes


def analyze_workspace(workspace: str) -> tuple[list[RuleViolation], list[RuleReward], int]:
    """Scan workspace and return (violations, rewards, score_0_100).

    Score starts at _STARTING_SCORE.  Penalties subtract, rewards add.
    Clamped to [0, 100].
    """
    ws = Path(workspace)
    all_viols: list[RuleViolation] = []
    all_rewards: list[RuleReward] = []
    source_files: list[Path] = []

    for root, dirs, files in os.walk(ws):
        # Prune ignored dirs in-place
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            p = Path(root) / fname
            if p.suffix.lower() in _SKIP_EXTS:
                continue
            if p.stat().st_size > _MAX_FILE_SIZE:
                continue
            source_files.append(p)

    for p in source_files:
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        ext = p.suffix.lower()
        lv, lr = _line_violations(p, src)
        all_viols.extend(lv)
        all_rewards.extend(lr)

        if ext == ".py":
            v2, r2 = _py_violations(p, src)
            all_viols.extend(v2)
            all_rewards.extend(r2)
        elif ext == ".json":
            all_viols.extend(_json_violations(p, src))

    # Test presence
    all_rewards.extend(_check_test_presence(workspace, source_files))
    # Architecture
    all_rewards.extend(_check_architecture(workspace))

    total_penalty = sum(v.penalty for v in all_viols)
    total_bonus = min(sum(r.bonus for r in all_rewards), 20)  # cap bonus at +20
    score = max(0, min(100, _STARTING_SCORE - total_penalty + total_bonus))

    return all_viols, all_rewards, score


def security_score_from_violations(violations: list[RuleViolation]) -> int:
    """Derive a 0-100 security score from SEC* violations."""
    sec_penalty = sum(v.penalty for v in violations if v.rule_id.startswith("SEC"))
    return max(0, 100 - sec_penalty * 3)  # each sec violation costs triple

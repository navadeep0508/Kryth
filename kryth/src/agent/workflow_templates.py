"""Deterministic workflow templates for KRYTH.

For well-understood task shapes (create file, rename, scaffold, install, run tests),
the model fills in parameters and this module executes the workflow directly —
no multi-turn LLM reasoning required. Falls back gracefully when no template matches.

Usage (in agent_loop.py, before the first LLM call):
    from agent.workflow_templates import match_workflow, execute_workflow
    wf = match_workflow(user_task)
    if wf:
        result = execute_workflow(wf, session)
        if result.success:
            return early ...
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class WorkflowResult:
    success: bool
    summary: str
    files_created: List[str] = field(default_factory=list)
    files_modified: List[str] = field(default_factory=list)
    commands_run: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    skipped: bool = False


@dataclass
class WorkflowTemplate:
    name: str
    description: str
    pattern: re.Pattern
    handler: Callable[["WorkflowTemplate", re.Match, Any], WorkflowResult]
    # If True, this template can exit the agent loop without any LLM call.
    # If False, it's a partial accelerator that still calls the LLM for details.
    zero_llm: bool = False


# ── Handlers ─────────────────────────────────────────────────────────────────

def _handle_create_hello(tmpl: WorkflowTemplate, m: re.Match, ctx: Any) -> WorkflowResult:
    lang = (m.group("lang") or "py").lower().lstrip(".")
    name = m.group("name") if m.group("name") else f"hello.{lang}"
    if "." not in name:
        name = f"{name}.{lang}"

    templates: dict[str, str] = {
        "py":   'print("Hello, World!")\n',
        "js":   'console.log("Hello, World!");\n',
        "ts":   'console.log("Hello, World!");\n',
        "rb":   'puts "Hello, World!"\n',
        "go":   'package main\nimport "fmt"\nfunc main() {\n\tfmt.Println("Hello, World!")\n}\n',
        "rs":   'fn main() {\n    println!("Hello, World!");\n}\n',
        "c":    '#include <stdio.h>\nint main() {\n    printf("Hello, World!\\n");\n    return 0;\n}\n',
        "cpp":  '#include <iostream>\nint main() {\n    std::cout << "Hello, World!" << std::endl;\n}\n',
        "java": 'public class Hello {\n    public static void main(String[] a) {\n        System.out.println("Hello, World!");\n    }\n}\n',
        "sh":   '#!/bin/bash\necho "Hello, World!"\n',
    }
    content = templates.get(lang, f'# hello.{lang}\n')
    try:
        with open(name, "w", encoding="utf-8") as f:
            f.write(content)
        return WorkflowResult(success=True, summary=f"Created {name}", files_created=[name])
    except Exception as e:
        return WorkflowResult(success=False, summary=f"Failed: {e}", errors=[str(e)])


def _handle_rename(tmpl: WorkflowTemplate, m: re.Match, ctx: Any) -> WorkflowResult:
    src = m.group("src").strip()
    dst = m.group("dst").strip()
    if not os.path.exists(src):
        return WorkflowResult(success=False, summary=f"Source not found: {src}", errors=[f"No such file: {src}"])
    try:
        shutil.move(src, dst)
        return WorkflowResult(success=True, summary=f"Renamed {src} → {dst}",
                              files_modified=[dst])
    except Exception as e:
        return WorkflowResult(success=False, summary=str(e), errors=[str(e)])


def _handle_move(tmpl: WorkflowTemplate, m: re.Match, ctx: Any) -> WorkflowResult:
    src = m.group("src").strip()
    dst = m.group("dst").strip()
    if not os.path.exists(src):
        return WorkflowResult(success=False, summary=f"Source not found: {src}", errors=[f"No such file: {src}"])
    try:
        os.makedirs(dst if os.path.isdir(dst) else os.path.dirname(dst) or ".", exist_ok=True)
        shutil.move(src, dst)
        return WorkflowResult(success=True, summary=f"Moved {src} → {dst}",
                              files_modified=[dst])
    except Exception as e:
        return WorkflowResult(success=False, summary=str(e), errors=[str(e)])


def _handle_delete(tmpl: WorkflowTemplate, m: re.Match, ctx: Any) -> WorkflowResult:
    target = m.group("target").strip()
    if not os.path.exists(target):
        return WorkflowResult(success=True, summary=f"{target} already absent")
    try:
        if os.path.isdir(target):
            shutil.rmtree(target)
        else:
            os.remove(target)
        return WorkflowResult(success=True, summary=f"Deleted {target}")
    except Exception as e:
        return WorkflowResult(success=False, summary=str(e), errors=[str(e)])


def _run_cmd(cmd: str, cwd: str | None = None) -> Tuple[bool, str]:
    try:
        r = subprocess.run(shlex.split(cmd), capture_output=True, text=True,
                           timeout=120, cwd=cwd)
        out = (r.stdout + r.stderr).strip()
        return r.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, str(e)


def _handle_install_dep(tmpl: WorkflowTemplate, m: re.Match, ctx: Any) -> WorkflowResult:
    pkg = m.group("pkg").strip()
    mgr = m.group("mgr") if m.group("mgr") else None

    if mgr:
        mgr = mgr.lower()
    elif os.path.exists("package.json"):
        mgr = "npm"
    elif os.path.exists("pyproject.toml") or os.path.exists("requirements.txt"):
        mgr = "pip"
    else:
        mgr = "pip"

    cmd_map = {
        "npm":  f"npm install {pkg}",
        "yarn": f"yarn add {pkg}",
        "pnpm": f"pnpm add {pkg}",
        "pip":  f"{sys.executable} -m pip install {pkg}",
        "uv":   f"uv pip install {pkg}",
        "cargo": f"cargo add {pkg}",
        "go":   f"go get {pkg}",
    }
    cmd = cmd_map.get(mgr, f"{mgr} install {pkg}")
    ok, out = _run_cmd(cmd)
    return WorkflowResult(
        success=ok, summary=f"{cmd} → {'ok' if ok else 'FAILED'}",
        commands_run=[cmd],
        errors=[] if ok else [out[:200]],
    )


def _handle_run_tests(tmpl: WorkflowTemplate, m: re.Match, ctx: Any) -> WorkflowResult:
    path = (m.group("path") if m.group("path") else ".").strip() or "."
    # Detect runner
    if os.path.exists("pytest.ini") or os.path.exists("pyproject.toml") or \
       os.path.exists("setup.cfg") or os.path.exists("conftest.py"):
        cmd = f"{sys.executable} -m pytest {path} -q --tb=short"
    elif os.path.exists("package.json"):
        cmd = "npm test"
    elif os.path.exists("Makefile"):
        cmd = "make test"
    else:
        cmd = f"{sys.executable} -m pytest {path} -q --tb=short"
    ok, out = _run_cmd(cmd)
    return WorkflowResult(
        success=ok, summary=out[:300] if out else ("Tests passed" if ok else "Tests failed"),
        commands_run=[cmd], errors=[] if ok else [out[:300]],
    )


def _handle_lint(tmpl: WorkflowTemplate, m: re.Match, ctx: Any) -> WorkflowResult:
    path = (m.group("path") if m.group("path") else ".").strip() or "."
    cmd = f"{sys.executable} -m ruff check {path} --fix" if _has("ruff") else \
          f"{sys.executable} -m flake8 {path}"
    ok, out = _run_cmd(cmd)
    return WorkflowResult(
        success=ok, summary=out[:300] or ("Lint ok" if ok else "Lint failed"),
        commands_run=[cmd], errors=[] if ok else [out[:300]],
    )


def _has(tool: str) -> bool:
    return shutil.which(tool) is not None or _run_cmd(f"{sys.executable} -m {tool} --version")[0]


def _handle_mkdir(tmpl: WorkflowTemplate, m: re.Match, ctx: Any) -> WorkflowResult:
    path = m.group("path").strip()
    try:
        os.makedirs(path, exist_ok=True)
        return WorkflowResult(success=True, summary=f"Created directory {path}")
    except Exception as e:
        return WorkflowResult(success=False, summary=str(e), errors=[str(e)])


# ── Template registry ─────────────────────────────────────────────────────────

_TEMPLATES: List[WorkflowTemplate] = [
    WorkflowTemplate(
        name="create_hello",
        description="Create a hello-world file in any language",
        pattern=re.compile(
            r"(?i)create\s+(?P<name>[\w.-]*(?:hello|greet|main|app)?[\w.-]*\.(?P<lang>\w+)|"
            r"(?P<name2>[\w.-]+))\s*(?:that\s+prints?\s+.+)?$"
        ),
        handler=_handle_create_hello,
        zero_llm=True,
    ),
    WorkflowTemplate(
        name="rename_file",
        description="Rename or move a file to a new name",
        pattern=re.compile(
            r"(?i)rename\s+(?P<src>['\"]?[\w./ \\-]+['\"]?)\s+(?:to|as|→)\s+(?P<dst>['\"]?[\w./ \\-]+['\"]?)$"
        ),
        handler=_handle_rename,
        zero_llm=True,
    ),
    WorkflowTemplate(
        name="move_file",
        description="Move a file or directory to another location",
        pattern=re.compile(
            r"(?i)move\s+(?P<src>['\"]?[\w./ \\-]+['\"]?)\s+(?:to|into)\s+(?P<dst>['\"]?[\w./ \\-]+['\"]?)$"
        ),
        handler=_handle_move,
        zero_llm=True,
    ),
    WorkflowTemplate(
        name="delete_file",
        description="Delete a file or directory",
        pattern=re.compile(
            r"(?i)(?:delete|remove|rm)\s+(?:the\s+)?(?:file\s+)?(?P<target>['\"]?[\w./ \\-]+['\"]?)$"
        ),
        handler=_handle_delete,
        zero_llm=True,
    ),
    WorkflowTemplate(
        name="install_dependency",
        description="Install a package/library dependency",
        pattern=re.compile(
            r"(?i)(?:install|add)\s+(?:(?P<mgr>npm|yarn|pnpm|pip|uv|cargo|go)\s+)?(?:package\s+|dep\s+|dependency\s+)?(?P<pkg>[\w@/.-]+(?:\s+[\w@/.-]+)*)$"
        ),
        handler=_handle_install_dep,
        zero_llm=True,
    ),
    WorkflowTemplate(
        name="run_tests",
        description="Run the test suite",
        pattern=re.compile(
            r"(?i)run\s+(?:all\s+)?tests?(?:\s+(?:in|for|on)\s+(?P<path>[\w./ \\-]+))?$"
        ),
        handler=_handle_run_tests,
        zero_llm=True,
    ),
    WorkflowTemplate(
        name="lint_format",
        description="Lint or format the codebase",
        pattern=re.compile(
            r"(?i)(?:lint|format|check\s+style|run\s+linter)(?:\s+(?P<path>[\w./ \\-]+))?$"
        ),
        handler=_handle_lint,
        zero_llm=True,
    ),
    WorkflowTemplate(
        name="mkdir",
        description="Create a directory",
        pattern=re.compile(
            r"(?i)(?:create|make|mkdir)\s+(?:a\s+)?(?:directory|folder|dir)\s+(?:called\s+|named\s+)?(?P<path>['\"]?[\w./ \\-]+['\"]?)$"
        ),
        handler=_handle_mkdir,
        zero_llm=True,
    ),
]


# ── Public API ────────────────────────────────────────────────────────────────

def match_workflow(task: str) -> Optional[Tuple[WorkflowTemplate, re.Match]]:
    """Return the first matching (template, match) for task, or None."""
    task = task.strip()
    for tmpl in _TEMPLATES:
        m = tmpl.pattern.match(task)
        if m:
            return tmpl, m
    return None


def execute_workflow(
    tmpl_match: Tuple[WorkflowTemplate, re.Match],
    ctx: Any = None,
) -> WorkflowResult:
    """Execute a matched workflow template and return its result."""
    tmpl, m = tmpl_match
    try:
        return tmpl.handler(tmpl, m, ctx)
    except Exception as e:
        return WorkflowResult(success=False, summary=f"workflow {tmpl.name} failed: {e}",
                              errors=[str(e)])


def try_workflow(task: str, ctx: Any = None) -> Optional[WorkflowResult]:
    """Convenience: match + execute in one call. Returns None if no match."""
    hit = match_workflow(task)
    if hit is None:
        return None
    return execute_workflow(hit, ctx)

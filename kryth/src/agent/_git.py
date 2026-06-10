"""Git-aware workflow tool.

A single ``git_op`` entry point dispatches structured git actions:

    status         — porcelain summary + paths
    diff           — unified diff (optionally for a ref or staged)
    log            — short oneline log of recent commits
    current_branch — print just the current branch
    branch         — create / switch branches
    commit         — stage + commit with message

Each action returns text the agent can reason about. The point isn't to
replace ``run_command`` — it's to give the agent a small, predictable
surface so it doesn't have to invent flags every time, and so the
permission layer can grant ``git_op:status`` without granting full
shell access. Mutating actions (branch, commit) live behind the same
permission machinery as anything else; the spec's docstring makes the
mutation explicit so profiles can deny them.

Errors flow through the ``[ERROR <CODE>] ...`` envelope so the model
can distinguish "not a git repo" from "command crashed".
"""

from __future__ import annotations

import subprocess
from typing import Iterable

from agent.tools._results import err


GIT_TIMEOUT_SECONDS = 30


def _git(args: list[str], cwd: str | None = None) -> tuple[int, str, str]:
    """Run ``git <args>`` and return ``(rc, stdout, stderr)``.

    UTF-8 with replacement so encoded-bytes commit messages don't blow
    up the decoder. A timeout guards against runaway git operations.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return 127, "", "git executable not found on PATH"
    except subprocess.TimeoutExpired:
        return 124, "", f"git {' '.join(args)} timed out after {GIT_TIMEOUT_SECONDS}s"
    except Exception as e:
        return 1, "", f"{type(e).__name__}: {e}"
    return proc.returncode, proc.stdout, proc.stderr


def _ensure_repo() -> str | None:
    rc, _, stderr = _git(["rev-parse", "--is-inside-work-tree"])
    if rc != 0:
        return err(
            "NOT_FOUND",
            "not inside a git repository",
            stderr.strip(),
        )
    return None


def _action_status() -> str:
    failure = _ensure_repo()
    if failure:
        return failure

    rc, out, errtext = _git(["status", "--porcelain=v1", "--branch"])
    if rc != 0:
        return err("EXEC_FAILED", "git status failed", errtext.strip())

    lines = out.splitlines()
    branch_line = ""
    files: list[str] = []
    counts = {"staged": 0, "modified": 0, "untracked": 0, "unmerged": 0}
    for ln in lines:
        if ln.startswith("##"):
            branch_line = ln[3:].strip()
            continue
        if len(ln) < 3:
            continue
        code = ln[:2]
        path = ln[3:]
        files.append(f"  {code}  {path}")
        if code == "??":
            counts["untracked"] += 1
        elif "U" in code or code in ("DD", "AA"):
            counts["unmerged"] += 1
        else:
            if code[0] not in " ?":
                counts["staged"] += 1
            if code[1] not in " ?":
                counts["modified"] += 1

    head = f"on {branch_line}" if branch_line else "(no branch info)"
    summary = (
        f"staged={counts['staged']} modified={counts['modified']} "
        f"untracked={counts['untracked']} unmerged={counts['unmerged']}"
    )
    body = "\n".join(files) if files else "  (clean working tree)"
    return f"{head}\n{summary}\n{body}"


def _action_diff(*, staged: bool = False, rev: str | None = None,
                 paths: Iterable[str] | None = None) -> str:
    failure = _ensure_repo()
    if failure:
        return failure

    args = ["diff"]
    if staged:
        args.append("--cached")
    if rev:
        args.append(rev)
    args.extend(["--stat", "--patch", "--no-color"])
    if paths:
        args.append("--")
        args.extend(p for p in paths if isinstance(p, str))

    rc, out, errtext = _git(args)
    if rc != 0:
        return err("EXEC_FAILED", "git diff failed", errtext.strip())
    if not out.strip():
        return "(no changes)"
    if len(out) > 20000:
        out = out[:20000] + "\n... [truncated; pass narrower paths or use 'log' for an overview]"
    return out


def _action_log(*, limit: int = 15, rev_range: str | None = None) -> str:
    failure = _ensure_repo()
    if failure:
        return failure

    fmt = "%h %ad %s  [%an]"
    args = ["log", f"-n{max(1, min(int(limit), 200))}", "--date=short",
            f"--pretty=format:{fmt}"]
    if rev_range:
        args.append(rev_range)
    rc, out, errtext = _git(args)
    if rc != 0:
        return err("EXEC_FAILED", "git log failed", errtext.strip())
    return out or "(no commits)"


def _action_current_branch() -> str:
    failure = _ensure_repo()
    if failure:
        return failure
    rc, out, errtext = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    if rc != 0:
        return err("EXEC_FAILED", "could not read current branch", errtext.strip())
    return out.strip() or "(detached HEAD)"


def _action_branch(*, name: str | None = None, base: str | None = None,
                   switch: bool = True) -> str:
    failure = _ensure_repo()
    if failure:
        return failure

    if not name:
        rc, out, errtext = _git(["branch", "--list", "--sort=-committerdate"])
        if rc != 0:
            return err("EXEC_FAILED", "git branch failed", errtext.strip())
        return out or "(no branches)"

    if not isinstance(name, str) or not name.strip():
        return err("BAD_ARGS", "branch.name must be a non-empty string")

    args = ["switch"] if switch else ["branch"]
    if switch:
        args.append("-c")
    args.append(name.strip())
    if base:
        args.append(base.strip())

    rc, out, errtext = _git(args)
    if rc != 0:
        return err(
            "EXEC_FAILED",
            f"could not create branch {name}",
            errtext.strip() or out.strip(),
        )
    tail = out.strip() or errtext.strip() or "ok"
    return f"branch ready: {name}\n{tail}"


def _action_commit(*, message: str, paths: Iterable[str] | None = None,
                   add_all: bool = False) -> str:
    failure = _ensure_repo()
    if failure:
        return failure

    if not isinstance(message, str) or not message.strip():
        return err("BAD_ARGS", "commit.message must be a non-empty string")

    if add_all:
        rc, _, errtext = _git(["add", "-A"])
        if rc != 0:
            return err("EXEC_FAILED", "git add -A failed", errtext.strip())
    elif paths:
        path_list = [p for p in paths if isinstance(p, str) and p.strip()]
        if path_list:
            rc, _, errtext = _git(["add", "--", *path_list])
            if rc != 0:
                return err("EXEC_FAILED", "git add failed", errtext.strip())

    rc, out, errtext = _git(["diff", "--cached", "--stat"])
    if rc != 0:
        return err("EXEC_FAILED", "could not read staged diff", errtext.strip())
    if not out.strip():
        return err("BAD_ARGS", "nothing staged to commit")

    short_stat = out.strip().splitlines()[-1] if out.strip() else ""

    rc, out, errtext = _git(["commit", "-m", message.strip()])
    if rc != 0:
        return err("EXEC_FAILED", "git commit failed", errtext.strip() or out.strip())

    rc2, sha, _ = _git(["rev-parse", "--short", "HEAD"])
    sha_text = sha.strip() if rc2 == 0 else "(unknown sha)"
    return f"committed {sha_text} — {short_stat}"


def git_op(
    action: str,
    *,
    paths=None,
    message: str | None = None,
    name: str | None = None,
    base: str | None = None,
    switch: bool = True,
    rev: str | None = None,
    rev_range: str | None = None,
    staged: bool = False,
    add_all: bool = False,
    limit: int = 15,
):
    """Run a structured git action. See ``_specs.py`` for the full schema.

    Mutating actions (``branch``, ``commit``) flow through the same
    permission gate as any other tool — the caller layer enforces
    user / profile policy.
    """
    if not isinstance(action, str):
        return err("BAD_ARGS", "git_op: action must be a string")
    act = action.strip().lower()

    if isinstance(paths, str):
        paths_list = [paths]
    elif isinstance(paths, (list, tuple)):
        paths_list = list(paths)
    elif paths is None:
        paths_list = None
    else:
        return err("BAD_ARGS", "git_op: paths must be a string or list")

    if act == "status":
        return _action_status()
    if act == "diff":
        return _action_diff(staged=bool(staged), rev=rev, paths=paths_list)
    if act == "log":
        return _action_log(limit=int(limit), rev_range=rev_range)
    if act == "current_branch":
        return _action_current_branch()
    if act == "branch":
        return _action_branch(name=name, base=base, switch=bool(switch))
    if act == "commit":
        if not message:
            return err("BAD_ARGS", "git_op: commit requires a 'message'")
        return _action_commit(
            message=message,
            paths=paths_list,
            add_all=bool(add_all),
        )

    return err(
        "BAD_ARGS",
        f"unknown git action: {action!r}",
        "valid: status, diff, log, current_branch, branch, commit",
    )


__all__ = ["git_op"]

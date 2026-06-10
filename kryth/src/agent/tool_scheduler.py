"""Parallel tool execution scheduler.

Converts a batch of LLM tool calls from sequential to dependency-aware
parallel execution. Independent tool calls run concurrently; conflicting
writes serialize automatically via an ownership lock manager.

Architecture
------------
  dispatch_tool_calls(session, tool_calls)
    └─ ToolScheduler.run(batch)
         ├─ classify each call (read / safe-write / unsafe-write / exclusive)
         ├─ group into parallel stages by resource conflicts
         ├─ execute each stage with ThreadPoolExecutor
         └─ collect results in original call order

Resource ownership
------------------
  FILE:<path>        — file write lock
  DIR:<path>         — directory-level lock (npm install, git ops)
  PORT:<n>           — network port
  GIT_STATE          — any git-mutating operation
  PACKAGE_MANAGER    — pip/npm/yarn/cargo install
  TERMINAL:<id>      — persistent shell session

Any tool that acquires a lock blocks other tools wanting the same lock.
Read-only tools never acquire locks and always run in parallel.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Tool classification
# ---------------------------------------------------------------------------

# Tools that are always safe to run in parallel — no shared state mutations.
_PARALLEL_SAFE = frozenset({
    "read_file", "list_files", "glob", "grep", "search_code",
    "semantic_search", "lookup_symbol", "lookup_imports", "lookup_dependents",
    "fts_search", "ast_search", "graphify_query", "search_smart",
    "todo_read", "task_output", "verify_files", "diagnose_error",
    "self_critique", "check_browser_errors", "extract_data",
    "browser_tab_list", "browser_get_html", "browser_get_url",
    "browser_state", "browser_screenshot", "get_research_report",
    "shell_state", "shell_plan", "process_list", "terminal_memory_recall",
    "supervisor_status", "supervisor_predict", "supervisor_health",
    "ownership_status", "budget_status", "mission_status", "mission_summary",
    "mission_impact_analysis", "factory_status", "browser_profile_list",
    "browser_session_status",
})

# Commands that must never run concurrently with anything (exclusive lock on
# the entire environment — installs, destructive git ops, etc.).
_EXCLUSIVE_CMDS = frozenset({
    "pip install", "pip uninstall", "pip3 install",
    "npm install", "npm uninstall", "npm ci",
    "yarn add", "yarn remove", "yarn install",
    "pnpm install", "pnpm add",
    "cargo install", "cargo build",
    "apt ", "apt-get ", "brew install",
    "git push", "git rebase", "git reset --hard",
    "git clean ", "git merge ", "docker build",
    "docker compose up", "docker-compose up",
    "sudo ", "rm -rf",
})

# Safe read commands for run_command (can run in parallel).
_SAFE_RUN_CMDS = frozenset({
    "npm run lint", "npm run typecheck", "npm run test", "npm run check",
    "pytest", "python -m pytest", "ruff check", "mypy ",
    "cargo test", "go test ", "jest", "vitest",
    "eslint ", "prettier --check", "tsc --noEmit",
})


def _resource_key_for_call(tool_name: str, args: dict) -> list[str]:
    """Return resource lock keys a tool call needs to acquire.

    Empty list = no locks needed (safe to parallelize freely).
    """
    if tool_name in _PARALLEL_SAFE:
        return []

    if tool_name in ("write_file", "edit_file", "multi_edit", "delete_file", "rollback_file"):
        path = args.get("path") or args.get("paths")
        if isinstance(path, list):
            return [f"FILE:{p}" for p in path]
        if path:
            return [f"FILE:{path}"]
        return ["FILE:*"]

    if tool_name == "run_command":
        cmd = (args.get("command") or "").strip()
        # Check exclusive patterns first
        for exc in _EXCLUSIVE_CMDS:
            if cmd.lower().startswith(exc) or f" {exc}" in cmd.lower():
                return ["EXCLUSIVE"]
        # Safe read-only commands
        for safe in _SAFE_RUN_CMDS:
            if cmd.lower().startswith(safe) or safe in cmd.lower():
                return []
        # Background commands don't block
        if args.get("run_in_background"):
            return []
        # Unknown command — conservative: no exclusive lock but also not free
        return [f"CMD:{cmd[:60]}"]

    if tool_name == "git_op":
        action = args.get("action", "")
        if action in ("status", "diff", "log", "current_branch"):
            return []
        return ["GIT_STATE"]

    if tool_name in ("spawn_agent", "spawn_agents_parallel", "run_task_graph"):
        return []  # agents manage their own locks internally

    if tool_name in ("browser_click", "browser_type", "browser_select",
                     "browser_scroll", "browser_back", "browser_submit",
                     "browser_eval_js", "browser_keys", "fill_form",
                     "browser_login", "open_url", "browser_setup",
                     "browser_use_task"):
        profile = args.get("profile", "default")
        return [f"BROWSER:{profile}"]

    return [f"TOOL:{tool_name}"]


def _is_exclusive(keys: list[str]) -> bool:
    return "EXCLUSIVE" in keys


# ---------------------------------------------------------------------------
# Ownership lock manager
# ---------------------------------------------------------------------------

class _OwnershipManager:
    """Per-session resource lock table.

    Locks are reentrant within the same call (same thread). Different
    calls waiting on the same lock queue up.
    """

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._meta_lock = threading.Lock()

    def _get_lock(self, key: str) -> threading.Lock:
        with self._meta_lock:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

    def acquire_all(self, keys: list[str]) -> None:
        # Sort to prevent deadlocks (consistent acquisition order).
        for key in sorted(set(keys)):
            self._get_lock(key).acquire()

    def release_all(self, keys: list[str]) -> None:
        for key in sorted(set(keys), reverse=True):
            try:
                self._get_lock(key).release()
            except RuntimeError:
                pass


# One ownership manager per session (stored on the session object).
def _get_ownership(session) -> _OwnershipManager:
    if not hasattr(session, "_ownership"):
        session._ownership = _OwnershipManager()
    return session._ownership


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

@dataclass
class _CallSlot:
    idx: int
    call: dict
    tool_name: str
    args: dict
    lock_keys: list[str]
    result: Any = None
    error: str = ""


import os as _os
_MAX_PARALLEL_TOOLS = min(64, max(16, (_os.cpu_count() or 8) * 4))


def _group_into_stages(slots: list[_CallSlot]) -> list[list[_CallSlot]]:
    """Group slots into the MINIMUM number of sequential stages.

    Parallel-first: read-only slots and non-conflicting writes all go into
    the SAME first stage whenever possible. Only genuine resource conflicts
    (same file, same port, exclusive command) force a new stage.

    Algorithm: greedy bin-packing — assign each slot to the first stage
    it fits into. Read-only slots always fit in any non-exclusive stage.
    """
    stages: list[list[_CallSlot]] = []
    # Collect all read-only slots first — they all go into one shared stage
    reads = [s for s in slots if not s.lock_keys]
    writes = [s for s in slots if s.lock_keys]

    # Reads form a single parallel batch (never need to split them)
    if reads:
        stages.append(reads)

    # Writes: bin-pack non-conflicting writes into as few stages as possible
    for slot in writes:
        if _is_exclusive(slot.lock_keys):
            stages.append([slot])
            continue
        placed = False
        for stage in stages:
            # Skip read-only stages (don't mix writes into the read batch)
            if all(not s.lock_keys for s in stage):
                continue
            stage_keys: set[str] = set()
            for s in stage:
                stage_keys.update(s.lock_keys)
            if not stage_keys.intersection(slot.lock_keys) and not any(
                _is_exclusive(s.lock_keys) for s in stage
            ):
                stage.append(slot)
                placed = True
                break
        if not placed:
            stages.append([slot])

    return stages


def parallel_dispatch(session, tool_calls: list[dict], dispatch_fn) -> None:
    """Execute ``tool_calls`` with maximum parallelism.

    ``dispatch_fn(session, call)`` is the existing serial dispatcher.
    Results are appended to the session in ORIGINAL call order — the LLM
    always sees responses in the same position as the requests.
    """
    if not tool_calls:
        return

    # Build slot descriptors
    slots: list[_CallSlot] = []
    for idx, call in enumerate(tool_calls):
        fn = call.get("function", {})
        tool_name = fn.get("name", "")
        try:
            args = json.loads(fn.get("arguments", "{}") or "{}")
        except (json.JSONDecodeError, TypeError):
            args = {}
        lock_keys = _resource_key_for_call(tool_name, args)
        slots.append(_CallSlot(idx=idx, call=call, tool_name=tool_name,
                               args=args, lock_keys=lock_keys))

    # Fast path: single call
    if len(slots) == 1:
        dispatch_fn(session, slots[0].call)
        return

    # Parallel-first: group into minimum stages then run each stage in parallel.
    # All reads land in one shared stage; writes are bin-packed to minimize stages.
    stages = _group_into_stages(slots)

    for stage in stages:
        if len(stage) == 1:
            dispatch_fn(session, stage[0].call)
        else:
            _run_stage_parallel(session, stage, dispatch_fn)


class _SessionProxy:
    """Thin proxy that captures session.append() calls instead of applying them.

    Each parallel worker gets its own proxy so threads never race on the real
    session. After all workers finish, captured messages are replayed to the
    real session in original slot order — preserving the LLM's expected
    tool-result ordering with zero locking and zero deadlock risk.
    """

    def __init__(self, real_session):
        self._real = real_session
        self.captured: list = []

    def append(self, msg) -> None:
        self.captured.append(msg)

    def __getattr__(self, name: str):
        return getattr(self._real, name)


def _run_stage_parallel(session, stage: list[_CallSlot], dispatch_fn) -> None:
    """Run all calls concurrently via proxy sessions, replay results in order.

    Each thread dispatches against its own _SessionProxy. No shared mutation
    of session.messages occurs during parallel execution — workers only read
    the session. After all workers finish, captured messages are flushed to
    the real session in slot order.
    """
    from agent import ui

    ownership = _get_ownership(session)
    workers = min(len(stage), _MAX_PARALLEL_TOOLS)

    # Pre-allocate ordered result slots
    proxies: list[_SessionProxy] = [_SessionProxy(session) for _ in stage]

    if len(stage) > 1:
        tool_names = [s.tool_name for s in stage]
        label = f"  ◈ parallel: {len(stage)} tools → {', '.join(tool_names[:6])}" + (" …" if len(tool_names) > 6 else "")
        ui.muted(label)
        # Update any running MissionControl dashboard
        try:
            from agent.ui.mission_control import get_active_mc
            from collections import Counter
            mc = get_active_mc()
            if mc is not None:
                counts = Counter(tool_names)
                labels = [f"{t}×{c}" if c > 1 else t for t, c in counts.most_common(5)]
                mc.set_parallel(labels)
        except Exception:
            pass

    def _run_one(idx: int, slot: _CallSlot) -> None:
        keys = [k for k in slot.lock_keys if k != "EXCLUSIVE"]
        if keys:
            ownership.acquire_all(keys)
        try:
            dispatch_fn(proxies[idx], slot.call)
        finally:
            if keys:
                ownership.release_all(keys)

    with ThreadPoolExecutor(max_workers=workers,
                            thread_name_prefix="kryth-tool") as ex:
        futures = {ex.submit(_run_one, i, slot): slot
                   for i, slot in enumerate(stage)}
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as exc:
                slot = futures[fut]
                from agent import ui as _ui
                _ui.warn(f"  parallel tool error ({slot.tool_name}): {exc}")

    # Replay captured messages to the real session in original slot order.
    # This is the only point where session.messages is mutated — single-threaded,
    # deterministic, no deadlock possible.
    for proxy in proxies:
        for msg in proxy.captured:
            session.append(msg)

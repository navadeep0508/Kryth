"""Tool registry for the agent loop.

Public surface:

- ``TOOLS``         — name → callable. Used by the dispatcher.
- ``TOOL_SPECS``    — JSON-schema list passed to the LLM via ``tools=``.
- ``READ_ONLY_TOOLS`` — names safe in plan mode.
- ``RUN_COMMAND_ERROR_MARKER`` — sentinel used to flag non-zero exits.

Implementations live in private submodules (``_file_ops``, ``_search``,
``_shell``, ``_todos``, ``_plan``, ``_subagent``). ``_specs`` carries
the LLM-facing schemas. The registry is assembled here so callers can
keep doing ``from agent.tools import TOOLS, TOOL_SPECS``.
"""

from __future__ import annotations

from agent.tools._common import RUN_COMMAND_ERROR_MARKER
from agent.tools._file_ops import (
    read_file,
    write_file,
    edit_file,
    multi_edit,
    delete_file,
    list_files,
    rollback_file,
)
from agent.tools._memory import add_memory
from agent.tools._search import (
    search_code,
    grep,
    glob_files,
    semantic_search,
    lookup_symbol,
    lookup_imports,
    lookup_dependents,
)
from agent.tools._shell import run_command, task_output
from agent.tools._browser import check_browser_errors
from agent.tools._todos import todo_write, todo_read
from agent.tools._plan import exit_plan_mode
from agent.tools._subagent import spawn_agent, spawn_agents_parallel
from agent.tools._critique import self_critique
from agent.tools._git import git_op
from agent.tools._verify import verify_files
from agent.tools._debug import diagnose_error
from agent.tools._project_runner import run_tests, run_install
from agent.tools._checkpoint import checkpoint
from agent.tools._task_graph import run_task_graph
from agent.tools._specs import TOOL_SPECS


TOOLS = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "multi_edit": multi_edit,
    "delete_file": delete_file,
    "list_files": list_files,
    "rollback_file": rollback_file,
    "run_command": run_command,
    "task_output": task_output,
    "search_code": search_code,
    "grep": grep,
    "glob": glob_files,
    "semantic_search": semantic_search,
    "lookup_symbol": lookup_symbol,
    "lookup_imports": lookup_imports,
    "lookup_dependents": lookup_dependents,
    "todo_write": todo_write,
    "todo_read": todo_read,
    "exit_plan_mode": exit_plan_mode,
    "spawn_agent": spawn_agent,
    "spawn_agents_parallel": spawn_agents_parallel,
    "self_critique": self_critique,
    "git_op": git_op,
    "verify_files": verify_files,
    "diagnose_error": diagnose_error,
    "run_tests": run_tests,
    "run_install": run_install,
    "checkpoint": checkpoint,
    "run_task_graph": run_task_graph,
    "add_memory": add_memory,
    "check_browser_errors": check_browser_errors,
}


READ_ONLY_TOOLS = {
    "read_file",
    "list_files",
    "search_code",
    "grep",
    "glob",
    "semantic_search",
    "lookup_symbol",
    "lookup_imports",
    "lookup_dependents",
    "todo_read",
    "todo_write",
    "task_output",
    "exit_plan_mode",
    "self_critique",
    "verify_files",
    "diagnose_error",
    "check_browser_errors",
}


# Tools that emit their own visual representation (panel, diff, todo
# list) and don't need the generic "⎿ first-line preview" tee under
# their tool header. The agent loop reads this set when deciding
# whether to emit ``tool_result``.
SELF_RENDERED_TOOLS = {
    "write_file",
    "edit_file",
    "multi_edit",
    "run_command",
    "todo_write",
    "exit_plan_mode",
}


# Sanity check at import: the spec list and the registry must agree.
# Drift here would silently advertise non-existent tools to the model
# (or hide real ones), so we fail fast instead.
_spec_names = {t["function"]["name"] for t in TOOL_SPECS}
_registry_names = set(TOOLS.keys())
if _spec_names != _registry_names:
    missing_specs = _registry_names - _spec_names
    missing_tools = _spec_names - _registry_names
    raise RuntimeError(
        "tools registry / specs mismatch — "
        f"in TOOLS but not TOOL_SPECS: {sorted(missing_specs)}; "
        f"in TOOL_SPECS but not TOOLS: {sorted(missing_tools)}"
    )


__all__ = [
    "TOOLS",
    "TOOL_SPECS",
    "READ_ONLY_TOOLS",
    "SELF_RENDERED_TOOLS",
    "RUN_COMMAND_ERROR_MARKER",
]

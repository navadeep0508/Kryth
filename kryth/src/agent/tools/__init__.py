"""Tool registry for the agent loop — CORE 22 TOOLS ONLY.

Public surface:

- ``TOOLS``         — name → callable. Used by the dispatcher.
- ``TOOL_SPECS``    — JSON-schema list passed to the LLM via ``tools=``.
- ``READ_ONLY_TOOLS`` — names safe in plan mode.
- ``RUN_COMMAND_ERROR_MARKER`` — sentinel used to flag non-zero exits.

Only the 22 core tools are exposed by default. Browser/OpenCLI/subagent/
research/terminal tools are available at runtime but NOT exposed to the
model unless the tool curator loads them on demand for specific tasks.
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
    search_repo,
    glob_files,
)
from agent.tools._shell import run_command
from agent.tools._opencli import (
    open_url,
    browser_screenshot,
    browser_get_url,
    browser_click,
    browser_type,
    browser_use_task,
)
from agent.tools._todos import todo_write, todo_read
from agent.tools._git import git_op
from agent.tools._verify import verify_files
from agent.tools._checkpoint import checkpoint
from agent.tools._specs import TOOL_SPECS

# ── Runtime-only tool imports (NOT in TOOLS, loaded on demand by curator) ──
# Browser primitives (already imported above): open_url, browser_screenshot, browser_get_url, browser_click, browser_type, browser_use_task
# Other browser tools available in _opencli: fill_form, upload_file, extract_data, download_content, browser_search, browser_login, browser_submit, browser_setup, browser_setup_verify, browser_select, browser_scroll, browser_back, browser_eval_js, browser_keys, browser_tab_list, browser_tab_new, browser_tab_select, browser_get_html, browser_state
# Subagent: spawn_agent, spawn_agents_parallel, run_task_graph
# Research: save_research_finding, get_research_report
# Terminal: shell_exec, shell_state, shell_plan, shell_run_plan, shell_build_test_loop, process_list, process_kill, terminal_memory_recall
# Supervisor: run_supervised_mission, supervisor_status, supervisor_predict, supervisor_health, supervisor_replan, supervisor_recover, ownership_claim, ownership_release, ownership_status, budget_status
# Factory: FACTORY_TOOLS
# Stream write: write_file_begin, write_file_chunk, write_file_finalize

TOOLS = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "multi_edit": multi_edit,
    "delete_file": delete_file,
    "list_files": list_files,
    "search_repo": search_repo,
    "glob": glob_files,
    "run_command": run_command,
    "verify_files": verify_files,
    "todo_write": todo_write,
    "todo_read": todo_read,
    "git_op": git_op,
    "checkpoint": checkpoint,
    "rollback_file": rollback_file,
    "add_memory": add_memory,
    "save_research_finding": lambda url, title, summary, facts="": __import__("agent.tools._opencli", fromlist=["save_research_finding"]).save_research_finding(url, title, summary, facts),
    "get_research_report": lambda: __import__("agent.tools._opencli", fromlist=["get_research_report"]).get_research_report(),
    "open_url": open_url,
    "browser_screenshot": browser_screenshot,
    "browser_get_url": browser_get_url,
    "browser_click": browser_click,
    "browser_type": browser_type,
    "browser_use_task": browser_use_task,
}

READ_ONLY_TOOLS = frozenset({
    "read_file",
    "list_files",
    "glob",
    "search_repo",
    "todo_read",
    "git_op",
    "verify_files",
    "browser_get_url",
    "get_research_report",
})

_MCP_NAMES: set[str] = set()


def register_mcp_tools(mcp_specs: list[dict]) -> int:
    """Register MCP tools from discovered servers.

    Each spec must have an ``mcp_server`` field. The corresponding
    handler is factory-built to dispatch calls to that MCP server.

    Returns the number of tools registered.
    """
    from agent.mcp.manager import call_mcp_tool
    count = 0
    for spec in mcp_specs:
        func = spec["function"]
        name = func["name"]
        server = spec.get("mcp_server", "")

        if name in _MCP_NAMES or name in TOOLS:
            continue

        def _make_handler(_server: str, _tool: str) -> callable:
            def _handler(**kwargs: dict) -> str:
                return call_mcp_tool(_server, _tool, kwargs)
            _handler.__name__ = _tool
            _handler.__qualname__ = f"MCP_{_server}.{_tool}"
            return _handler

        TOOLS[name] = _make_handler(server, name)
        TOOL_SPECS.append(spec)
        _MCP_NAMES.add(name)
        count += 1
    return count

SELF_RENDERED_TOOLS = frozenset({
    "read_file",
    "write_file",
    "edit_file",
    "multi_edit",
    "run_command",
    "glob",
    "search_repo",
    "browser_click",
    "browser_type",
    "browser_screenshot",
    "browser_use_task",
    "todo_write",
    "checkpoint",
})
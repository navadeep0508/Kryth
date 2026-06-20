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
    fts_search,
    ast_search,
    graphify_query,
    search_smart,
)
from agent.tools._shell import run_command, task_output
from agent.tools._browser import check_browser_errors
from agent.tools._opencli import (
    open_url,
    fill_form,
    upload_file,
    extract_data,
    download_content,
    browser_search,
    browser_login,
    browser_submit,
    browser_setup,
    browser_setup_verify,
    browser_click,
    browser_type,
    browser_select,
    browser_scroll,
    browser_screenshot,
    browser_back,
    browser_eval_js,
    browser_keys,
    browser_tab_list,
    browser_tab_new,
    browser_tab_select,
    browser_get_html,
    browser_get_url,
    browser_state,
    save_research_finding,
    get_research_report,
    browser_use_task,
)
from agent.tools._todos import todo_write, todo_read
from agent.tools._plan import exit_plan_mode
from agent.tools._subagent import spawn_agent, spawn_agents_parallel
from agent.tools._critique import self_critique
from agent.tools._git import git_op
from agent.tools._verify import verify_files
from agent.tools._debug import diagnose_error
from agent.tools._project_runner import run_tests, run_install
from agent.tools._terminal import (
    shell_exec,
    shell_state,
    shell_plan,
    shell_run_plan,
    shell_build_test_loop,
    process_list,
    process_kill,
    terminal_memory_recall,
)
from agent.tools._checkpoint import checkpoint
from agent.tools._task_graph import run_task_graph
from agent.tools._supervisor import (
    run_supervised_mission,
    supervisor_status,
    supervisor_predict,
    supervisor_health,
    supervisor_replan,
    supervisor_recover,
    ownership_claim,
    ownership_release,
    ownership_status,
    budget_status,
)
from agent.tools._specs import TOOL_SPECS as _BASE_SPECS
from agent.tools._retrieval_specs import RETRIEVAL_TOOL_SPECS
from agent.tools._terminal_specs import TERMINAL_TOOL_SPECS
from agent.tools._browser_profile_specs import BROWSER_PROFILE_TOOL_SPECS
from agent.tools._browser_profile import BROWSER_PROFILE_TOOLS
from agent.tools._supervisor_specs import SUPERVISOR_TOOL_SPECS
from agent.tools._mission_specs import MISSION_TOOL_SPECS
from agent.tools._mission import MISSION_TOOLS
from agent.tools._factory_specs import FACTORY_TOOL_SPECS
from agent.tools._factory import FACTORY_TOOLS
from agent.tools._stream_write_specs import STREAM_WRITE_TOOL_SPECS
from agent.tools._stream_write import (
    write_file_begin,
    write_file_chunk,
    write_file_finalize,
)

TOOL_SPECS = (
    _BASE_SPECS + RETRIEVAL_TOOL_SPECS + TERMINAL_TOOL_SPECS
    + BROWSER_PROFILE_TOOL_SPECS + SUPERVISOR_TOOL_SPECS
    + MISSION_TOOL_SPECS + FACTORY_TOOL_SPECS
    + STREAM_WRITE_TOOL_SPECS
)


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
    "fts_search": fts_search,
    "ast_search": ast_search,
    "graphify_query": graphify_query,
    "search_smart": search_smart,
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
    # Terminal engine tools
    "shell_exec": shell_exec,
    "shell_state": shell_state,
    "shell_plan": shell_plan,
    "shell_run_plan": shell_run_plan,
    "shell_build_test_loop": shell_build_test_loop,
    "process_list": process_list,
    "process_kill": process_kill,
    "terminal_memory_recall": terminal_memory_recall,
    "check_browser_errors": check_browser_errors,
    "open_url": open_url,
    "fill_form": fill_form,
    "upload_file": upload_file,
    "extract_data": extract_data,
    "download_content": download_content,
    "browser_search": browser_search,
    "browser_login": browser_login,
    "browser_submit": browser_submit,
    "browser_setup": browser_setup,
    "browser_setup_verify": browser_setup_verify,
    "browser_click": browser_click,
    "browser_type": browser_type,
    "browser_select": browser_select,
    "browser_scroll": browser_scroll,
    "browser_screenshot": browser_screenshot,
    "browser_back": browser_back,
    "browser_eval_js": browser_eval_js,
    "browser_keys": browser_keys,
    "browser_tab_list": browser_tab_list,
    "browser_tab_new": browser_tab_new,
    "browser_tab_select": browser_tab_select,
    "browser_get_html": browser_get_html,
    "browser_get_url": browser_get_url,
    "browser_state": browser_state,
    "save_research_finding": save_research_finding,
    "get_research_report": get_research_report,
    "browser_use_task": browser_use_task,
    # Browser profile manager tools
    **BROWSER_PROFILE_TOOLS,
    # Execution Supervisor tools
    "run_supervised_mission": run_supervised_mission,
    "supervisor_status": supervisor_status,
    "supervisor_predict": supervisor_predict,
    "supervisor_health": supervisor_health,
    "supervisor_replan": supervisor_replan,
    "supervisor_recover": supervisor_recover,
    "ownership_claim": ownership_claim,
    "ownership_release": ownership_release,
    "ownership_status": ownership_status,
    "budget_status": budget_status,
    # Mission Graph & Workspace Intelligence tools
    **MISSION_TOOLS,
    # Autonomous Software Factory tools
    **FACTORY_TOOLS,
    # Streaming file write tools (Rule 16)
    "write_file_begin": write_file_begin,
    "write_file_chunk": write_file_chunk,
    "write_file_finalize": write_file_finalize,
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
    "fts_search",
    "ast_search",
    "graphify_query",
    "search_smart",
    "todo_read",
    "todo_write",
    "task_output",
    "exit_plan_mode",
    "self_critique",
    "verify_files",
    "diagnose_error",
    "check_browser_errors",
    "extract_data",
    "browser_tab_list",
    "browser_get_html",
    "browser_get_url",
    "browser_state",
    "browser_screenshot",
    "get_research_report",
    # Browser profile manager (read-only queries)
    "browser_profile_list",
    "browser_session_status",
    "browser_profile_get_user_data_dir",
    # Terminal engine (read-only queries)
    "shell_state",
    "shell_plan",
    "process_list",
    "terminal_memory_recall",
    # Supervisor (read-only queries)
    "supervisor_status",
    "supervisor_predict",
    "supervisor_health",
    "ownership_status",
    "budget_status",
    # Mission (read-only queries)
    "mission_status",
    "mission_summary",
    "mission_impact_analysis",
    # Factory (read-only queries)
    "factory_status",
    "factory_architecture_audit",
    "factory_code_review",
    "factory_dashboard",
    "factory_maintenance_scan",
}


# Tools that emit their own visual representation (panel, diff, todo
# list) and don't need the generic "⎿ first-line preview" tee under
# their tool header. The agent loop reads this set when deciding
# whether to emit ``tool_result``.
SELF_RENDERED_TOOLS = {
    "write_file",
    "write_file_begin",
    "write_file_chunk",
    "write_file_finalize",
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

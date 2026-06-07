"""Engineering Layer — High-Level Action Dashboard.

Shows what KRYTH is doing in engineering terms, not raw tool names.

READ   → Repository Analysis
SEARCH → Code Navigation / Dependency Mapping
WRITE  → Code Generation
EXEC   → Build Execution / Test Execution
PATCH  → Code Modification
DELETE → Cleanup
browser_* → Browser Verification

Emits structured section + action rows instead of tool spam.
"""

from __future__ import annotations

from rich.console import Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from agent.ui.console import console
from agent.ui.theme import CORE, DOT, ERROR, WAITING


# Map raw tool names → engineering labels
_TOOL_TO_ENG: dict[str, str] = {
    # File reads
    "read_file": "Repository Analysis",
    "list_files": "Repository Scan",
    # Search
    "grep": "Code Navigation",
    "glob": "File Discovery",
    "search_code": "Code Search",
    "semantic_search": "Semantic Analysis",
    "lookup_symbol": "Symbol Lookup",
    "lookup_imports": "Import Analysis",
    "lookup_dependents": "Dependency Analysis",
    "fts_search": "Full-Text Search",
    "ast_search": "AST Analysis",
    "search_smart": "Smart Code Search",
    # Writes
    "write_file": "Code Generation",
    "edit_file": "Code Modification",
    "multi_edit": "Batch Code Modification",
    "delete_file": "Cleanup",
    "rollback_file": "Change Rollback",
    # Shell
    "run_command": "Build Execution",
    "run_tests": "Test Execution",
    "run_install": "Dependency Installation",
    "task_output": "Process Output",
    # Terminal engine
    "shell_exec": "Command Execution",
    "shell_state": "Environment Analysis",
    "shell_plan": "Execution Planning",
    "shell_run_plan": "Automated Build",
    "shell_build_test_loop": "Build & Test Loop",
    # Git
    "git_op": "Version Control",
    # Agents
    "spawn_agent": "Team Deployment",
    "spawn_agents_parallel": "Parallel Team Deployment",
    # Plan
    "todo_write": "Task Planning",
    "exit_plan_mode": "Plan Finalized",
    # Quality
    "self_critique": "Code Review",
    "verify_files": "Validation",
    "diagnose_error": "Error Diagnosis",
    # Memory
    "add_memory": "Knowledge Storage",
    "checkpoint": "Checkpoint Created",
    "run_task_graph": "Task Orchestration",
    # Browser
    "open_url": "Browser Verification",
    "browser_use_task": "Browser Automation",
    "browser_search": "Web Research",
    "browser_screenshot": "UI Capture",
    "check_browser_errors": "Browser Error Check",
    # Retrieval
    "terminal_memory_recall": "Workflow Recall",
    "process_list": "Process Monitoring",
    "process_kill": "Process Management",
}

# Tool groupings for section headers
_SECTION_GROUPS: list[tuple[str, frozenset]] = [
    ("Repository Analysis", frozenset({"read_file", "list_files", "glob", "grep",
                                        "search_code", "semantic_search",
                                        "lookup_symbol", "lookup_imports",
                                        "lookup_dependents", "fts_search",
                                        "ast_search", "search_smart", "shell_state"})),
    ("Code Generation", frozenset({"write_file", "edit_file", "multi_edit", "delete_file"})),
    ("Build & Test", frozenset({"run_command", "run_tests", "run_install",
                                 "shell_exec", "shell_run_plan", "shell_build_test_loop",
                                 "shell_plan"})),
    ("Team Coordination", frozenset({"spawn_agent", "spawn_agents_parallel",
                                      "run_task_graph", "checkpoint"})),
    ("Browser Verification", frozenset({"open_url", "browser_use_task", "browser_search",
                                         "browser_screenshot", "check_browser_errors"})),
]


def tool_to_eng_label(tool_name: str) -> str:
    return _TOOL_TO_ENG.get(tool_name, tool_name.replace("_", " ").title())


def render_engineering_action(
    label: str,
    status: str,
    detail: str = "",
) -> None:
    """Render a single engineering action row."""
    if status == "done":
        glyph = CORE
        style = "eng.done"
    elif status == "failed":
        glyph = ERROR
        style = "eng.failed"
    elif status == "running":
        glyph = WAITING
        style = "eng.running"
    else:
        glyph = WAITING
        style = "eng.pending"

    line = Text.assemble(
        (glyph, style),
        ("  ", ""),
        (label, style),
    )
    if detail:
        line.append(f"  {DOT}  {detail}", style="eng.detail")

    console.print(f"  ", end="")
    console.print(line)


def render_engineering_section(title: str) -> None:
    """Render a named engineering section header."""
    console.print()
    console.print(Text.assemble(
        (CORE, "kryth.core"),
        (f"  {title}", "eng.section"),
    ))


def render_engineering_panel(section: str, actions: list) -> None:
    """Render a complete engineering panel with all actions."""
    if not actions:
        return

    body = Table.grid(padding=(0, 2), expand=False)
    body.add_column(no_wrap=True, min_width=2)
    body.add_column(overflow="fold")
    body.add_column(overflow="fold", style="eng.detail")

    for action in actions:
        status = action.status if hasattr(action, "status") else action.get("status", "pending")
        label = action.label if hasattr(action, "label") else action.get("label", "")
        detail = action.detail if hasattr(action, "detail") else action.get("detail", "")

        if status == "done":
            glyph, style = CORE, "eng.done"
        elif status == "failed":
            glyph, style = ERROR, "eng.failed"
        elif status == "running":
            glyph, style = WAITING, "eng.running"
        else:
            glyph, style = WAITING, "eng.pending"

        body.add_row(
            Text(glyph, style=style),
            Text(label, style=style),
            Text(detail, style="eng.detail") if detail else Text(""),
        )

    title_text = section or "Execution"
    console.print()
    console.print(Panel(
        body,
        title=Text.assemble((CORE, "kryth.core"), (f"  {title_text}", "eng.section")),
        title_align="left",
        border_style="divider",
        padding=(0, 2),
        expand=False,
    ))

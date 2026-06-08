"""Mission Console — the clean event-to-output translator.

Every tool call passes through here. Most are silent.
Only engineering milestones surface as timeline events.

This is the single source of truth for what the user sees:
  - Timeline: one line per meaningful action
  - Activity: what's happening right now (replaces spinner)
  - Metrics: structured output for commands, never raw logs

The four layers:
  EXECUTIVE   — timeline + mission panel only (default)
  ENGINEERING — timeline + section headers + action labels
  TERMINAL    — structured metric panels for every command
  DEBUG       — everything raw (gated behind env var / /layer debug)
"""

from __future__ import annotations

import os
import time
import threading
from typing import Optional

from agent.ui.console import console
from agent.ui.theme import CORE, DOT, ERROR, WAITING


# ── Tool classification ──────────────────────────────────────────────────────
#
# SILENT  — never shown in executive/engineering layers.
#           These are pure reads/searches that generate no user-visible event.
#
# ACTION  — shown as a brief timeline line (one sentence, no args).
#
# MILESTONE — shown with a section break and timeline entry.

_SILENT = frozenset({
    "lookup_symbol", "lookup_imports", "lookup_dependents",
    "todo_read", "task_output", "rollback_file",
    "shell_state", "shell_plan", "process_list", "terminal_memory_recall",
    "supervisor_predict", "supervisor_health", "ownership_status", "budget_status",
    "browser_get_url", "browser_get_html", "browser_state", "browser_tab_list",
    "extract_data", "get_research_report", "check_browser_errors",
    "verify_files", "diagnose_error",
})

# Tools that produce a rich result panel rendered after the tool completes.
# These are silent at TOOL_START but show a premium panel at TOOL_RESULT.
_RICH_RESULT = frozenset({
    "read_file", "list_files", "glob", "grep", "search_code",
    "semantic_search", "fts_search", "ast_search", "search_smart", "graphify_query",
    "delete_file",
})

_MILESTONE = frozenset({
    "write_file", "multi_edit",
    "run_command", "shell_exec", "shell_run_plan", "shell_build_test_loop",
    "spawn_agent", "spawn_agents_parallel", "run_task_graph",
    "browser_use_task", "browser_login",
    "checkpoint", "git_op",
    "run_supervised_mission",
})

# Maps tool names to high-level section names.
# When the section changes between consecutive tool calls, a visual
# section divider is emitted so the user understands which phase of
# work they're watching.
_SECTION_GROUPS: dict[str, str] = {
    # Repository exploration
    "read_file":       "Repository Analysis",
    "list_files":      "Repository Analysis",
    "glob":            "Repository Analysis",
    "grep":            "Code Search",
    "search_code":     "Code Search",
    "semantic_search": "Code Search",
    "fts_search":      "Code Search",
    "ast_search":      "Code Search",
    "search_smart":    "Code Search",
    # File changes
    "write_file":      "File Generation",
    "edit_file":       "Code Changes",
    "multi_edit":      "Code Changes",
    "delete_file":     "File Changes",
    # Execution
    "run_command":     "Build & Test",
    "shell_exec":      "Build & Test",
    "shell_run_plan":  "Build & Test",
    "run_install":     "Dependencies",
    "run_tests":       "Build & Test",
    # Browser
    "browser_use_task": "Browser Verification",
    "browser_login":    "Browser Verification",
    "open_url":         "Browser Verification",
    # Git
    "git_op":          "Version Control",
    # Agents
    "spawn_agent":     "Agent Deployment",
    "spawn_agents_parallel": "Agent Deployment",
}

# Tool name → human engineering label (used in timeline)
_LABELS: dict[str, str] = {
    # File operations
    "write_file":       "Updating project",
    "edit_file":        "Applying changes",
    "multi_edit":       "Applying changes",
    "delete_file":      "Removing file",
    "rollback_file":    "Reverting changes",
    # Shell
    "run_command":      "Executing task",
    "shell_exec":       "Executing task",
    "shell_run_plan":   "Running build plan",
    "shell_build_test_loop": "Build and test loop",
    "run_install":      "Installing dependencies",
    "run_tests":        "Running tests",
    # Agents
    "spawn_agent":      "Deploying team member",
    "spawn_agents_parallel": "Deploying engineering team",
    "run_task_graph":   "Orchestrating tasks",
    # Browser
    "browser_use_task": "Browser automation",
    "browser_login":    "🌐 Opening URL",
    "browser_click":    "🖱  Click",
    "browser_type":     "⌨  Type",
    "browser_screenshot": "📷 Screenshot",
    "browser_scroll":   "📜 Scroll",
    "browser_search":   "🔍 Web search",
    "open_url":         "🌐 Opening URL",
    "fill_form":        "📝 Fill form",
    "upload_file":      "📤 Upload file",
    "download_content": "📥 Download",
    "browser_submit":   "✓ Submit",
    "browser_eval_js":  "⚡ Script",
    "browser_back":     "← Back",
    "browser_keys":     "⌨  Input",
    "browser_tab_new":  "🌐 New tab",
    "browser_tab_select": "🌐 Switch tab",
    "save_research_finding": "💾 Save finding",
    # Planning
    "todo_write":       "Updating mission plan",
    "exit_plan_mode":   "Plan finalized",
    "checkpoint":       "Mission checkpoint",
    # Git
    "git_op":           "Version control",
    # Memory
    "add_memory":       "Saving to memory",
    # Quality
    "self_critique":    "Code review",
    # Search (action-level, not silent)
    "read_file":        "Reading project",
    "list_files":       "Scanning project",
    "grep":             "Code navigation",
    "glob":             "File discovery",
    "search_code":      "Repository search",
    "semantic_search":  "Semantic analysis",
    # Supervisor
    "run_supervised_mission": "Starting supervised mission",
    "supervisor_replan": "Dynamic replanning",
    "supervisor_recover": "Automatic recovery",
    "ownership_claim":  "Claiming resource",
    # Other
    "repair_agent":     "Automatic recovery",
}


def label(tool_name: str, args: dict | None = None) -> str:
    """Return the clean engineering label for a tool call."""
    base = _LABELS.get(tool_name)
    if base:
        return base
    # fallback: humanize the name
    return tool_name.replace("_", " ").title()


def is_silent(tool_name: str) -> bool:
    return tool_name in _SILENT or tool_name in _RICH_RESULT


def is_milestone(tool_name: str) -> bool:
    return tool_name in _MILESTONE


# ── Activity line ────────────────────────────────────────────────────────────

_ACTIVITY_MESSAGES = [
    "Analyzing project",
    "Mapping architecture",
    "Validating dependencies",
    "Generating execution plan",
    "Reviewing code",
    "Preparing changes",
    "Verifying build",
    "Running checks",
    "Synthesizing results",
    "Finalizing implementation",
    "Checking edge cases",
    "Optimizing solution",
]

_activity_idx = 0
_activity_lock = threading.Lock()


def next_activity_message() -> str:
    global _activity_idx
    with _activity_lock:
        msg = _ACTIVITY_MESSAGES[_activity_idx % len(_ACTIVITY_MESSAGES)]
        _activity_idx += 1
    return msg


def reset_activity() -> None:
    global _activity_idx
    with _activity_lock:
        _activity_idx = 0


# ── Timeline printer ─────────────────────────────────────────────────────────

def _time_str() -> str:
    return time.strftime("%H:%M:%S")


def emit_timeline(message: str, kind: str = "info") -> None:
    """Print one clean timeline line."""
    glyph_map = {
        "info":    (CORE,    "kryth.core"),
        "success": (CORE,    "timeline.success"),
        "warn":    (ERROR,   "timeline.warn"),
        "error":   (ERROR,   "timeline.error"),
        "running": (WAITING, "timeline.info"),
    }
    glyph, style = glyph_map.get(kind, (CORE, "kryth.core"))
    from rich.text import Text
    from agent.ui.theme import LEFT_MARGIN
    console.print(Text.assemble(
        (" " * LEFT_MARGIN, ""),
        (_time_str(), "timeline.time"),
        ("  ", ""),
        (glyph, style),
        ("  ", ""),
        (message, style if kind != "info" else "title"),
    ))


def emit_section(title: str) -> None:
    """Print a clean section separator."""
    from rich.text import Text
    from agent.ui.theme import LEFT_MARGIN
    try:
        w = console.width - LEFT_MARGIN - len(title) - 8
    except Exception:
        w = 50
    trailer = "─" * max(10, min(w, 60))
    console.print()
    console.print(Text.assemble(
        (" " * LEFT_MARGIN + "─ ", "divider"),
        (CORE, "kryth.core"),
        (f"  {title}  ", "eng.section"),
        (trailer, "divider"),
    ))


# ── Tool event handler ───────────────────────────────────────────────────────

class MissionConsole:
    """Routes tool events into the appropriate clean output."""

    def __init__(self) -> None:
        self._debug = False
        self._current_tool: Optional[str] = None
        self._current_args: dict = {}
        self._tool_count = 0
        self._milestone_count = 0
        self._last_section: str = ""   # tracks active section for divider emission

    def configure(self, debug: bool = False) -> None:
        self._debug = debug

    def _maybe_emit_section(self, tool_name: str) -> None:
        """Emit a section divider when the engineering phase changes."""
        new_section = _SECTION_GROUPS.get(tool_name, "")
        if not new_section or new_section == self._last_section:
            return
        # Flush any pending read batch before the section break
        try:
            from agent.ui.tool_results import _read_batcher
            _read_batcher.flush()
        except Exception:
            pass
        self._last_section = new_section
        emit_section(new_section)

    def on_tool_start(self, tool_name: str, args: dict) -> None:
        self._current_tool = tool_name
        self._current_args = args or {}
        self._tool_count += 1

        # Update engineering state
        try:
            from agent.ui.ui_state import ui_state
            eng_label = label(tool_name, args)
            ui_state.add_eng_action(eng_label, "running")
        except Exception:
            pass

        # Debug layer: show everything
        if self._debug:
            self._debug_tool_header(tool_name, args)
            return

        # Emit section header on phase change (before any other output)
        self._maybe_emit_section(tool_name)

        # Rich-result tools: silent at start; panel shown on result
        if tool_name in _RICH_RESULT:
            return

        # Silent tools: nothing at all
        if tool_name in _SILENT:
            return

        # Milestone: section break + timeline
        if is_milestone(tool_name):
            self._milestone_count += 1
            lbl = label(tool_name, args)
            detail = _extract_detail(tool_name, args)
            if detail:
                emit_timeline(f"{lbl}  —  {detail}")
            else:
                emit_timeline(lbl)
            return

        # Action (non-silent, non-milestone): brief timeline line
        lbl = label(tool_name, args)
        emit_timeline(lbl)

    def on_tool_result(self, tool_name: str, result: str, error: bool) -> None:
        # Update engineering state
        try:
            from agent.ui.ui_state import ui_state
            eng_label = label(tool_name)
            ui_state.finish_eng_action(eng_label, success=not error)
        except Exception:
            pass

        # Rich-result tools: show premium panel (skip on error)
        if tool_name in _RICH_RESULT and not error:
            try:
                from agent.ui import tool_results
                tool_results.dispatch(tool_name, self._current_args, result)
            except Exception:
                pass
            return

        if self._debug:
            from rich.text import Text
            first = result.strip().splitlines()[0][:120] if result.strip() else "(empty)"
            console.print(Text.assemble(
                ("  ", ""), ("│ ", "tool.tee"), (first, "tool.error" if error else "tool.result")
            ))

    def _debug_tool_header(self, tool_name: str, args: dict) -> None:
        from rich.text import Text
        summary = _extract_detail(tool_name, args)
        action = label(tool_name)
        console.print()
        if summary:
            console.print(Text.assemble(
                (CORE, "tool.bullet"), (f" {action.upper()}", "tool.name"),
                ("  ·  ", "muted"), (summary, "tool.arg"),
            ))
        else:
            console.print(Text.assemble(
                (CORE, "tool.bullet"), (f" {action.upper()}", "tool.name"),
            ))

    def reset_turn(self) -> None:
        self._tool_count = 0
        self._milestone_count = 0
        self._current_tool = None
        self._current_args = {}
        self._last_section = ""
        reset_activity()
        # Flush any pending reads from previous turn
        try:
            from agent.ui.tool_results import _read_batcher
            _read_batcher.flush()
        except Exception:
            pass


def _extract_detail(tool_name: str, args: dict | None) -> str:
    """Return a brief, clean detail string from tool args — no raw dumps."""
    if not args:
        return ""
    # Browser: URL is the most useful detail
    if tool_name in {"open_url", "browser_login"}:
        url = args.get("url") or args.get("href") or ""
        if url:
            # Trim to hostname + path, no scheme
            trimmed = url.replace("https://", "").replace("http://", "")
            return trimmed[:60]
    if tool_name in {"browser_click", "browser_type", "browser_keys"}:
        sel = args.get("selector") or args.get("text") or args.get("element") or ""
        if sel:
            return str(sel)[:40]
    if tool_name == "browser_screenshot":
        return "capturing screenshot"
    if tool_name in {"browser_scroll"}:
        return "scrolling"
    if tool_name == "browser_search":
        q = args.get("query") or ""
        return f'"{q[:30]}"' if q else "web search"
    # File path
    for key in ("path", "file", "directory", "dest"):
        val = args.get(key)
        if isinstance(val, str) and val:
            return os.path.basename(val) or val
    # Command — show first meaningful token
    cmd = args.get("command") or args.get("cmd") or ""
    if cmd:
        parts = cmd.strip().split()
        # Show "npm install" or "pytest tests/" style
        short = " ".join(parts[:3]) if len(parts) > 1 else parts[0] if parts else cmd
        return short[:50] + ("…" if len(short) > 50 else "")
    # Query / pattern
    for key in ("query", "pattern", "prompt"):
        val = args.get(key)
        if isinstance(val, str) and val:
            return f'"{val[:40]}{"…" if len(val) > 40 else ""}"'
    # Task / agent description
    for key in ("task", "description", "goal"):
        val = args.get(key)
        if isinstance(val, str) and val:
            return str(val)[:50]
    return ""


# Module-level singleton
mission_console = MissionConsole()

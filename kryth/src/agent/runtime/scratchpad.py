"""Runtime Scratchpad v3 — single execution brain for KRYTH.

Sole authority on: goal, intent, tools, progress, next_action, should_stop.

No other system independently decides execution flow.
"""

from __future__ import annotations

import logging
import os as _os
import re as _re

_logger = logging.getLogger(__name__)
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Iterable

from agent.runtime.confidence import (
    ConfidenceScores,
    PolicyController,
    update_scores_from_state,
)


# ── Loop decision type ─────────────────────────────────────────────────

@dataclass
class LoopDecision:
    """Single decision from evaluate(): stop, continue, or nudge."""
    done: bool = False
    nudge: Optional[str] = None
    finish_reason: str = "completed"


# ── Execution todo step ────────────────────────────────────────────────────

@dataclass
class TodoStep:
    """Structured execution plan item."""
    id: str
    title: str
    status: str = "pending"  # pending, active, completed, blocked, failed
    tool_hint: Optional[str] = None  # expected tool to accomplish this step
    verification_required: bool = False


# ── Intent-tool mapping (consolidated from tool_curator) ────────────────

_MINIMAL_CORE = frozenset({
    "read_file", "write_file", "edit_file", "multi_edit",
    "run_command", "list_files",
    "todo_write",
})
_FIX_CORE = _MINIMAL_CORE | frozenset({"grep", "run_tests"})
_SEARCH_CORE = frozenset({
    "read_file", "list_files", "grep", "glob", "search_smart", "lookup_symbol",
})
_MODIFY_CORE = _MINIMAL_CORE | frozenset({"grep", "delete_file"})
_BUILD_CORE = _FIX_CORE | frozenset({"glob", "search_smart", "run_install"})
_REFACTOR_CORE = _BUILD_CORE | frozenset({"self_critique", "lookup_symbol"})
_REFACTOR_DEEP = _REFACTOR_CORE | frozenset({
    "lookup_imports", "lookup_dependents",
    "checkpoint", "rollback_file", "verify_files",
    "write_file_begin", "write_file_chunk", "write_file_finalize",
})
_READ_ONLY_CORE = frozenset({"read_file", "lookup_symbol"})
_READ_PROJECT_CORE = frozenset({"read_file", "list_files", "lookup_symbol"})

_INTENT_TO_CORE = {
    "CHAT":   frozenset(),
    "CHAT_READ": _READ_ONLY_CORE,
    "READ":   _READ_PROJECT_CORE,
    "SEARCH": _SEARCH_CORE,
    "MODIFY": _MODIFY_CORE,
    "RUN":    _MINIMAL_CORE,
    "BUILD":  _BUILD_CORE,
    "FIX":    _FIX_CORE,
    "REFACTOR": _REFACTOR_CORE,
}
INTENTS = tuple(_INTENT_TO_CORE.keys())

NEXT_ACTIONS = (
    "READ", "SEARCH", "WRITE", "RUN", "VERIFY", "SUMMARIZE", "DONE",
)


# ── Domain tool triggers (from tool_curator) ─────────────────────────────

_BROWSER_ESSENTIAL = frozenset({
    "browser_use_task", "open_url", "extract_data",
    "browser_search", "save_research_finding", "get_research_report",
})
_BROWSER_FULL_RE = _re.compile(
    r"\b(fill|form|login|upload|submit|click|type|scroll|eval|tab|iframe"
    r"|interact|automate|multi.?step|sequence|workflow|authenticate)\b", _re.I,
)
_DOMAIN_TOOLS: dict[str, object] = {
    "browser": lambda n: n.startswith("browser_") or n in {
        "open_url", "fill_form", "upload_file", "extract_data",
        "download_content", "save_research_finding", "get_research_report",
        "check_browser_errors", "browser_use_task",
    },
    "git": lambda n: n == "git_op",
}
_DOMAIN_TRIGGERS: dict[str, _re.Pattern] = {
    "browser": _re.compile(
        r"\b(browser|navigate|click|scrape|scraping|crawl|website|webpage|url|https?://"
        r"|youtube|google\.com|open\s+the?\s*(site|page|url|browser)|web\s+automation"
        r"|browser_use|fill\s+the?\s*form|download\s+(from|the)\s*(web|site|page))\b", _re.I,
    ),
    "git": _re.compile(
        r"\b(git|commit|branch|merge|rebase|stage|stash|push|pull|clone|checkout"
        r"|diff|version\s+control)\b", _re.I,
    ),
}

# Intent-detection regexes (from tool_curator)
_TRIVIAL_CREATE_RE = _re.compile(
    r"^(create|write|make|add)\s+\w.*\.(py|js|ts|html|css|sh|txt|md|json|yaml|yml)\b"
    r"|^(create|write|make)\s+(a\s+)?(file|script|function|class|hello|simple)\b"
    r"|\bprint\b.*\brun\b|\brun\b.*\bprint\b", _re.I,
)
_FIX_RE = _re.compile(
    r"\b(fix|bug|error|crash|debug|issue|problem|broken|failing|exception|traceback"
    r"|why\s+(is|does|doesn'?t|isn'?t|won'?t)|not\s+work|doesn'?t\s+work)\b", _re.I,
)
_READ_PROJECT_RE = _re.compile(
    r"^(read|open|show\s+me)\s+(the\s+|this\s+)?"
    r"(project|directory|folder|repo|codebase|files|structure)\b", _re.I,
)
_READ_RE = _re.compile(
    r"^(read|open|cat|show\s+me)\s+\S+"
    r"|\bread\s+(the\s+)?(contents\s+of|content\s+of)\b", _re.I,
)
_SEARCH_RE = _re.compile(
    r"\b(search|find|look\s+(for|through|at)|where\s+is|grep|locate|show\s+me"
    r"|what\s+(files|functions|classes)|list\s+all|scan|explore|navigate"
    r"|which\s+(file|module)|look\s+across\s+all)\b", _re.I,
)
_MODIFY_RE = _re.compile(
    r"\b(modify|update|change|edit|rewrite|overwrite|append|prepend|insert|"
    r"remove\s+(line|function|import|code)|replace|rename|"
    r"write\s+(a\s+)?(file|script|function|class|hello|simple))\b", _re.I,
)
_RUN_RE = _re.compile(
    r"^(run|execute|start|launch)\s"
    r"|\b(run\s+(the\s+)?(test|suite|lint|format|build|dev|server|script|command|task))"
    r"|\b(execute\s+(this|that|the))"
    r"|\b(start\s+(the\s+)?(server|app|service))"
    r"|^(install|npm\s+(run|install|start|test)|pip\s+install|yarn)\b", _re.I,
)
_BUILD_RE = _re.compile(
    r"\b(build|implement|create\s+(a|the|an)\s+(app|api|server|service|endpoint"
    r"|backend|frontend|cli|bot|module|package|project|system)"
    r"|add\s+(feature|support|endpoint|route|page|component)|set\s+up|scaffold"
    r"|install|package\.json|requirements\.txt)\b", _re.I,
)
_REFACTOR_RE = _re.compile(
    r"\b(refactor|audit|analyze|optimiz|restructure|clean\s+up|improve"
    r"|review|all\s+files|across\s+the\s+codebase|throughout|everywhere)\b", _re.I,
)
_REFACTOR_DEEP_RE = _re.compile(
    r"\b(entire\s+codebase|comprehensive\s+(audit|review|refactor|analysis)"
    r"|audit\s+entire|refactor\s+everything|migrate\s+(entire|all)"
    r"|system.wide|codebase.wide|all\s+modules|every\s+file)\b", _re.I,
)

# Hard caps per intent (prevent tool-explosion)
_INTENT_CAPS: dict[str, int] = {
    "read": 8, "search": 12, "modify": 10, "run": 15,
    "build": 15, "refactor": 18, "explore": 15, "default": 20,
}


# ── Command/path normalization ───────────────────────────────────────────

_COMMAND_NORMALIZATIONS: list = []


def _init_normalizations():
    global _COMMAND_NORMALIZATIONS
    _COMMAND_NORMALIZATIONS = [
        (_re.compile(r"^python\s+(\./)?([A-Za-z0-9_/-]+\.py)"), r"python \2"),
        (_re.compile(r"^python3\s+(\./)?([A-Za-z0-9_/-]+\.py)"), r"python \2"),
        (_re.compile(r"^node\s+(\./)?([A-Za-z0-9_/-]+\.js)"), r"node \2"),
        (_re.compile(r"^npm\s+run\s+"), r"npm run "),
        (_re.compile(r"^yarn\s+"), r"yarn "),
        (_re.compile(r"\s+"), " "),
    ]


_init_normalizations()


def normalize_command(cmd: str) -> str:
    c = cmd.strip().lower()
    c = _re.sub(r"\s+", " ", c)
    c = _re.sub(r"^(\.\\\\|\./)", "", c)
    for pattern, replacement in _COMMAND_NORMALIZATIONS:
        c = pattern.sub(replacement, c)
    return c.strip()


def normalize_path(path: str) -> str:
    try:
        return str(Path(path).resolve())
    except Exception:
        p = _os.path.normpath(_os.path.abspath(path))
        return p.lower()


def _known_duplicate(label_a: str, label_b: str) -> bool:
    if label_a == label_b:
        return True
    if "ran:" in label_a and "ran:" in label_b:
        cmd_a = normalize_command(label_a.split("ran:")[-1].strip())
        cmd_b = normalize_command(label_b.split("ran:")[-1].strip())
        return cmd_a == cmd_b
    for prefix in ("read ", "wrote ", "edited "):
        if label_a.startswith(prefix) and label_b.startswith(prefix):
            path_a = label_a[len(prefix):].strip().strip('"')
            path_b = label_b[len(prefix):].strip().strip('"')
            return normalize_path(path_a) == normalize_path(path_b)
    return False


# ── Scratchpad core dataclass ────────────────────────────────────────────

@dataclass
class Scratchpad:
    goal: str = ""
    intent: str = "READ"
    completed_steps: List[str] = field(default_factory=list)
    findings: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    next_action: str = ""
    progress: float = 0.0
    confidence: float = 0.5
    allowed_tools: List[str] = field(default_factory=lambda: list(_READ_PROJECT_CORE))
    should_stop: bool = False

    # Duplicate/loop detection
    _max_progress_seen: float = 0.0
    _loop_count: int = 0
    _last_n_actions: deque = field(default_factory=lambda: deque(maxlen=6))
    _tool_call_count: int = 0
    _tool_error_count: int = 0
    _force_summarize: bool = False

    # Stop-after-success tracking (moved from anti_paralysis)
    _files_written: int = 0
    _tests_passed: bool = False

    # ── Confidence scores (multi-dimensional) ───────────────────────────────
    confidence_scores: ConfidenceScores = field(default_factory=ConfidenceScores)

    # ── Execution plan ──────────────────────────────────────────────────────
    todos: List[TodoStep] = field(default_factory=list)
    active_todo_idx: int = 0  # index of currently active todo (if any)

    def reset(self) -> None:
        self.__init__()


# ── Intent selection ─────────────────────────────────────────────────────

def _select_domains(text: str) -> set:
    if not text:
        return set()
    text_lower = text.lower()
    return {d for d, rx in _DOMAIN_TRIGGERS.items() if rx.search(text_lower)}


def _pick_intent(text: str, domains: set) -> str:
    """Map user input + domains to a scratchpad intent name."""
    if domains:
        return "CHAT"  # domain tasks start lean; curate_tools adds extras

    if _TRIVIAL_CREATE_RE.search(text.strip()):
        return "CHAT"
    if _REFACTOR_DEEP_RE.search(text):
        return "REFACTOR"
    if _REFACTOR_RE.search(text):
        return "REFACTOR"
    if _READ_PROJECT_RE.search(text):
        return "READ"
    if _READ_RE.search(text):
        return "CHAT_READ"
    if _SEARCH_RE.search(text) and not _BUILD_RE.search(text) and not _MODIFY_RE.search(text):
        return "SEARCH"
    if _MODIFY_RE.search(text):
        return "MODIFY"
    if _RUN_RE.search(text):
        return "RUN"
    if _FIX_RE.search(text):
        return "FIX"
    if _BUILD_RE.search(text):
        return "BUILD"

    # Greeting detection (original simple check for short inputs)
    words = set(_re.findall(r"[A-Za-z]+", text.lower()))
    if words & {"hi", "hello", "hey", "alo"}:
        return "CHAT"

    return "BUILD"


# ── ScratchpadManager ────────────────────────────────────────────────────

class ScratchpadManager:
    def __init__(self):
        self.state: Optional[Scratchpad] = None
        self.current_intent: str = "READ"
        self._tool_map: Dict[str, str] = {
            "list_files":  "scanned repository",
            "read_file":   "read {path}",
            "write_file":  "wrote {path}",
            "edit_file":   "edited {path}",
            "multi_edit":  "edited multiple files",
            "run_command": "ran: {command}",
            "run_tests":   "ran tests",
            "grep":        "searched: {pattern}",
            "glob":        "searched: {pattern}",
            "search_code": "searched: {pattern}",
        }

    # ── Initialization ────────────────────────────────────────────────────

    def initialize(self, user_input: str) -> None:
        self.state = Scratchpad(goal=user_input.strip())
        text = (user_input or "").strip()
        domains = _select_domains(text)
        self.current_intent = _pick_intent(text, domains)
        self.state.intent = self.current_intent
        self.state.allowed_tools = list(_INTENT_TO_CORE.get(self.current_intent, _BUILD_CORE))
        self.state.next_action = self.current_intent if self.current_intent not in ("CHAT", "CHAT_READ") else "DONE"
        self._recompute_state()
        plan = self._create_execution_plan()
        if plan:
            self.state.todos = plan
            self.state.active_todo_idx = 0

    # ── Execution plan ────────────────────────────────────────────────────

    def _create_execution_plan(self) -> list:
        """Auto-generate execution plan based on intent and goal complexity.

        Only generates plans for tasks with 3+ estimated steps.
        Returns empty list for simple tasks (≤2 steps).
        """
        if self.state is None:
            return []
        intent = self.state.intent
        if intent in ("CHAT", "CHAT_READ"):
            return []

        _plan_templates = {
            "BUILD": [
                ("build_1", "Analyze requirements and project structure", "list_files", False),
                ("build_2", "Create/implement source files", "write_file", False),
                ("build_3", "Install dependencies if needed", "run_install", False),
                ("build_4", "Run tests to verify", "run_tests", True),
                ("build_5", "Final verification", None, True),
            ],
            "FIX": [
                ("fix_1", "Read and understand the issue", "read_file", False),
                ("fix_2", "Edit files to fix the bug", "edit_file", False),
                ("fix_3", "Run tests to confirm fix", "run_tests", True),
                ("fix_4", "Verify the fix works", None, True),
            ],
            "REFACTOR": [
                ("ref_1", "Scan codebase for relevant files", "list_files", False),
                ("ref_2", "Read key files to understand structure", "read_file", False),
                ("ref_3", "Apply refactoring edits", "edit_file", False),
                ("ref_4", "Run tests to validate", "run_tests", True),
                ("ref_5", "Final verification", None, True),
            ],
            "MODIFY": [
                ("mod_1", "Read file(s) to understand current code", "read_file", False),
                ("mod_2", "Edit file(s) to apply changes", "edit_file", False),
                ("mod_3", "Verify the changes work", None, True),
            ],
            "RUN": [
                ("run_1", "Set up and prepare execution", "run_install", False),
                ("run_2", "Execute the command/script", "run_command", False),
                ("run_3", "Verify output and success", None, True),
            ],
            "READ": [
                ("read_1", "Scan project structure", "list_files", False),
                ("read_2", "Read key files", "read_file", False),
                ("read_3", "Summarize findings", None, False),
            ],
            "SEARCH": [
                ("search_1", "Search for relevant code/patterns", "grep", False),
                ("search_2", "Read and review matches", "read_file", False),
                ("search_3", "Summarize results", None, False),
            ],
        }

        template = _plan_templates.get(intent)
        if template is None:
            return []

        num_steps = len(template)
        if num_steps < 3:
            return []

        # For 3-step intents (MODIFY, RUN, READ, SEARCH), check goal has substance
        if num_steps == 3:
            goal = (self.state.goal or "").strip()
            if len(goal) < 15:
                return []

        todos = []
        for i, (tid, title, tool_hint, verify) in enumerate(template):
            status = "active" if i == 0 else "pending"
            todos.append(TodoStep(
                id=tid,
                title=title,
                status=status,
                tool_hint=tool_hint,
                verification_required=verify,
            ))
        return todos

    def _has_evidence(self, step: TodoStep, tool_name: str, result: str) -> bool:
        """Check whether the tool call produced enough evidence to mark a step complete.

        Steps with ``verification_required=True`` need strong evidence
        (tests pass, command succeeded).
        Other steps advance on tool match + no fatal error.
        """
        _r = str(result).lower()
        _has_err = "[error" in _r or "[ERROR" in str(result) or "traceback" in _r

        # Fatal error → never advance
        if _has_err:
            return False

        # Verification-required steps need semantic proof
        if step.verification_required:
            if step.tool_hint in ("run_tests",):
                # Tests must actually pass
                return ("passed" in _r or "ok" in _r) and "failed" not in _r
            if step.tool_hint in ("run_command", "run_install"):
                # Command must succeed (no failure signals)
                return not any(w in _r for w in (
                    "error", "failed", "not found", "permission denied",
                    "exit code", "could not", "cannot",
                ))
            if step.tool_hint in ("read_file",):
                # Read must return content
                return len(str(result)) > 50
            if step.tool_hint in ("write_file", "edit_file", "multi_edit"):
                # Write must not fail (already checked _has_err above)
                return True
            # Default for unknown verification: tool ran without error
            return True

        # Non-verification steps: basic sanity check
        if step.tool_hint in ("run_tests",):
            # Don't advance tests that failed
            return not any(w in _r for w in ("failed", "0 passed", "error"))
        if step.tool_hint in ("read_file",):
            return len(str(result)) > 20

        return True

    def _advance_todo(self, tool_name: str, args: dict, result: str = "") -> None:
        """Advance the execution plan based on tool match + semantic evidence.

        A step only advances when:
        1. The tool name matches the active step's hint (or is an equivalent tool), AND
        2. ``_has_evidence()`` confirms the tool produced meaningful output.
        """
        if self.state is None or not self.state.todos:
            return
        s = self.state
        if s.active_todo_idx >= len(s.todos):
            return

        active = s.todos[s.active_todo_idx]
        if active.status != "active":
            return

        hint = active.tool_hint
        matched = False
        if hint and tool_name == hint:
            matched = True
        elif hint == "todo_write" and tool_name == "todo_write":
            matched = True
        elif hint in ("write_file", "edit_file", "multi_edit") and tool_name in ("write_file", "edit_file", "multi_edit"):
            matched = True
        elif hint in ("run_command", "run_install", "run_tests") and tool_name in ("run_command", "run_install", "run_tests"):
            matched = True
        elif hint in ("read_file", "grep") and tool_name in ("read_file", "grep"):
            matched = True

        if matched and self._has_evidence(active, tool_name, result):
            active.status = "completed"
            next_idx = s.active_todo_idx + 1
            while next_idx < len(s.todos):
                if s.todos[next_idx].status == "pending":
                    s.todos[next_idx].status = "active"
                    s.active_todo_idx = next_idx
                    return
                next_idx += 1
            # All todos completed
            s._force_summarize = True

    # ── Single authoritative tool gate ────────────────────────────────────

    def allows_tool(self, tool_name: str) -> bool:
        """Single gate: is this tool allowed right now?"""
        if self.state is None:
            return True
        return tool_name in set(self.state.allowed_tools)

    def curate_tools(self, tool_specs: list, *, force_full: bool = False) -> list:
        """Single tool-curation entry point. Replaces tool_curator.curate().

        Returns only tools relevant to the current intent + active domains.
        When intent blocks a tool class (e.g. CHAT won't offer write_file),
        the spec is simply not returned here — the model never sees it.
        """
        if force_full or self.state is None:
            return list(tool_specs)

        allowed = set(self.state.allowed_tools)
        if not allowed:
            return []  # CHAT — no tools

        text = self.state.goal
        domains = _select_domains(text)
        _lookup_domain = {n: d for d, pred in _DOMAIN_TOOLS.items()
                          for n in _get_domain_names(d, pred)}

        _needs_streaming = bool(_re.search(
            r"\b(large\s+file|long\s+file|stream|chunk|>200\s+lines|big\s+file|write\s+large)\b",
            text, _re.I,
        ))

        keep = []
        for spec in tool_specs:
            name = (spec.get("function", {}) or {}).get("name", "")
            if not name:
                continue
            if name in allowed:
                keep.append(spec)
                continue
            dom = _lookup_domain.get(name)
            if dom and dom in domains:
                if dom == "browser":
                    _browser_full = "browser" in domains and _BROWSER_FULL_RE.search(text)
                    if not _browser_full:
                        if name in _BROWSER_ESSENTIAL:
                            keep.append(spec)
                    else:
                        keep.append(spec)
                else:
                    keep.append(spec)
            elif name in frozenset({"write_file_begin", "write_file_chunk", "write_file_finalize"}) and _needs_streaming:
                keep.append(spec)

        cap = _INTENT_CAPS.get(self.current_intent.lower(), _INTENT_CAPS["default"])
        if domains:
            cap = _INTENT_CAPS["explore"]
        if len(keep) > cap:
            keep = keep[:cap]

        return keep or list(tool_specs)[:25]

    # ── Tool update hook ──────────────────────────────────────────────────

    def update_after_tool(self, tool_name: str, result: str, args: Optional[dict] = None) -> None:
        if self.state is None:
            return

        step_label = self._format_tool_success(tool_name, result, args)
        if not step_label:
            return

        if self._is_duplicate(step_label):
            self.state._loop_count += 1
        else:
            self.state.completed_steps.append(step_label)
            self.state._loop_count = 0
            finding = self._extract_finding(tool_name, result, args)
            if finding:
                self.state.findings.append(finding)

        self.state._last_n_actions.append(step_label)
        if self._detect_loop():
            self.state._force_summarize = True

        self.state._tool_call_count += 1
        if "[error" in str(result).lower() or "[ERROR" in str(result):
            self.state._tool_error_count += 1

        # Stop-after-success tracking
        if tool_name in ("write_file", "edit_file", "delete_file"):
            self.state._files_written += 1
        if tool_name in ("run_tests", "run_command"):
            _result_str = str(result)
            if ("passed" in _result_str.lower() or "ok" in _result_str.lower()) \
               and "failed" not in _result_str.lower() \
               and "error" not in _result_str.lower()[:100]:
                self.state._tests_passed = True

        self._check_blockers(result)
        self._advance_todo(tool_name, args or {}, result)
        self._recompute_state()

    def _format_tool_success(self, tool_name: str, args_or_result: str, args: Optional[dict] = None) -> Optional[str]:
        pattern = self._tool_map.get(tool_name)
        if not pattern:
            return None
        if args:
            try:
                return pattern.format(
                    path=args.get("path", "") or "(unknown)",
                    command=(args.get("command", "") or "")[:60] or "(unknown)",
                    pattern=args.get("pattern", "") or "(unknown)",
                )
            except Exception as _e:
                _logger.debug("_format_tool_success format: %s", _e)
        if "path" in args_or_result or "Args:" in args_or_result:
            try:
                import json as _json
                extracted = _json.loads(str(args_or_result).split("Args:")[-1].split("|")[0])
                return pattern.format(path=extracted.get("path", ""), command=extracted.get("command", ""), pattern=extracted.get("pattern", ""))
            except Exception as _e:
                _logger.debug("_format_tool_success json extract: %s", _e)
        return pattern

    # ── Semantic duplicate detection ──────────────────────────────────────

    def _is_duplicate(self, new_label: str) -> bool:
        if self.state is None:
            return False
        for existing in self.state.completed_steps:
            if _known_duplicate(existing, new_label):
                return True
        return False

    # ── Loop killer ───────────────────────────────────────────────────────

    def _detect_loop(self) -> bool:
        if self.state is None or len(self.state._last_n_actions) < 3:
            return False
        recent = list(self.state._last_n_actions)
        if len(recent) >= 3:
            if recent[-1] == recent[-2] == recent[-3]:
                return True
            scan_count = sum(1 for a in recent if a == "scanned repository")
            if scan_count >= 3:
                return True
        # Re-read detection: same file path read more than once in last 6 actions
        read_files = [a for a in recent if a.startswith("read ")]
        if len(read_files) >= 2 and len(set(read_files)) < len(read_files):
            return True
        return False

    # ── Finding extraction ────────────────────────────────────────────────

    def _extract_finding(self, tool_name: str, result: str, args: Optional[dict] = None) -> Optional[str]:
        path = (args or {}).get("path", "") if args else ""
        if tool_name == "read_file" and path:
            lines = result.count("\n") + 1
            first_line = (result or "").split("\n")[0][:60].strip()
            return f"{path}: {lines} lines | {first_line}"
        if tool_name == "grep":
            pattern = (args or {}).get("pattern", "") if args else ""
            matches = len([l for l in (result or "").split("\n") if l.strip()])
            return f"grep '{pattern}': {matches} matches"
        if tool_name == "run_command" and result:
            cmd = (args or {}).get("command", "")[:50] if args else ""
            lines = (result or "").strip().split("\n")
            summary = lines[-1][:80] if lines else ""
            return f"$ {cmd} ... {summary}"
        if tool_name == "list_files":
            count = len([l for l in (result or "").split("\n") if l.strip()])
            return f"scanned {count} files/dirs"
        return None

    # ── Blocker detection ─────────────────────────────────────────────────

    def _check_blockers(self, tool_result: str) -> None:
        if self.state is None:
            return
        lower = str(tool_result).lower()
        blockers = []
        if "[error" in lower:
            blockers.append("execution error")
        elif "missing dependency" in lower:
            blockers.append("missing dependency")
        elif "permission denied" in lower:
            blockers.append("permission denied")
        elif "key not set" in lower:
            blockers.append("missing API key")
        self.state.blockers = list(set(blockers))

    # ── State recomputation ───────────────────────────────────────────────

    def _recompute_state(self) -> None:
        if self.state is None:
            return
        s = self.state
        # Derive multi-dimensional confidence from accumulated evidence
        update_scores_from_state(
            s.confidence_scores,
            completed_steps=s.completed_steps,
            findings=s.findings,
            todos=s.todos,
            files_written=s._files_written,
            tests_passed=s._tests_passed,
            tool_error_count=s._tool_error_count,
            tool_call_count=s._tool_call_count,
            loop_count=s._loop_count,
            force_summarize=s._force_summarize,
        )
        s.progress = self._compute_progress()
        s._max_progress_seen = max(s._max_progress_seen, s.progress)
        s.confidence = self._compute_confidence()
        s.next_action = self.decide_next_action()
        s.should_stop = self._compute_should_stop()

    def _compute_progress(self) -> float:
        intent = self.state.intent if self.state else "READ"
        steps_str = str(self.state.completed_steps).lower()

        if intent in ("CHAT", "CHAT_READ"):
            return 1.0
        if intent == "READ":
            read_count = steps_str.count("read ")
            return min(read_count / 6, 1.0)
        if intent == "SEARCH":
            return min(steps_str.count("searched") / 3, 1.0)
        if intent == "RUN":
            launched = int("server running" in steps_str or "server is alive" in steps_str or "started" in steps_str)
            verified = int("code=200" in steps_str or "status: alive" in steps_str or "success" in steps_str)
            return launched * 0.6 + verified * 0.4
        if intent == "MODIFY":
            edits = steps_str.count("edited ") + steps_str.count("wrote ")
            verified = int("passed" in steps_str or "validation" in steps_str)
            return min(edits / 2, 0.7) + verified * 0.3
        if intent == "BUILD":
            files = int("wrote " in steps_str)
            tests = int("passed" in steps_str or "success" in steps_str)
            return files * 0.5 + tests * 0.5
        if intent == "FIX":
            edits = steps_str.count("edited ") + steps_str.count("wrote ")
            verified = int("passed" in steps_str)
            return min(edits, 1.0) * 0.6 + verified * 0.4
        if intent == "REFACTOR":
            return min(steps_str.count("edited ") / 3, 0.6) + int("passed" in steps_str) * 0.4
        return 0.5

    def _compute_confidence(self) -> float:
        """Overall confidence derived from multi-dimensional ConfidenceScores."""
        if self.state is None:
            return 0.5
        return self.state.confidence_scores.overall

    def _compute_should_stop(self) -> bool:
        if self.state is None:
            return False
        if self.state.intent in ("CHAT", "CHAT_READ"):
            return True
        if self.state._force_summarize:
            return True
        if self.state.next_action == "DONE":
            return True
        if self.state.progress > 0.9 and self.state.confidence > 0.7:
            return True
        if self.state.progress >= 1.0 and self.state.confidence >= 0.5:
            return True
        if self.state.next_action == "SUMMARIZE":
            return self._is_done_for_intent(self.state.intent, self.state)
        return False

    def _is_done_for_intent(self, intent: str, s) -> bool:
        # If execution plan exists, respect todo completion
        if s.todos:
            # All completed → done
            if all(t.status == "completed" for t in s.todos):
                return True
            # All terminal (blocked or failed) → done (can't proceed)
            if all(t.status in ("completed", "blocked", "failed") for t in s.todos):
                return True
            # Any step blocked/failed AND LLM said DONE → done
            if s.next_action == "DONE":
                return True
            return False
        if s.blockers:
            return False
        steps = str(s.completed_steps).lower()
        if intent in ("READ", "SEARCH"):
            return True
        if intent == "MODIFY":
            verified = any(x in steps for x in ["passed", "validation", "read ", "diff"])
            return s._files_written >= 1 and (verified or s._tests_passed)
        if intent == "RUN":
            return any(x in steps for x in ["success", "failed", "code=200", "status: alive"])
        if intent == "BUILD":
            verified = any(x in steps for x in ["passed", "success", "executed", "running", "alive"])
            return s._files_written >= 1 and verified
        if intent == "FIX":
            return s._files_written >= 1 and ("passed" in steps or s._tests_passed)
        if intent == "REFACTOR":
            return s._files_written >= 1 and any(x in steps for x in ["passed", "success", "validation"])
        return False

    # ── Next action engine ────────────────────────────────────────────────

    def decide_next_action(self) -> str:
        if self.state is None:
            return "READ"

        s = self.state
        cs = s.confidence_scores

        # Force summarise overrides everything
        if s._force_summarize:
            return "SUMMARIZE"

        # Blocker-based decisions (still heuristic — no confidence data yet)
        blockers = s.blockers
        if blockers:
            if "missing dependency" in blockers:
                return "RUN"
            if "execution error" in blockers:
                return "VERIFY"
            if "permission denied" in blockers or "missing API key" in blockers:
                return "SUMMARIZE"

        # Chat intents always done
        if s.intent in ("CHAT", "CHAT_READ"):
            return "DONE"

        steps_str = str(s.completed_steps).lower()
        progress = s.progress

        # ── Policy-driven decisions (replace raw heuristics) ──────────────

        # Read more if knowledge is low and we haven't exhausted reads
        if PolicyController.should_read_more(cs):
            if s.intent in ("READ", "SEARCH"):
                read_count = len([st for st in s.completed_steps if st.startswith("read ")])
                if read_count < 6:
                    return "READ"
                return "SEARCH" if s.intent == "SEARCH" else "READ"

        # Conclude if diagnosis confidence is high or task is complete
        if PolicyController.should_conclude(cs) or PolicyController.is_task_complete(cs):
            if s.intent in ("READ", "SEARCH"):
                return "SUMMARIZE"
            if progress >= 0.85:
                return "VERIFY"

        # Fallback: intent-specific heuristics for the action gap
        if s.intent == "READ":
            if "scanned repository" not in steps_str:
                return "READ"
            read_count = len([st for st in s.completed_steps if st.startswith("read ")])
            return "SUMMARIZE" if read_count >= 3 else "READ"

        if s.intent == "SEARCH":
            if "searched:" not in steps_str and "searched: {pattern}" not in steps_str:
                return "SEARCH"
            return "SUMMARIZE"

        if s.intent == "MODIFY":
            edits = steps_str.count("edited ") + steps_str.count("wrote ")
            if edits < 1:
                return "WRITE"
            return "VERIFY"

        if s.intent == "RUN":
            if any(w in steps_str for w in ("server", "process", "pid")):
                if any(w in steps_str for w in ("alive", "code=200")):
                    return "SUMMARIZE"
                return "VERIFY"
            return "RUN"

        if s.intent in ("BUILD", "FIX", "REFACTOR"):
            writes = steps_str.count("wrote ") + steps_str.count("edited ")
            if writes < 1:
                return "WRITE"
            return "VERIFY"

        return "READ"

    # ── Completion nudge (single source — replaces multiple heuristics) ──

    def next_nudge(self, is_question_turn: bool = False) -> Optional[str]:
        """Return a nudge string if the model needs guidance, else None.
        Uses confidence gaps + plan state to decide what to suggest.
        """
        if self.state is None:
            return None
        s = self.state
        if s._force_summarize:
            return None
        if is_question_turn:
            return None

        cs = s.confidence_scores

        # ── Confidence-gap nudges ──────────────────────────────────────────
        if PolicyController.should_read_more(cs):
            if cs.repo_scan_done < 0.5:
                return "[sys] Scan the project structure first to understand what exists."
            if cs.entrypoint_read < 0.5:
                return "[sys] Read the main entrypoint file to understand the codebase structure."
            read_count = len([st for st in s.completed_steps if st.startswith("read ")])
            if read_count < 3:
                return "[sys] Read more relevant files to build understanding."
            # Still need more — suggest reading based on intent
            return "[sys] Keep exploring. Read core files to strengthen your understanding."

        if PolicyController.should_ask_user(cs, s.blockers):
            return "[sys] You're blocked and diagnosis confidence is low. Consider asking the user for clarification."

        # ── Plan-based nudges ──────────────────────────────────────────────
        if s.todos:
            active = None
            for t in s.todos:
                if t.status == "active":
                    active = t
                    break
            if active:
                hint = active.tool_hint or ""
                if hint in ("write_file", "edit_file", "") and s._files_written == 0:
                    return f"[sys] Next step: {active.title}. Write the required file(s)."
                if hint in ("run_install", "run_command", "run_tests") and s._files_written > 0:
                    return f"[sys] Next step: {active.title}. Run the command to verify."
                if hint == "read_file":
                    return f"[sys] Next step: {active.title}. Read the relevant file(s)."

        # ── Fallback nudges ────────────────────────────────────────────────
        if s._files_written > 0 and s._tests_passed:
            return "[sys] Files modified and tests passing. Task complete — emit final summary."
        _cmds = [st for st in s.completed_steps if st.startswith("ran:")]
        if s._files_written > 0 and not _cmds:
            return f"[sys] {s._files_written} file(s) written. Run install+start now."
        if s._files_written == 0:
            intent = s.intent
            if intent in ("BUILD", "FIX", "MODIFY", "REFACTOR"):
                return "[sys] Write files now. Call write_file to implement the changes."
        return None

    # ── LLM update hook ───────────────────────────────────────────────────

    def update_after_llm(self, llm_response: str) -> None:
        pass

    # ── Should-finish check ───────────────────────────────────────────────

    def should_finish(self) -> bool:
        if self.state is None:
            return False
        s = self.state
        # Execution plan all completed/terminal → finish immediately
        if s.todos:
            if all(t.status == "completed" for t in s.todos):
                return True
            if all(t.status in ("completed", "blocked", "failed") for t in s.todos):
                return True
        if s.should_stop:
            return True
        # Confidence-based completion gating
        if PolicyController.is_task_complete(s.confidence_scores):
            return True
        if s.next_action in ("SUMMARIZE", "VERIFY", "DONE"):
            if s.progress >= 0.7:
                return True
        return False

    # ── Single evaluate() — replaces agent_loop text-only completion path ──

    def evaluate(self, *, has_blocked_commands: bool = False, is_question_turn: bool = False) -> LoopDecision:
        """Single authority: should the loop stop, continue, or nudge?

        Replaces agent_loop lines 1282-1361 (text-only completion path).
        Returns a LoopDecision — the caller only branches on done/nudge.
        """
        if self.state is None:
            return LoopDecision(done=False)

        s = self.state

        # 1. Should finish? (scratchpad's own completion detection)
        if self.should_finish():
            # Check for repetitive conclusion statements that should trigger done
            try:
                from agent.session import get_session
                session = get_session()
                msgs = session.messages
                _COMPLETE_PREFIXES = [
                    "the task is already", "task already", "already complete",
                    "the migration is", "migration is already", "already switched",
                    "fully switched", "no further", "already fully", "project has been",
                    "fully migrated", "changes are already", "no additional",
                ]
                resps = []
                for m in reversed(msgs):
                    if m.get("role") == "assistant" and m.get("content"):
                        content = m.get("content", "").strip()[:80]
                        if any(content.startswith(prefix) for prefix in _COMPLETE_PREFIXES):
                            resps.append(content)
                            if len(resps) >= 3:
                                return LoopDecision(done=True)
                        else:
                            break
            except Exception as _e:
                _logger.debug("evaluate LLM response parse: %s", _e)
            return LoopDecision(done=True)

        # 2. Has blocked commands with no writes — check next_action
        if has_blocked_commands and s._files_written == 0:
            if s.next_action in ("SUMMARIZE", "DONE"):
                return LoopDecision(done=True)
            return LoopDecision(
                done=False,
                nudge=f"[scratchpad] Task incomplete. Next action: {s.next_action}. Continue.",
            )

        # 3. Ask scratchpad for a nudge
        nudge = self.next_nudge(is_question_turn=is_question_turn)
        if nudge:
            return LoopDecision(done=False, nudge=nudge)

        # 4. No nudge needed — task is done
        return LoopDecision(done=True)

    # ── Prompt block ──────────────────────────────────────────────────────

    def render_prompt_block(self) -> str:
        if self.state is None:
            return ""
        s = self.state

        # ── Execution plan (shown first — most actionable) ──────────────
        plan_block = ""
        if s.todos:
            _done = sum(1 for t in s.todos if t.status == "completed")
            _total = len(s.todos)
            _active = [t for t in s.todos if t.status in ("active", "pending", "blocked", "failed")]
            if _total <= 1:
                plan_block = ""  # trivial — don't waste tokens on a 1-step plan
            elif _done == _total:
                plan_block = f"EXECUTION PLAN  ({_done}/{_total} — all done)"
            else:
                plan_lines = [f"EXECUTION PLAN  ({_done}/{_total})"]
                for t in _active:
                    if t.status == "active":
                        plan_lines.append(f"  \u2192 {t.title}")
                    elif t.status == "blocked":
                        plan_lines.append(f"  \u2717 {t.title}")
                    elif t.status == "failed":
                        plan_lines.append(f"  \u2717 {t.title} (failed)")
                    else:
                        plan_lines.append(f"  \u25CB {t.title}")
                plan_block = "\n".join(plan_lines)

        # ── Scratchpad state ────────────────────────────────────────────
        blockers = s.blockers or []
        cs = s.confidence_scores
        block = (
            f"[SCRATCHPAD] {s.intent} | "
            f"{round(s.progress * 100, 1)}% | "
            f"K{round(cs.knowledge * 100):.0f} D{round(cs.diagnosis * 100):.0f} C{round(cs.completion * 100):.0f} | "
            f"NEXT: {s.next_action}"
        )
        if blockers:
            block += f" | BLOCKED: {'; '.join(blockers)}"
        if s.should_stop:
            block += " | COMPLETE"
        if s._force_summarize:
            block += " | SUMMARIZE"

        # Combine plan + state (plan first)
        if plan_block:
            block = plan_block + "\n\n" + block
        return block

    # ── Legacy filter_tools (kept for backward compat, delegates to allows_tool) ─

    def filter_tools(self, tool_specs: list) -> list:
        if self.state is None:
            return tool_specs
        allowed = set(self.state.allowed_tools)
        if not allowed:
            return []
        return [s for s in tool_specs if s.get("function", {}).get("name") in allowed]


def _get_domain_names(domain: str, pred) -> list:
    """Get names of tools matching a domain predicate for lookup index."""
    import inspect
    names = []
    if domain == "browser":
        names = list(_BROWSER_ESSENTIAL)
        names.extend(["fill_form", "upload_file", "download_content",
                       "check_browser_errors", "browser_use_task"])
    elif domain == "git":
        names = ["git_op"]
    return names


# ── Global instance ───────────────────────────────────────────────────────

scratch = ScratchpadManager()


def scratchpad_reset() -> None:
    if scratch.state is not None:
        scratch.state.reset()


__all__ = ["ScratchpadManager", "scratchpad_reset", "normalize_command", "normalize_path"]

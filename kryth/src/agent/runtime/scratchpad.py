"""Runtime Scratchpad v2 — Active execution state for single-agent flow.

Purpose: Track goal, progress, findings, blockers, and drive next action.
Eliminates duplicated reads, task drift, over-reasoning loops, and failure
to detect completion.

Design:
- Live execution state (not memory — not persistent across sessions)
- Active controller: filters tools, detects loops, decides should_stop
- Minimal footprint: ~200-400 prompt tokens
- Integrates with agent_loop via 4 hooks
"""

from __future__ import annotations

import os as _os
import re as _re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set


# ── Intent definitions ────────────────────────────────────────────────────

INTENTS = ("CHAT", "READ", "SEARCH", "MODIFY", "RUN", "BUILD")

NEXT_ACTIONS = (
    "READ",      # Explore/understand files
    "SEARCH",    # grep/glob/search patterns
    "WRITE",     # Write or edit files
    "RUN",       # Execute commands (install, build, test)
    "VERIFY",    # Run validation (lint, typecheck, tests)
    "SUMMARIZE", # Generate natural language summary
    "DONE",      # Task is complete
)

_TOOL_WHITELIST: Dict[str, List[str]] = {
    "CHAT":    [],
    "READ":    ["list_files", "read_file", "grep", "search_code", "glob"],
    "SEARCH":  ["grep", "search_code", "glob", "read_file", "list_files"],
    "MODIFY":  ["read_file", "list_files", "grep", "write_file", "edit_file", "multi_edit", "glob", "search_code"],
    "RUN":     ["read_file", "list_files", "run_command", "grep", "glob"],
    "BUILD":   ["read_file", "list_files", "grep", "write_file", "edit_file", "multi_edit", "run_command", "run_tests", "glob", "search_code"],
}


# ── Command/path normalization (Phase 2: semantic dedup) ──────────────────

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
    # Normalize common runner patterns
    for pattern, replacement in _COMMAND_NORMALIZATIONS:
        c = pattern.sub(replacement, c)
    return c.strip()


def normalize_path(path: str) -> str:
    """Resolve ./, ../, and duplicate slashes to canonical form."""
    try:
        return str(Path(path).resolve())
    except Exception:
        p = _os.path.normpath(_os.path.abspath(path))
        return p.lower()


def _known_duplicate(label_a: str, label_b: str) -> bool:
    """True when two step labels describe the same logical action."""
    if label_a == label_b:
        return True
    # Normalized command comparison
    if "ran:" in label_a and "ran:" in label_b:
        cmd_a = normalize_command(label_a.split("ran:")[-1].strip())
        cmd_b = normalize_command(label_b.split("ran:")[-1].strip())
        return cmd_a == cmd_b
    # Normalized path comparison
    for prefix in ("read ", "wrote ", "edited "):
        if label_a.startswith(prefix) and label_b.startswith(prefix):
            path_a = label_a[len(prefix):].strip().strip('"')
            path_b = label_b[len(prefix):].strip().strip('"')
            return normalize_path(path_a) == normalize_path(path_b)
    return False


# ── Scratchpad core ───────────────────────────────────────────────────────

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
    allowed_tools: List[str] = field(default_factory=lambda: list(_TOOL_WHITELIST["READ"]))
    should_stop: bool = False

    # Transient
    _max_progress_seen: float = 0.0
    _loop_count: int = 0           # Repeated same-step counter
    _last_n_actions: deque = field(default_factory=lambda: deque(maxlen=6))
    _tool_call_count: int = 0
    _tool_error_count: int = 0
    _force_summarize: bool = False

    def reset(self) -> None:
        self.__init__()


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
        self._detect_intent(user_input)
        self.state.intent = self.current_intent
        self.state.allowed_tools = list(_TOOL_WHITELIST.get(self.current_intent, []))
        self.state.next_action = self.current_intent if self.current_intent != "CHAT" else "DONE"
        self._recompute_state()

    def _detect_intent(self, user_input: str) -> None:
        words = set(_re.findall(r"[A-Za-z]+", user_input.lower()))
        if words & {"hi", "hello", "hey", "alo"}:
            self.current_intent = "CHAT"
            return
        if words & {"run", "start", "launch", "execute"}:
            self.current_intent = "RUN"
            return
        if words & {"build", "create", "make", "scaffold"}:
            self.current_intent = "BUILD"
            return
        if words & {"modify", "edit", "fix", "change", "update"}:
            self.current_intent = "MODIFY"
            return
        if words & {"search", "find", "locate", "explore"}:
            self.current_intent = "SEARCH"
            return
        if words & {"read", "examine", "inspect", "understand"}:
            self.current_intent = "READ"
            return
        self.current_intent = "READ"

    # ── Tool update hook ──────────────────────────────────────────────────

    def update_after_tool(self, tool_name: str, result: str, args: Optional[dict] = None) -> None:
        if self.state is None:
            return

        step_label = self._format_tool_success(tool_name, result, args)
        if not step_label:
            return

        # Phase 2: semantic duplicate detection
        if self._is_duplicate(step_label):
            self.state._loop_count += 1
        else:
            self.state.completed_steps.append(step_label)
            self.state._loop_count = 0
            # Replace raw result with structured finding
            finding = self._extract_finding(tool_name, result, args)
            if finding:
                self.state.findings.append(finding)

        # Phase 3: loop killer
        self.state._last_n_actions.append(step_label)
        if self._detect_loop():
            self.state._force_summarize = True

        self.state._tool_call_count += 1
        if "[error" in str(result).lower() or "[ERROR" in str(result):
            self.state._tool_error_count += 1

        self._check_blockers(result)
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
            except Exception:
                pass
        if "path" in args_or_result or "Args:" in args_or_result:
            try:
                import json as _json
                extracted = _json.loads(str(args_or_result).split("Args:")[-1].split("|")[0])
                return pattern.format(path=extracted.get("path", ""), command=extracted.get("command", ""), pattern=extracted.get("pattern", ""))
            except Exception:
                pass
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
        # Check last 3 actions for repeats
        recent = list(self.state._last_n_actions)
        if len(recent) >= 3:
            if recent[-1] == recent[-2] == recent[-3]:
                return True
            # "scanned repository" repeated 3+ times
            scan_count = sum(1 for a in recent if a == "scanned repository")
            if scan_count >= 3:
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

        self.state.progress = self._compute_progress()
        self.state._max_progress_seen = max(self.state._max_progress_seen, self.state.progress)
        self.state.confidence = self._compute_confidence()
        self.state.next_action = self.decide_next_action()
        self.state.should_stop = self._compute_should_stop()

    def _compute_progress(self) -> float:
        intent = self.state.intent if self.state else "READ"
        steps_str = str(self.state.completed_steps).lower()

        if intent == "CHAT":
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
        return 0.5

    def _compute_confidence(self) -> float:
        if self.state is None:
            return 0.5
        base = 0.5
        if self.state._tool_call_count == 0:
            return base
        err_rate = self.state._tool_error_count / max(self.state._tool_call_count, 1)
        base -= err_rate * 0.4
        # Success rate bonus (perfect execution → +0.3)
        base += (1.0 - err_rate) * 0.3
        if not self.state.blockers:
            base += 0.1
        if self.state._loop_count > 2:
            base -= 0.3
        if self.state._force_summarize:
            base -= 0.2
        return max(0.0, min(1.0, base))

    def _compute_should_stop(self) -> bool:
        if self.state is None:
            return False
        if self.state.intent == "CHAT":
            return True
        if self.state._force_summarize:
            return True
        if self.state.progress > 0.9 and self.state.confidence > 0.7:
            return True
        if self.state.next_action == "DONE":
            return True
        if self.state.progress >= 1.0 and self.state.confidence >= 0.5:
            return True
        return False

    # ── Next action engine ────────────────────────────────────────────────

    def decide_next_action(self) -> str:
        if self.state is None:
            return "READ"

        if self.state._force_summarize:
            return "SUMMARIZE"

        intent = self.state.intent
        steps = str(self.state.completed_steps).lower()
        blockers = self.state.blockers

        # Blockers → recovery
        if blockers:
            if "missing dependency" in blockers:
                return "RUN"
            if "execution error" in blockers:
                return "VERIFY"
            if "permission denied" in blockers or "missing API key" in blockers:
                return "SUMMARIZE"

        # Completion threshold → terminal
        progress = self.state.progress
        if progress >= 0.95 and intent != "CHAT":
            return "SUMMARIZE" if intent in ("READ", "SEARCH") else "VERIFY"

        if intent == "CHAT":
            return "DONE"

        # Intent-specific transitions
        if intent == "READ":
            if "scanned repository" not in steps:
                return "READ"
            read_count = len([s for s in self.state.completed_steps if s.startswith("read ")])
            if read_count < 3:
                return "READ"
            return "SUMMARIZE"

        if intent == "SEARCH":
            if "searched:" in steps or "searched: {pattern}" in steps:
                return "SUMMARIZE"
            return "SEARCH"

        if intent == "MODIFY":
            edits = steps.count("edited ") + steps.count("wrote ")
            if edits < 1:
                return "WRITE"
            if edits < 2 and "edited" not in steps:
                return "WRITE"
            return "VERIFY"

        if intent == "RUN":
            if "server" in steps or "process" in steps or "pid" in steps:
                if "alive" in steps or "code=200" in steps:
                    return "SUMMARIZE"
                return "VERIFY"
            return "RUN"

        if intent == "BUILD":
            writes = steps.count("wrote ") + steps.count("edited ")
            if writes < 2:
                return "WRITE"
            return "VERIFY"

        return "READ"

    # ── LLM update hook ───────────────────────────────────────────────────

    def update_after_llm(self, llm_response: str) -> None:
        pass

    # ── Should-finish check ───────────────────────────────────────────────

    def should_finish(self) -> bool:
        if self.state is None:
            return False
        if self.state.should_stop:
            return True
        if self.state.progress > 0.9 and self.state.confidence > 0.8:
            return True
        if self.state.next_action in ("SUMMARIZE", "VERIFY", "DONE"):
            if self.state.progress >= 0.7:
                return True
        return False

    # ── Prompt block ──────────────────────────────────────────────────────

    def render_prompt_block(self) -> str:
        if self.state is None:
            return ""
        s = self.state
        goal = s.goal[:80].strip() + ("..." if len(s.goal) > 80 else "")
        steps = s.completed_steps[-6:] or ["(none)"]
        findings = s.findings[-3:] or ["(none)"]
        blockers = s.blockers or ["(none)"]
        tools = s.allowed_tools or ["(all)"]

        block = (
            f"TASK SCRATCHPAD\n"
            f"GOAL:     {goal}\n"
            f"INTENT:   {s.intent}\n"
            f"PROGRESS: {round(s.progress * 100, 1)}%  "
            f"CONFIDENCE: {round(s.confidence * 100, 1)}%\n"
            f"STEPS:    {', '.join(steps)}\n"
            f"FINDINGS: {', '.join(findings)}\n"
            f"BLOCKERS: {', '.join(blockers)}\n"
            f"NEXT:     {s.next_action}"
        )
        if s.should_stop:
            block += "\nSTATUS:   COMPLETE — stopping"
        if s._force_summarize:
            block += "\nSTATUS:   LOOP DETECTED — summarizing"
        return block

    # ── Tool filtering ────────────────────────────────────────────────────

    def filter_tools(self, tool_specs: list) -> list:
        """Return only tools allowed by current intent."""
        if self.state is None:
            return tool_specs
        allowed = set(self.state.allowed_tools)
        if not allowed:
            return []  # CHAT — no tools
        return [s for s in tool_specs if s.get("function", {}).get("name") in allowed]


# ── Global instance ───────────────────────────────────────────────────────

scratch = ScratchpadManager()


def scratchpad_reset() -> None:
    if scratch.state is not None:
        scratch.state.reset()


__all__ = ["ScratchpadManager", "scratchpad_reset", "normalize_command", "normalize_path"]

"""Runtime Scratchpad — execution state for single-agent sequential flow.

Purpose: Track active goal, progress, blockers, and next action to eliminate:
- repeated reads and scans
- task drift (READ → FIX)
- over-reasoning loops
- failure to detect task completion

Design:
- Live execution state (not memory — not persistent across sessions)
- Minimal footprint: ~200-400 prompt tokens
- Integrates with existing agent_loop, MemoryManager, DuplicateDetector
- Single-threaded, synced with agent turn cycle
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


# Allowed intents — correspond 1:1 with permission profiles
INTENTS = ("CHAT", "READ", "SEARCH", "MODIFY", "RUN", "BUILD")

# Allowed next actions
action_types = [
    "READ",          # Explore/understand
    "SEARCH",        # grep/glob/search patterns
    "WRITE",         # Write or edit files
    "RUN",           # Execute commands (install, build, test)
    "VERIFY",        # Run validation (lint, typecheck, tests)
    "SUMMARIZE",     # Generate natural language summary
    "DONE",          # Task is complete
]


@dataclass
class Scratchpad:
    """Current execution state."""
    
    goal: str = ""                  # User's stated objective
    intent: str = "READ"           # Detected intent: READ, MODIFY, RUN, etc.
    completed_steps: List[str] = field(default_factory=list)  # Tools called with outcomes
    current_state: str = ""       # Human-readable status
    blockers: List[str] = field(default_factory=list)  # Obstacles encountered
    next_action: str = ""         # High-level next action
    completion_score: float = 0.0   # 0.0 → 1.0 task completeness

    # Transient state
    _intent_job_done: bool = False     # Intent fulfilled?
    _max_completion_seen: float = 0.0
    
    def reset(self) -> None:
        """Reset state for new user task."""
        self.__init__()


class ScratchpadManager:
    """Top-level scratchpad orchestrator."""
    
    def __init__(self):
        self.state = None
        self.current_intent = "READ"  # Default
        # Tool → step label mapping
        self._tool_map = {
            "list_files": "scanned repository",
            "read_file": "read {path}",
            "write_file": "wrote {path}",
            "edit_file": "edited {path}",
            "multi_edit": "edited multiple files",
            "run_command": "ran: {command}",
            "run_tests": "ran tests",
            "grep": "searched: {pattern}",
            "glob": "searched: {pattern}",
            "search_code": "searched: {pattern}",
        }
    
    def initialize(self, user_input: str) -> None:
        """Initialize scratchpad for new task."""
        self.state = Scratchpad(goal=user_input.strip())
        self._detect_intent(user_input)
        self.state.intent = self.current_intent
        self.state.current_state = "Initialized"
    
    def _detect_intent(self, user_input: str) -> None:
        """Map user input to execution intent (word-boundary aware)."""
        import re as _re
        words = set(_re.findall(r"[A-Za-z]+", user_input.lower()))
        
        # CHAT is implicit — contained greeting, no exec signals
        if words & {"hi", "hello", "hey", "alo"}:
            self.current_intent = "CHAT"
            return
        
        # Intent classifiers (word set intersection — no false substrings)
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
        
        self.current_intent = "READ"  # default
    
    def update_after_tool(self, tool_name: str, result: str, args: Optional[dict] = None) -> None:
        """Update state after a tool call completes."""
        if self.state is None:
            return
        
        # Update completed steps
        step_label = self._format_tool_success(tool_name, result, args)
        if step_label:
            self.state.completed_steps.append(step_label)
            # Trim tail to avoid bloating prompt
            if len(self.state.completed_steps) > 15:
                self.state.completed_steps = self.state.completed_steps[-15:]
        
        # Check blockers
        self._check_blockers(result)
        
        # Re-evaluate completion and next action
        self._recompute_state()
    
    def _format_tool_success(self, tool_name: str, args_or_result: str, args: Optional[dict] = None) -> Optional[str]:
        """Humanize tool call for tracking."""
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
        # Fallback: try to extract from result string
        if "path" in args_or_result or "Args:" in args_or_result:
            try:
                import json
                extracted = json.loads(str(args_or_result).split("Args:")[-1].split("|")[0])
                return pattern.format(path=extracted.get("path", ""), command=extracted.get("command", ""), pattern=extracted.get("pattern", ""))
            except Exception:
                pass
        return pattern
    
    def _check_blockers(self, tool_result: str) -> None:
        """Extract blockers from tool result (word heuristic)."""
        if self.state is None:
            return
        
        blockers = []
        lower_result = str(tool_result).lower()
        
        # Light/signaled blocking heuristics
        if "[error" in lower_result:
            blockers.append("execution error")
        elif "missing dependency" in lower_result:
            blockers.append("missing dependency")
        elif "permission denied" in lower_result:
            blockers.append("permission denied")
        elif "key not set" in lower_result:
            blockers.append("missing API key")
        
        self.state.blockers = list(set(blockers))  # dedup
    
    def _recompute_state(self) -> None:
        """Update dynamic state — completion + next action."""
        if self.state is None:
            return
        
        # Completion scoring (per intent)
        self.state.completion_score = self._compute_completion()
        self.state._max_completion_seen = max(
            self.state._max_completion_seen, self.state.completion_score
        )
        
        # Intent fulfilled?
        if (
            (self.state.intent == "READ" and "read" in str(self.state.completed_steps))
            or (self.state.intent == "RUN" and "ran:" in str(self.state.completed_steps))
            or (self.state.intent == "SEARCH" and "searched:" in str(self.state.completed_steps))
            or (self.state.intent in ("MODIFY", "BUILD") and "wrote " in str(self.state.completed_steps))
        ):
            self.state._intent_job_done = True
        
# decided_next_action
        self.state.next_action = self.decide_next_action()
        
        # Current status
        self.state.current_state = self._format_current_state()
    
    def _compute_completion(self) -> float:
        """Compute task completion %."""
        intent = self.state.intent if self.state else "READ"
        steps = str(self.state.completed_steps).lower()
        
        if intent == "CHAT":
            return 1.0  # Always done immediately
        elif intent == "READ":
            # READ task: typical 6 reads to fully understand small project
            read_steps = steps.count("read ")
            return min(read_steps / 6, 1.0)
        elif intent == "RUN":
            # RUN task: server launched + service verified → 100%
            run_launched = int("server running" in steps or "server is alive" in steps)
            verify_passed = int("code=200" in steps or "status: alive" in steps)
            return run_launched * 0.6 + verify_passed * 0.4
        elif intent == "SEARCH":
            # SEARCH task: 3+ searches → 100%
            return min(steps.count("searched") / 3, 1.0)
        elif intent == "MODIFY":
            # MODIFY task: 2 edits + test/verify → 100%
            edit_steps = steps.count("edited ") + steps.count("wrote ")
            verify_done = int("test passed" in steps or "validation complete" in steps)
            return min(edit_steps / 2, 0.7) + verify_done * 0.3
        elif intent == "BUILD":
            # BUILD task: files written + tests passing → 100%
            files_written = int("wrote " in steps)
            tests_passed = int("-success" in steps or "passed" in steps)
            return files_written * 0.5 + tests_passed * 0.5
        else:
            return 0.5  # safe baseline
    
    def decide_next_action(self) -> str:
        """Deterministic next-action selection."""
        if self.state is None:
            return "READ"
        
        intent = self.state.intent
        steps = str(self.state.completed_steps).lower()
        blockers = self.state.blockers
        
        # Blockers → recovery action
        if blockers:
            if "missing dependency" in blockers:
                return "RUN"  # install
            if "permission denied" in blockers:
                return "SUMMARIZE"  # await fix
            if "execution error" in blockers:
                return "VERIFY"  # debug
            if "missing API key" in blockers:
                return "SUMMARIZE"  # await key
        
        # Completion check (disabled for budget—use should_finish() at turn level)
        completion = self.state.completion_score
        if completion >= 0.95 and intent != "CHAT":
            return "SUMMARIZE" if intent in ("READ", "SEARCH") else "VERIFY"
        
        # Intent transition logic
        if intent == "READ":
            # READ → READ (new files) → SUMMARIZE → DONE
            if "scanned repository" in steps:
                return "READ"  # read
            elif "read " in steps:
                if "app.py" not in steps and "package.json" not in steps:
                    return "READ"  # read deeper
                return "SUMMARIZE"  # synthesize
            return "READ"
        
        elif intent == "SEARCH":
            # SEARCH → SEARCH → SUMMARIZE
            if "searched:" not in steps:
                return "SEARCH"
            elif "fulltext search" not in steps:
                return "SEARCH"
            else:
                return "SUMMARIZE"
        
        elif intent == "MODIFY":
            # MODIFY: WRITE → VERIFY → SUMMARIZE
            if blocks := len(self.state.blockers):
                return "VERIFY"
            if write_steps := (steps.count("wrote ") + steps.count("edited ")):
                if write_steps < 2:
                    return "WRITE"
                else:
                    return "VERIFY"
            return "WRITE"  # first write
        
        elif intent == "RUN":
            # RUN: RUN server → VERIFY → SUMMARIZE
            if "server" in steps:
                if "alive" in steps:
                    return "SUMMARIZE"
                return "VERIFY"
            return "RUN"  # launch server
        
        elif intent == "BUILD":
            # BUILD: WRITE → WRITE → VERIFY
            if blocks := len(self.state.blockers):
                return "VERIFY"
            if write_steps := (steps.count("wrote ") + steps.count("edited ")):
                if write_steps < 3:
                    return "WRITE"
                else:
                    return "VERIFY"
            return "WRITE"  # first write
        
        elif intent == "CHAT":
            return "DONE"
        
        return "READ"  # default
    
    def _format_current_state(self) -> str:
        """Brief state summary."""
        intent = self.state.intent
        actions = self.state.next_action
        completion = round(self.state.completion_score * 100, 1)
        
        if intent == "CHAT":
            return f"Waiting for message (CHAT)"
        return f"{intent} task: {actions.lower()} ({completion}%)"
    
    def update_after_llm(self, llm_response: str) -> None:
        """Update state after LLM turn — no-op for legacy compat."""
        # Future: track language reasoning, refine completion score
        pass
    
    def should_finish(self) -> bool:
        """True when task is complete — drives agent_loop exit."""
        score = self.state.completion_score if self.state else 0.0
        actions = set(self.state.completed_steps[-3:])
        next_action = self.state.next_action
        intent_finished = self.state._intent_job_done
        
        # Absolute termination ✅
        _TERMINAL_ACTIONS = ("SUMMARIZE", "VERIFY", "DONE")
        if next_action in _TERMINAL_ACTIONS and intent_finished:
            return True
        
        # Intent fulfilled + completion threshold
        if self.state.intent == "CHAT":
            return True
        
        # READ intent: after first summary or 4+ high-level scans → done
        read_steps = sum("read " in s or "scanned" in s for s in self.state.completed_steps)
        if self.state.intent == "READ" and read_steps >= 4:
            return True
        
        return False
    
    def render_prompt_block(self) -> str:
        """Compact scratchpad block (target: 150-400 tokens)."""
        if self.state is None:
            return ""
        
        goal = self.state.goal[:80].strip() + ("..." if len(self.state.goal) > 80 else "")
        intent = self.state.intent
        steps = self.state.completed_steps[-6:] or ["(none)"]
        blockers = self.state.blockers or ["(none)"]
        next_action = self.state.next_action
        completion = round(self.state.completion_score * 100, 1)
        
        block = (
            f"TASK SCRATCHPAD\n"
            f"GOAL:     {goal}\n"
            f"INTENT:   {intent}\n"
            f"PROGRESS: {completion}%\n"
            f"STEPS:    {', '.join(steps)}\n"
            f"BLOCKERS: {', '.join(blockers)}\n"
            f"NEXT:     {next_action}"
        )
        return block


# Global instance — internal use only
scratch = ScratchpadManager()


def scratchpad_reset() -> None:
    """Reset scratchpad (called from REPL /clear)."""
    scratch.state.reset()


__all__ = ["ScratchpadManager", "scratchpad_reset"]
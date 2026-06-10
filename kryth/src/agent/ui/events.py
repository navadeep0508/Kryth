"""Event system: the boundary between agent logic and the terminal.

Every action that should be visible to the user — a tool starting, a
diff to preview, a streaming chunk, a permission prompt — is published
as an ``Event`` on the shared bus. The renderer subscribes to the bus
and turns events into Rich output.

Why an event bus instead of direct ``console.print`` calls?

- **Decoupling.** ``agent_loop.py`` doesn't import rich, doesn't know
  what a panel is, doesn't care whether output goes to a TTY, a log
  file, a websocket, or a test harness.
- **Composability.** Multiple subscribers can react to the same event:
  the renderer paints it, the logger records it, a test harness asserts
  on it.
- **Replayability.** Events are plain data; future versions can record
  and replay them.

This module is pure-Python (no rich import). The renderer in
``renderer.py`` is the only consumer that touches Rich.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class EventKind(str, Enum):
    """The closed set of UI event categories.

    Adding a new event type? Add it here AND add a render path in
    ``renderer.py``. Keep the names noun-ish (what happened), not verb-ish
    (what to do).
    """

    # Lifecycle ------------------------------------------------------
    BANNER = "banner"
    TURN_START = "turn.start"
    TURN_END = "turn.end"
    TURN_INTERRUPTED = "turn.interrupted"
    TURN_MAX_TURNS = "turn.max_turns"
    SESSION_RESET = "session.reset"

    # Planning & status ---------------------------------------------
    PLAN = "plan"
    PLAN_PROSE = "plan.prose"
    PLAN_MODE = "plan.mode"
    STATUS = "status"
    AUTO_SKILLS = "skills.auto"

    # LLM stream ----------------------------------------------------
    LLM_WAITING = "llm.waiting"
    LLM_REASONING_START = "llm.reasoning.start"
    LLM_REASONING_CHUNK = "llm.reasoning.chunk"
    LLM_REASONING_END = "llm.reasoning.end"
    LLM_CONTENT_START = "llm.content.start"
    LLM_CONTENT_CHUNK = "llm.content.chunk"
    LLM_CONTENT_END = "llm.content.end"
    LLM_USAGE = "llm.usage"
    LLM_ERROR = "llm.error"
    LLM_HERMES_RECOVERY = "llm.hermes_recovery"
    LLM_DEGENERATE = "llm.degenerate"
    LLM_RETRY = "llm.retry"

    # Tool dispatch -------------------------------------------------
    TOOL_START = "tool.start"
    TOOL_RESULT = "tool.result"
    TOOL_ERROR = "tool.error"
    TOOL_CANCELLED = "tool.cancelled"
    TOOL_COERCED = "tool.coerced"
    TOOL_DENIED = "tool.denied"
    TOOL_HOOK_BLOCKED = "tool.hook_blocked"

    # File / shell side-effects -------------------------------------
    WRITE_PREVIEW = "write.preview"
    DIFF = "diff"
    SHELL_RUN = "shell.run"
    SHELL_END = "shell.end"
    TODOS = "todos"
    RUN_SUMMARY = "run.summary"

    # Subagents -----------------------------------------------------
    SUBAGENT_START = "subagent.start"
    SUBAGENT_END = "subagent.end"

    # Compaction ----------------------------------------------------
    COMPACT_START = "compact.start"
    COMPACT_FALLBACK = "compact.fallback"

    # Generic log ----------------------------------------------------
    LOG = "log"

    # ── Next-Gen UI Events ─────────────────────────────────────────

    # Mission lifecycle (Executive Layer)
    MISSION_START = "mission.start"       # data: goal, estimated_duration
    MISSION_PROGRESS = "mission.progress" # data: percent (0-100), stage
    MISSION_COMPLETE = "mission.complete" # data: summary dict
    MISSION_FAILED = "mission.failed"     # data: reason

    # Agent dashboard (multi-agent mode)
    AGENT_UPDATE = "agent.update"         # data: name, status, task, progress (0-100)

    # Agent lifecycle (dynamic OS)
    AGENT_CREATED    = "agent.created"      # data: agent_id, role, task_count
    AGENT_TASK_START = "agent.task.start"   # data: agent_id, task_id, description
    AGENT_TASK_DONE  = "agent.task.done"    # data: agent_id, task_id, turns_used
    AGENT_FAILED     = "agent.failed"       # data: agent_id, role, reason
    WORK_STOLEN      = "work.stolen"        # data: agent_id, task_id
    QUEUE_STATUS     = "queue.status"       # data: pending, running, done, failed

    # Timeline events
    TIMELINE_EVENT = "timeline.event"     # data: message, kind (info/success/warn/error)

    # Engineering layer actions (abstracted tool names)
    ENGINEERING_ACTION = "engineering.action"   # data: label, status (running/done/failed), detail
    ENGINEERING_SECTION = "engineering.section" # data: title

    # UI layer switching
    LAYER_CHANGE = "layer.change"         # data: layer (executive/engineering/terminal/debug)

    # Batch approval (replaces per-tool prompts)
    APPROVAL_BATCH = "approval.batch"     # data: items list, risk, estimated_time

    # Reflection at task end
    REFLECTION = "reflection.show"        # data: worked, failed, learned, store_prompt

    # Memory display
    MEMORY_DISPLAY = "memory.display"     # data: similar_tasks, best_workflow, success_rate

    # Terminal dashboard (abstracted shell output)
    TERMINAL_SUMMARY = "terminal.summary" # data: command, status, metrics dict, expandable_log

    # DAG visualization
    DAG_UPDATE = "dag.update"             # data: nodes list [{id, label, status, active}]


@dataclass
class Event:
    """A single thing that happened in the agent worth showing.

    ``data`` carries the kind-specific payload. ``ts`` is set at
    construction so logs and replays preserve ordering even if a
    subscriber is slow.
    """

    kind: EventKind
    data: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.monotonic)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


# Subscriber signature. Synchronous; subscribers must not block the bus.
Subscriber = Callable[[Event], None]


class EventBus:
    """Thread-safe synchronous fan-out bus.

    All emitters publish events here; subscribers handle them in
    registration order. Subscribers that raise are logged once and
    skipped on future events to keep the bus healthy.
    """

    def __init__(self) -> None:
        self._subs: list[Subscriber] = []
        self._broken: set[int] = set()
        self._lock = threading.RLock()

    def subscribe(self, fn: Subscriber) -> Callable[[], None]:
        with self._lock:
            self._subs.append(fn)
        sub_id = id(fn)

        def _unsub() -> None:
            with self._lock:
                self._subs[:] = [s for s in self._subs if id(s) != sub_id]
                self._broken.discard(sub_id)
        return _unsub

    def emit(self, kind: EventKind, **data: Any) -> Event:
        evt = Event(kind=kind, data=data)
        with self._lock:
            subs = list(self._subs)
        for s in subs:
            if id(s) in self._broken:
                continue
            try:
                s(evt)
            except Exception:
                # Mark broken so a malfunctioning subscriber can't cripple
                # the whole UI. Print only once via stderr so we don't
                # recurse into ourselves.
                import sys
                self._broken.add(id(s))
                print(
                    f"[ui.events] subscriber {s} raised; disabling",
                    file=sys.stderr,
                )
        return evt


# Module-level default bus. Tests can build their own.
BUS = EventBus()


def emit(kind: EventKind, **data: Any) -> Event:
    """Convenience: publish to the default bus."""
    return BUS.emit(kind, **data)

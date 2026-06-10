"""UIStateManager — central mutable UI state for the Next-Gen renderer.

All event handlers write here. The renderers read from here.
Thread-safe. Never import rich directly — this is pure data.

Layers:
    executive   — mission, progress, team status, result (default)
    engineering — high-level actions and sections
    terminal    — abstracted shell outputs with metrics
    debug       — raw tool calls, stack traces, LLM context
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class UILayer(str, Enum):
    EXECUTIVE = "executive"
    ENGINEERING = "engineering"
    TERMINAL = "terminal"
    DEBUG = "debug"


@dataclass
class MissionState:
    goal: str = ""
    status: str = "idle"         # idle | running | complete | failed
    progress: int = 0            # 0-100
    stage: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    estimated_duration: str = ""
    summary: dict = field(default_factory=dict)
    error: str = ""

    @property
    def elapsed(self) -> float:
        if not self.start_time:
            return 0.0
        end = self.end_time if self.end_time else time.monotonic()
        return end - self.start_time

    @property
    def elapsed_str(self) -> str:
        e = self.elapsed
        m, s = divmod(int(e), 60)
        return f"{m:02d}:{s:02d}"


@dataclass
class AgentStatus:
    name: str
    status: str = "idle"         # idle | running | done | failed
    task: str = ""
    progress: int = 0            # 0-100
    updated_at: float = field(default_factory=time.monotonic)


@dataclass
class TimelineEntry:
    message: str
    kind: str = "info"           # info | success | warn | error
    timestamp: float = field(default_factory=time.time)

    @property
    def time_str(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))


@dataclass
class EngineeringAction:
    label: str
    status: str = "running"      # running | done | failed | pending
    detail: str = ""
    started_at: float = field(default_factory=time.monotonic)


@dataclass
class TerminalResult:
    command: str
    status: str                  # success | failed
    metrics: dict = field(default_factory=dict)
    expandable_log: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class DAGNode:
    id: str
    label: str
    status: str = "pending"      # pending | active | done | failed
    active: bool = False


@dataclass
class ApprovalRequest:
    items: list = field(default_factory=list)
    risk: str = "low"
    estimated_time: str = ""
    resolved: bool = False
    answer: str = ""             # "y" | "n"


@dataclass
class ReflectionState:
    worked: str = ""
    failed: str = ""
    learned: str = ""
    visible: bool = False


@dataclass
class MemoryState:
    similar_tasks: int = 0
    best_workflow: str = ""
    success_rate: int = 0
    visible: bool = False


class UIStateManager:
    """Single source of truth for all UI state.

    The renderer calls snapshot() to read atomically before drawing.
    Event handlers call mutate methods to update state.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()

        # Layer
        self._layer = UILayer.EXECUTIVE

        # Mission
        self._mission = MissionState()

        # Agents
        self._agents: dict[str, AgentStatus] = {}

        # Timeline (last 50)
        self._timeline: list[TimelineEntry] = []

        # Engineering actions (current section)
        self._eng_section: str = ""
        self._eng_actions: list[EngineeringAction] = []

        # Terminal results (last 20)
        self._terminal_results: list[TerminalResult] = []

        # DAG
        self._dag_nodes: list[DAGNode] = []

        # Pending approval
        self._approval: Optional[ApprovalRequest] = None

        # Reflection
        self._reflection = ReflectionState()

        # Memory
        self._memory = MemoryState()

        # Debug: raw tool calls buffer (last 200 entries)
        self._debug_log: list[dict] = []

        # Turn metrics
        self._turn_tool_count: int = 0
        self._turn_errors: int = 0

    # ------------------------------------------------------------------
    # Layer control

    def set_layer(self, layer: str) -> None:
        with self._lock:
            try:
                self._layer = UILayer(layer)
            except ValueError:
                pass

    def get_layer(self) -> UILayer:
        with self._lock:
            return self._layer

    # ------------------------------------------------------------------
    # Mission

    def mission_start(self, goal: str, estimated_duration: str = "") -> None:
        with self._lock:
            self._mission = MissionState(
                goal=goal,
                status="running",
                progress=0,
                start_time=time.monotonic(),
                estimated_duration=estimated_duration,
            )
            self._timeline.append(TimelineEntry(f"Mission started: {goal}", "info"))
            self._trim_timeline()

    def mission_progress(self, percent: int, stage: str = "") -> None:
        with self._lock:
            self._mission.progress = max(0, min(100, percent))
            if stage:
                self._mission.stage = stage

    def mission_complete(self, summary: dict) -> None:
        with self._lock:
            self._mission.status = "complete"
            self._mission.progress = 100
            self._mission.end_time = time.monotonic()
            self._mission.summary = summary
            self._timeline.append(TimelineEntry("Mission completed", "success"))
            self._trim_timeline()

    def mission_failed(self, reason: str) -> None:
        with self._lock:
            self._mission.status = "failed"
            self._mission.end_time = time.monotonic()
            self._mission.error = reason
            self._timeline.append(TimelineEntry(f"Mission failed: {reason}", "error"))
            self._trim_timeline()

    # ------------------------------------------------------------------
    # Agents

    def update_agent(self, name: str, status: str, task: str = "", progress: int = 0) -> None:
        with self._lock:
            if name not in self._agents:
                self._agents[name] = AgentStatus(name=name)
            a = self._agents[name]
            a.status = status
            if task:
                a.task = task
            a.progress = progress
            a.updated_at = time.monotonic()

    def get_agents(self) -> list[AgentStatus]:
        with self._lock:
            return list(self._agents.values())

    # ------------------------------------------------------------------
    # Timeline

    def add_timeline_event(self, message: str, kind: str = "info") -> None:
        with self._lock:
            self._timeline.append(TimelineEntry(message=message, kind=kind))
            self._trim_timeline()

    def get_timeline(self, last_n: int = 20) -> list[TimelineEntry]:
        with self._lock:
            return list(self._timeline[-last_n:])

    def _trim_timeline(self) -> None:
        if len(self._timeline) > 50:
            self._timeline = self._timeline[-50:]

    # ------------------------------------------------------------------
    # Engineering

    def set_eng_section(self, title: str) -> None:
        with self._lock:
            self._eng_section = title
            self._eng_actions = []

    def add_eng_action(self, label: str, status: str = "running", detail: str = "") -> None:
        with self._lock:
            # Update existing if same label
            for a in self._eng_actions:
                if a.label == label:
                    a.status = status
                    a.detail = detail
                    return
            self._eng_actions.append(EngineeringAction(label=label, status=status, detail=detail))

    def finish_eng_action(self, label: str, success: bool = True) -> None:
        with self._lock:
            for a in self._eng_actions:
                if a.label == label:
                    a.status = "done" if success else "failed"
                    return

    def get_eng_state(self) -> tuple[str, list[EngineeringAction]]:
        with self._lock:
            return self._eng_section, list(self._eng_actions)

    # ------------------------------------------------------------------
    # Terminal results

    def add_terminal_result(self, command: str, status: str,
                             metrics: dict, expandable_log: str) -> None:
        with self._lock:
            self._terminal_results.append(TerminalResult(
                command=command, status=status,
                metrics=metrics, expandable_log=expandable_log,
            ))
            if len(self._terminal_results) > 20:
                self._terminal_results = self._terminal_results[-20:]

    def get_terminal_results(self, last_n: int = 10) -> list[TerminalResult]:
        with self._lock:
            return list(self._terminal_results[-last_n:])

    # ------------------------------------------------------------------
    # DAG

    def update_dag(self, nodes: list) -> None:
        with self._lock:
            self._dag_nodes = [
                DAGNode(
                    id=str(n.get("id", "")),
                    label=str(n.get("label", "")),
                    status=str(n.get("status", "pending")),
                    active=bool(n.get("active", False)),
                )
                for n in nodes
            ]

    def get_dag(self) -> list[DAGNode]:
        with self._lock:
            return list(self._dag_nodes)

    # ------------------------------------------------------------------
    # Approval

    def set_approval(self, items: list, risk: str, estimated_time: str) -> None:
        with self._lock:
            self._approval = ApprovalRequest(
                items=list(items), risk=risk, estimated_time=estimated_time
            )

    def get_approval(self) -> Optional[ApprovalRequest]:
        with self._lock:
            return self._approval

    def resolve_approval(self, answer: str) -> None:
        with self._lock:
            if self._approval:
                self._approval.resolved = True
                self._approval.answer = answer

    # ------------------------------------------------------------------
    # Reflection

    def show_reflection(self, worked: str, failed: str, learned: str) -> None:
        with self._lock:
            self._reflection = ReflectionState(
                worked=worked, failed=failed, learned=learned, visible=True
            )

    def get_reflection(self) -> ReflectionState:
        with self._lock:
            return self._reflection

    def clear_reflection(self) -> None:
        with self._lock:
            self._reflection = ReflectionState()

    # ------------------------------------------------------------------
    # Memory

    def set_memory(self, similar_tasks: int, best_workflow: str, success_rate: int) -> None:
        with self._lock:
            self._memory = MemoryState(
                similar_tasks=similar_tasks,
                best_workflow=best_workflow,
                success_rate=success_rate,
                visible=True,
            )

    def get_memory(self) -> MemoryState:
        with self._lock:
            return self._memory

    # ------------------------------------------------------------------
    # Debug log

    def debug_append(self, entry: dict) -> None:
        with self._lock:
            self._debug_log.append(entry)
            if len(self._debug_log) > 200:
                self._debug_log = self._debug_log[-200:]

    def get_debug_log(self, last_n: int = 50) -> list[dict]:
        with self._lock:
            return list(self._debug_log[-last_n:])

    # ------------------------------------------------------------------
    # Turn metrics

    def turn_reset(self) -> None:
        with self._lock:
            self._turn_tool_count = 0
            self._turn_errors = 0

    def inc_tool(self) -> None:
        with self._lock:
            self._turn_tool_count += 1

    def inc_error(self) -> None:
        with self._lock:
            self._turn_errors += 1

    def get_turn_metrics(self) -> tuple[int, int]:
        with self._lock:
            return self._turn_tool_count, self._turn_errors

    # ------------------------------------------------------------------
    # Mission quick-access

    def get_mission(self) -> MissionState:
        with self._lock:
            return self._mission


# Module-level singleton
ui_state = UIStateManager()
